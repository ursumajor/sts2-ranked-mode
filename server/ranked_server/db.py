"""SQLite storage."""

from __future__ import annotations

import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    steam_id    TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS windows (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL CHECK (kind IN ('daily', 'manual')),
    starts_at        INTEGER NOT NULL,
    ends_at          INTEGER NOT NULL,
    submit_deadline  INTEGER NOT NULL,
    seed             TEXT NOT NULL,
    setup_key        TEXT NOT NULL,
    game_version     TEXT,
    character        TEXT,
    created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS windows_time ON windows (starts_at, ends_at);

CREATE TABLE IF NOT EXISTS runs (
    id             TEXT PRIMARY KEY,
    window_id      TEXT NOT NULL REFERENCES windows (id),
    player_id      INTEGER NOT NULL REFERENCES players (id),
    status         TEXT NOT NULL CHECK (status IN ('in_progress', 'finished')),
    started_at     INTEGER NOT NULL,
    finished_at    INTEGER,
    victory        INTEGER,
    abandoned      INTEGER,
    floors         INTEGER,
    badges         INTEGER,
    run_time       INTEGER,
    score          INTEGER,
    ascension      INTEGER,
    character      TEXT,
    valid          INTEGER,
    flags          TEXT,
    game_version   TEXT,
    mod_version    TEXT,
    run_json       TEXT,
    run_file       TEXT,      -- the game's own run history file (<start_time>.run), verbatim
    UNIQUE (window_id, player_id)
);
CREATE INDEX IF NOT EXISTS runs_board ON runs (window_id, status, valid, score);

-- Recurring window schedules, edited from the /admin page.
CREATE TABLE IF NOT EXISTS schedules (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                  TEXT NOT NULL UNIQUE,
    start_time            TEXT NOT NULL,              -- "HH:MM" UTC
    duration_minutes      INTEGER NOT NULL,
    submit_grace_minutes  INTEGER NOT NULL,
    weekdays              TEXT NOT NULL DEFAULT '0123456',  -- Monday = 0
    characters            TEXT,                       -- comma-separated pool; NULL = all
    game_version          TEXT,
    enabled               INTEGER NOT NULL DEFAULT 1,
    created_at            INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


class Database:
    """A single shared connection guarded by a lock. SQLite is plenty for a community leaderboard."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(windows)")}
        if "character" not in cols:  # databases created before windows had an assigned character
            self._conn.execute("ALTER TABLE windows ADD COLUMN character TEXT")
        run_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(runs)")}
        if "run_file" not in run_cols:  # databases created before .run files were uploaded
            self._conn.execute("ALTER TABLE runs ADD COLUMN run_file TEXT")
        if "schedule_id" not in cols:  # databases created before recurring schedules lived in the DB
            self._conn.execute("ALTER TABLE windows ADD COLUMN schedule_id INTEGER")
        self.lock = threading.RLock()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self.lock:
            return self._conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()
