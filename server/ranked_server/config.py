"""Server configuration, read from environment variables (and server/.env, if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent  # the server/ folder


def load_env_file(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from server/.env into os.environ.

    Real environment variables win over the file. Blank lines and # comments are ignored, and values may be
    wrapped in quotes. This makes the server start the same way on Windows and Linux, without shell tricks.
    """
    path = path or SERVER_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _resolve_db_path(raw: str) -> str:
    """Relative DB paths are relative to server/, not to wherever the process was started from."""
    if raw == ":memory:" or Path(raw).is_absolute():
        return raw
    return str(SERVER_DIR / raw)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


DEFAULT_CHARACTERS = (
    "CHARACTER.IRONCLAD",
    "CHARACTER.SILENT",
    "CHARACTER.REGENT",
    "CHARACTER.NECROBINDER",
    "CHARACTER.DEFECT",
    "CHARACTER.FOOL",
)


@dataclass(frozen=True)
class Settings:
    db_path: str
    # Required for /api/v1/admin/* routes. Admin routes are disabled when empty.
    admin_key: str
    # Recurring daily window start, "HH:MM" in UTC. Only used once, to create the first recurring schedule
    # ("daily") in the DB. After that, schedules are edited at /admin. Empty string = no initial schedule.
    daily_start: str
    daily_duration_minutes: int
    # How long after a window closes a run that was started inside it may still be submitted.
    submit_grace_minutes: int
    # If set, auto-created daily windows require this exact game version (e.g. "v0.113.1").
    game_version: str
    # Characters a window can be assigned at random (STS2 model ids).
    characters: tuple[str, ...] = DEFAULT_CHARACTERS
    # Mark the /admin session cookie Secure (HTTPS only). Turn off only for local http testing.
    cookie_secure: bool = True

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            db_path=_resolve_db_path(os.environ.get("RANKED_DB_PATH", "ranked.db")),
            admin_key=os.environ.get("RANKED_ADMIN_KEY", ""),
            daily_start=os.environ.get("RANKED_DAILY_START", "18:00"),
            daily_duration_minutes=_int_env("RANKED_DAILY_DURATION_MINUTES", 240),
            submit_grace_minutes=_int_env("RANKED_SUBMIT_GRACE_MINUTES", 180),
            game_version=os.environ.get("RANKED_GAME_VERSION", ""),
            cookie_secure=os.environ.get("RANKED_COOKIE_SECURE", "1") not in ("0", "false", "no"),
            characters=tuple(
                c.strip() for c in os.environ.get("RANKED_CHARACTERS", ",".join(DEFAULT_CHARACTERS)).split(",") if c.strip()
            ),
        )
