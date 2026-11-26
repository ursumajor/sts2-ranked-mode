"""STS2 Ranked Mode server.

Run with:  python -m ranked_server            (reads server/.env; see --help)
      or:  uvicorn --factory ranked_server.app:create_app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from urllib.parse import quote
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import windows as win
from .admin import mount_admin
from .pages import VIEWER_HTML, render_home, render_not_found, render_window, run_file_response
from .viewer import build_viewer
from .config import Settings, load_env_file
from .db import Database
from .validation import RejectedRun, check_run, check_run_file

API = "/api/v1"


# ---------- request models ----------


class RegisterRequest(BaseModel):
    steam_id: str = Field(pattern=r"^\d{1,20}$")
    name: str = Field(min_length=1, max_length=64)


class StartRequest(BaseModel):
    window_id: str
    game_version: str = Field(max_length=64)
    mod_version: str = Field(max_length=32)


class FinishRequest(BaseModel):
    victory: bool
    abandoned: bool
    score: int
    game_version: str = Field(max_length=64)
    game_commit: str | None = Field(default=None, max_length=64)
    mod_version: str = Field(max_length=32)
    run: dict[str, Any]
    # The game's run history file (<start_time>.run) as written to disk. Optional for older mods.
    run_file: str | None = Field(default=None, max_length=2_000_000)
    # Per-act maps and visited coordinates captured by the mod (0.4.0+): {"acts": {"0": {"map": ..., "visited": [...]}}}
    map_log: dict[str, Any] | None = None


class WindowMapsRequest(BaseModel):
    # {"0": {"act_id": "ACT.UNDERDOCKS", "map": <saved_map>}, ...} as produced by tools/MapGen
    acts: dict[str, dict[str, Any]]


class WindowCreateRequest(BaseModel):
    starts_at: int
    ends_at: int
    game_version: str | None = None
    seed: str | None = Field(default=None, pattern=r"^[0-9A-HJ-NP-Z]{4,32}$")
    character: str | None = Field(default=None, pattern=r"^CHARACTER\.[A-Z_]+$")
    submit_grace_minutes: int | None = Field(default=None, ge=0)
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


# ---------- app factory ----------


def create_app(settings: Settings | None = None, clock: Callable[[], int] = win.now) -> FastAPI:
    if settings is None:
        load_env_file()
        settings = Settings.from_env()
    db = Database(settings.db_path)
    win.fill_missing_characters(db, settings)
    win.seed_schedules_from_env(db, settings)
    app = FastAPI(title="STS2 Ranked Mode", version="0.1.0")
    app.state.settings = settings
    app.state.db = db
    app.state.clock = clock

    def t() -> int:
        return app.state.clock()

    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def optional_player(authorization: str | None = Header(default=None)) -> dict | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        row = db.one("SELECT * FROM players WHERE token_hash = ?", (hash_token(authorization[7:].strip()),))
        return dict(row) if row else None

    def require_player(player: dict | None = Depends(optional_player)) -> dict:
        if player is None:
            raise HTTPException(401, "missing or invalid player token")
        return player

    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if not settings.admin_key:
            raise HTTPException(403, "admin API disabled (set RANKED_ADMIN_KEY)")
        if not x_admin_key or not secrets.compare_digest(x_admin_key, settings.admin_key):
            raise HTTPException(403, "bad admin key")

    def run_summary(row) -> dict | None:
        if row is None:
            return None
        return {"id": row["id"], "status": row["status"], "started_at": row["started_at"]}

    # ---------- public / player API ----------

    @app.get(API + "/status")
    def status(player: dict | None = Depends(optional_player)):
        now = t()
        win.ensure_daily_windows(db, settings, now)
        current = win.current_window(db, now)
        upcoming = win.next_window(db, now)
        out: dict[str, Any] = {
            "server_time": now,
            "current_window": win.public_window(current, now, include_setup_key=True) if current else None,
            "next_window": win.public_window(upcoming, now, include_setup_key=False) if upcoming else None,
        }
        if player and current:
            row = db.one(
                "SELECT * FROM runs WHERE window_id = ? AND player_id = ?", (current["id"], player["id"])
            )
            out["current_window"]["your_run"] = run_summary(row)
        return out

    @app.post(API + "/register", status_code=201)
    def register(body: RegisterRequest):
        token = secrets.token_urlsafe(32)
        with db.lock:
            if db.one("SELECT 1 FROM players WHERE steam_id = ?", (body.steam_id,)):
                raise HTTPException(409, "this Steam ID is already registered")
            cur = db.execute(
                "INSERT INTO players (steam_id, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
                (body.steam_id, body.name.strip(), hash_token(token), t()),
            )
        return {"player_id": cur.lastrowid, "token": token}

    @app.post(API + "/runs/start", status_code=201)
    def start_run(body: StartRequest, player: dict = Depends(require_player)):
        """Consumes the player's single attempt for the window and reveals the secret seed."""
        now = t()
        win.ensure_daily_windows(db, settings, now)
        with db.lock:
            window = win.current_window(db, now)
            if window is None or window["id"] != body.window_id:
                raise HTTPException(403, "that ranked window is not open")
            if window["game_version"] and body.game_version != window["game_version"]:
                raise HTTPException(
                    409, f"this window requires game version {window['game_version']} (you have {body.game_version})"
                )
            existing = db.one(
                "SELECT * FROM runs WHERE window_id = ? AND player_id = ?", (window["id"], player["id"])
            )
            if existing:
                raise HTTPException(409, "you already used your attempt for this window")
            run_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO runs (id, window_id, player_id, status, started_at, game_version, mod_version)"
                " VALUES (?, ?, ?, 'in_progress', ?, ?, ?)",
                (run_id, window["id"], player["id"], now, body.game_version, body.mod_version),
            )
        return {
            "run_id": run_id,
            "seed": window["seed"],
            "window": win.public_window(window, now, include_setup_key=True),
        }

    @app.post(API + "/runs/{run_id}/finish")
    def finish_run(run_id: str, body: FinishRequest, player: dict = Depends(require_player)):
        now = t()
        with db.lock:
            row = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
            if row is None or row["player_id"] != player["id"]:
                raise HTTPException(404, "unknown run")
            if row["status"] == "finished":
                raise HTTPException(409, "run already submitted")
            window = dict(db.one("SELECT * FROM windows WHERE id = ?", (row["window_id"],)))
            try:
                checked = check_run(
                    run=body.run,
                    victory=body.victory,
                    abandoned=body.abandoned,
                    client_score=body.score,
                    game_version=body.game_version,
                    window=window,
                    started_at=row["started_at"],
                    received_at=now,
                )
            except RejectedRun as e:
                raise HTTPException(422, str(e))
            checked.flags.extend(check_run_file(body.run_file, body.run, window))
            db.execute(
                "UPDATE runs SET status = 'finished', finished_at = ?, victory = ?, abandoned = ?, floors = ?,"
                " badges = ?, run_time = ?, score = ?, ascension = ?, character = ?, valid = ?, flags = ?,"
                " game_version = ?, mod_version = ?, run_json = ?, run_file = ?, map_log = ? WHERE id = ?",
                (
                    now,
                    int(checked.victory),
                    int(checked.abandoned),
                    checked.floors,
                    checked.badges,
                    checked.run_time,
                    checked.score,
                    checked.ascension,
                    checked.character,
                    int(checked.valid),
                    json.dumps(checked.flags),
                    body.game_version,
                    body.mod_version,
                    json.dumps(body.run, separators=(",", ":")),
                    body.run_file,
                    json.dumps(body.map_log, separators=(",", ":")) if body.map_log else None,
                    run_id,
                ),
            )
            rank = None
            if checked.valid:
                better = db.one(
                    "SELECT COUNT(*) AS n FROM runs WHERE window_id = ? AND status = 'finished' AND valid = 1"
                    " AND (score > ? OR (score = ? AND finished_at < ?))",
                    (window["id"], checked.score, checked.score, now),
                )
                rank = better["n"] + 1
        return {"accepted": True, "valid": checked.valid, "flags": checked.flags, "score": checked.score, "rank": rank}

    def leaderboard_rows(window_id: str) -> list[dict]:
        rows = db.all(
            "SELECT r.*, r.run_file IS NOT NULL AS has_run_file, p.name AS player_name"
            " FROM runs r JOIN players p ON p.id = r.player_id"
            " WHERE r.window_id = ? AND r.status = 'finished' AND r.valid = 1"
            " ORDER BY r.score DESC, r.finished_at ASC",
            (window_id,),
        )
        return [
            {
                "rank": i + 1,
                "player": r["player_name"],
                "score": r["score"],
                "victory": bool(r["victory"]),
                "abandoned": bool(r["abandoned"]),
                "floors": r["floors"],
                "badges": r["badges"],
                "run_time": r["run_time"],
                "character": r["character"],
                "ascension": r["ascension"],
                "run_id": r["id"],
                "has_run_file": bool(r["has_run_file"]),
            }
            for i, r in enumerate(rows)
        ]

    def resolve_window(window_id: str | None) -> dict:
        now = t()
        win.ensure_daily_windows(db, settings, now)
        if window_id:
            row = db.one("SELECT * FROM windows WHERE id = ?", (window_id,))
        else:
            row = db.one(
                "SELECT * FROM windows WHERE starts_at <= ? ORDER BY starts_at DESC LIMIT 1", (now,)
            )
        if row is None:
            raise HTTPException(404, "no such window")
        return dict(row)

    @app.get(API + "/leaderboard")
    def leaderboard(window_id: str | None = Query(default=None)):
        window = resolve_window(window_id)
        now = t()
        counts = db.one(
            "SELECT SUM(status = 'in_progress') AS in_progress, SUM(status = 'finished' AND valid = 0) AS flagged"
            " FROM runs WHERE window_id = ?",
            (window["id"],),
        )
        return {
            "window": win.public_window(window, now, include_setup_key=False),
            "entries": leaderboard_rows(window["id"]),
            "in_progress": counts["in_progress"] or 0,
            "flagged": counts["flagged"] or 0,
        }

    @app.get(API + "/windows")
    def list_windows(limit: int = Query(default=30, le=200)):
        now = t()
        win.ensure_daily_windows(db, settings, now)
        rows = db.all("SELECT * FROM windows WHERE starts_at <= ? ORDER BY starts_at DESC LIMIT ?", (now, limit))
        return [win.public_window(dict(r), now, include_setup_key=False) for r in rows]

    # ---------- admin API ----------

    @app.post(API + "/admin/windows", status_code=201, dependencies=[Depends(require_admin)])
    def admin_create_window(body: WindowCreateRequest):
        if body.ends_at <= body.starts_at:
            raise HTTPException(422, "ends_at must be after starts_at")
        win.ensure_daily_windows(db, settings, t())
        with db.lock:
            clash = win.overlapping(db, body.starts_at, body.ends_at)
            if clash:
                raise HTTPException(409, f"overlaps window {clash[0]['id']}")
            window = win.create_window(
                db,
                settings,
                window_id=body.id or f"manual-{uuid.uuid4().hex[:8]}",
                kind="manual",
                starts_at=body.starts_at,
                ends_at=body.ends_at,
                game_version=body.game_version,
                seed=body.seed,
                character=body.character,
                submit_grace_minutes=body.submit_grace_minutes,
            )
        return window

    @app.get(API + "/admin/windows", dependencies=[Depends(require_admin)])
    def admin_list_windows():
        return [dict(r) for r in db.all("SELECT * FROM windows ORDER BY starts_at DESC LIMIT 200")]

    @app.delete(API + "/admin/windows/{window_id}", dependencies=[Depends(require_admin)])
    def admin_delete_window(window_id: str):
        with db.lock:
            if db.one("SELECT 1 FROM runs WHERE window_id = ?", (window_id,)):
                raise HTTPException(409, "window already has runs")
            db.execute("DELETE FROM windows WHERE id = ?", (window_id,))
        return {"deleted": window_id}

    @app.get(API + "/admin/runs", dependencies=[Depends(require_admin)])
    def admin_runs(window_id: str, flagged_only: bool = False):
        sql = (
            "SELECT r.id, r.status, r.started_at, r.finished_at, r.score, r.valid, r.flags, r.floors, r.victory,"
            " r.abandoned, r.character, r.ascension, r.game_version, r.mod_version, p.name, p.steam_id"
            " FROM runs r JOIN players p ON p.id = r.player_id WHERE r.window_id = ?"
        )
        if flagged_only:
            sql += " AND r.valid = 0"
        return [dict(r) for r in db.all(sql + " ORDER BY r.started_at", (window_id,))]

    @app.get(API + "/admin/runs/{run_id}/json", dependencies=[Depends(require_admin)])
    def admin_run_json(run_id: str):
        row = db.one("SELECT run_json FROM runs WHERE id = ?", (run_id,))
        if row is None or row["run_json"] is None:
            raise HTTPException(404, "no run data")
        return json.loads(row["run_json"])

    mount_admin(app, db, settings, t)

    # ---------- HTML leaderboard ----------

    @app.get("/", response_class=HTMLResponse)
    def index(window_id: str | None = Query(default=None)):
        if window_id:  # old links: /?window_id=X
            return RedirectResponse(f"/windows/{quote(window_id, safe='')}", status_code=301)
        now = t()
        win.ensure_recurring_windows(db, settings, now)
        rows = db.all(
            "SELECT w.*,"
            " (SELECT COUNT(*) FROM runs r WHERE r.window_id = w.id) AS players,"
            " (SELECT COUNT(*) FROM runs r WHERE r.window_id = w.id AND r.status = 'finished' AND r.valid = 1) AS finished,"
            " l.name AS leader_name, l.victory AS leader_victory, l.floors AS leader_floors"
            " FROM windows w LEFT JOIN ("
            "   SELECT r.window_id, p.name, r.victory, r.floors,"
            "          ROW_NUMBER() OVER (PARTITION BY r.window_id ORDER BY r.score DESC, r.finished_at ASC) AS rn"
            "   FROM runs r JOIN players p ON p.id = r.player_id WHERE r.status = 'finished' AND r.valid = 1"
            " ) l ON l.window_id = w.id AND l.rn = 1"
            " WHERE w.starts_at > ? OR w.ends_at > ? OR w.id IN ("
            "   SELECT id FROM windows WHERE ends_at <= ? ORDER BY starts_at DESC LIMIT 30)"
            " ORDER BY w.starts_at",
            (now, now, now),
        )
        return render_home([dict(r) for r in rows], now)

    def viewer_data(run_id: str, *, public: bool) -> dict | None:
        row = db.one(
            "SELECT r.*, p.name AS player_name, w.submit_deadline, w.maps AS window_maps, w.seed AS window_seed"
            " FROM runs r JOIN players p ON p.id = r.player_id JOIN windows w ON w.id = r.window_id WHERE r.id = ?",
            (run_id,),
        )
        if row is None or row["run_json"] is None:
            return None
        if public and (not row["valid"] or t() < row["submit_deadline"]):
            return None
        meta = {
            "player": row["player_name"],
            "character": row["character"],
            "victory": bool(row["victory"]),
            "abandoned": bool(row["abandoned"]),
            "floors": row["floors"],
            "run_time": row["run_time"],
            "ascension": row["ascension"],
            "window_id": row["window_id"],
            "seed": row["window_seed"],
        }
        return build_viewer(
            json.loads(row["run_json"]),
            json.loads(row["map_log"]) if row["map_log"] else None,
            json.loads(row["window_maps"]) if row["window_maps"] else None,
            meta,
        )

    @app.get("/runs/{run_id}/viewer.json")
    def public_viewer_json(run_id: str):
        data = viewer_data(run_id, public=True)
        if data is None:
            raise HTTPException(404, "run not viewable yet")
        return data

    @app.get("/runs/{run_id}/view", response_class=HTMLResponse)
    def public_viewer_page(run_id: str):
        if viewer_data(run_id, public=True) is None:
            return HTMLResponse(render_not_found(), 404)
        return HTMLResponse(VIEWER_HTML)

    app.state.viewer_data = viewer_data

    @app.put(API + "/admin/windows/{window_id}/maps", dependencies=[Depends(require_admin)])
    def admin_put_window_maps(window_id: str, body: WindowMapsRequest):
        with db.lock:
            if db.one("SELECT 1 FROM windows WHERE id = ?", (window_id,)) is None:
                raise HTTPException(404, "no such window")
            db.execute("UPDATE windows SET maps = ? WHERE id = ?", (json.dumps(body.acts, separators=(",", ":")), window_id))
        return {"window_id": window_id, "acts": sorted(body.acts)}

    @app.get(API + "/admin/backfill", dependencies=[Depends(require_admin)])
    def admin_backfill_candidates():
        """Windows whose runs would benefit from seed-rebuilt maps: finished runs without a full map_log."""
        rows = db.all(
            "SELECT w.id AS window_id, MIN(r.id) AS run_id FROM windows w JOIN runs r ON r.window_id = w.id"
            " WHERE r.status = 'finished' AND r.run_json IS NOT NULL AND r.map_log IS NULL AND w.maps IS NULL"
            " GROUP BY w.id ORDER BY w.starts_at"
        )
        return [dict(r) for r in rows]

    @app.get("/runs/{run_id}.run")
    def public_run_file(run_id: str):
        """A run's .run file, public once its window's submit deadline has passed (it contains the seed)."""
        row = db.one(
            "SELECT r.run_file, r.valid, r.window_id, p.name, w.submit_deadline FROM runs r"
            " JOIN players p ON p.id = r.player_id JOIN windows w ON w.id = r.window_id WHERE r.id = ?",
            (run_id,),
        )
        if row is None or row["run_file"] is None or not row["valid"] or t() < row["submit_deadline"]:
            raise HTTPException(404, "no run file")
        return run_file_response(row["run_file"], row["window_id"], row["name"])

    @app.get("/windows/{window_id}", response_class=HTMLResponse)
    def window_page(window_id: str):
        try:
            board = leaderboard(window_id)
        except HTTPException:
            return HTMLResponse(render_not_found(), 404)
        return render_window(board, t())

    return app


