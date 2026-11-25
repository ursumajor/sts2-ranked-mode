"""Ranked time windows: recurring daily windows plus admin-scheduled ones."""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

from .config import Settings
from .db import Database

# Same alphabet the game uses for seeds (SeedHelper): no O or I, so canonicalisation is a no-op.
SEED_ALPHABET = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
SEED_LENGTH = 12


def random_seed() -> str:
    return "".join(secrets.choice(SEED_ALPHABET) for _ in range(SEED_LENGTH))


def now() -> int:
    return int(time.time())


def overlapping(db: Database, starts_at: int, ends_at: int, exclude_id: str | None = None) -> list:
    return db.all(
        "SELECT * FROM windows WHERE starts_at < ? AND ends_at > ? AND id IS NOT ?",
        (ends_at, starts_at, exclude_id),
    )


def create_window(
    db: Database,
    settings: Settings,
    *,
    window_id: str,
    kind: str,
    starts_at: int,
    ends_at: int,
    game_version: str | None,
    seed: str | None = None,
    character: str | None = None,
    submit_grace_minutes: int | None = None,
) -> dict:
    grace = settings.submit_grace_minutes if submit_grace_minutes is None else submit_grace_minutes
    db.execute(
        "INSERT INTO windows (id, kind, starts_at, ends_at, submit_deadline, seed, setup_key, game_version, character,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            window_id,
            kind,
            starts_at,
            ends_at,
            ends_at + grace * 60,
            seed or random_seed(),
            random_seed(),
            game_version or None,
            character or secrets.choice(settings.characters),
            now(),
        ),
    )
    return dict(db.one("SELECT * FROM windows WHERE id = ?", (window_id,)))


def fill_missing_characters(db: Database, settings: Settings) -> None:
    for row in db.all("SELECT id FROM windows WHERE character IS NULL"):
        db.execute("UPDATE windows SET character = ? WHERE id = ?", (secrets.choice(settings.characters), row["id"]))


def ensure_daily_windows(db: Database, settings: Settings, at: int | None = None) -> None:
    """Lazily create the recurring daily windows for yesterday, today and tomorrow (UTC).

    Seeds are generated at creation time and stay secret until a player starts a run in the window.
    A daily window is skipped if it would overlap an existing (e.g. manual) window.
    """
    if not settings.daily_start:
        return
    hour, minute = (int(p) for p in settings.daily_start.split(":"))
    at = now() if at is None else at
    today = datetime.fromtimestamp(at, tz=timezone.utc).date()
    with db.lock:
        for offset in (-1, 0, 1):
            day = today + timedelta(days=offset)
            window_id = f"daily-{day.isoformat()}"
            if db.one("SELECT 1 FROM windows WHERE id = ?", (window_id,)):
                continue
            start = int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc).timestamp())
            end = start + settings.daily_duration_minutes * 60
            if overlapping(db, start, end):
                continue
            create_window(
                db,
                settings,
                window_id=window_id,
                kind="daily",
                starts_at=start,
                ends_at=end,
                game_version=settings.game_version,
            )


def current_window(db: Database, at: int) -> dict | None:
    row = db.one(
        "SELECT * FROM windows WHERE starts_at <= ? AND ends_at > ? ORDER BY starts_at LIMIT 1", (at, at)
    )
    return dict(row) if row else None


def next_window(db: Database, at: int) -> dict | None:
    row = db.one("SELECT * FROM windows WHERE starts_at > ? ORDER BY starts_at LIMIT 1", (at,))
    return dict(row) if row else None


def public_window(window: dict, at: int, *, include_setup_key: bool) -> dict:
    """What clients may see about a window. The run seed is only revealed after the submit deadline."""
    out = {
        "id": window["id"],
        "kind": window["kind"],
        "starts_at": window["starts_at"],
        "ends_at": window["ends_at"],
        "submit_deadline": window["submit_deadline"],
        "game_version": window["game_version"],
        "character": window["character"],
        "game_mode": "dread",
    }
    if include_setup_key:
        out["setup_key"] = window["setup_key"]
    if at >= window["submit_deadline"]:
        out["seed"] = window["seed"]
    return out
