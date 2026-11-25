"""Checks on a submitted run.

The game client is never fully trustworthy, so the server recomputes what it can from the run itself
(victory, floors, run time) and only takes the badge count from the client-reported daily score.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class RejectedRun(Exception):
    """The submission is not a run of this window at all. Nothing is stored."""


# Flags that make a run ineligible for the leaderboard (it is still stored for review).
INVALIDATING_FLAGS = {
    "reloaded",
    "custom_game",
    "debug_command",
    "start_time_mismatch",
    "late",
    "version_mismatch",
    "score_mismatch",
    "run_time_exceeds_wall_clock",
}

START_TIME_TOLERANCE_BEFORE = 300  # run's start_time may be at most 5 min before /runs/start
START_TIME_TOLERANCE_AFTER = 900  # ...and at most 15 min after (loading, character screens)


def encode_daily_score(victory: bool, floors: int, badges: int, run_time: int) -> int:
    """Mirror of ScoreUtility.CalculateDailyScore in sts2.dll (v0.113.1)."""
    floors = max(0, min(floors, 99))
    badges = max(0, min(badges, 99))
    run_time = max(0, min(run_time, 9999))
    return (2 if victory else 1) * 100_000_000 + floors * 1_000_000 + badges * 10_000 + (9999 - run_time)


def decode_daily_score(score: int) -> dict:
    """Mirror of ScoreUtility.DecodeDailyScore."""
    victory = score // 100_000_000
    floors = score // 1_000_000 % 100
    badges = score // 10_000 % 100
    run_time = 9999 - score % 10_000
    return {
        "valid": 1 <= victory <= 3 and 0 <= run_time <= 9999,
        "victory": victory == 2,
        "floors": floors,
        "badges": badges,
        "run_time": run_time,
    }


@dataclass
class CheckedRun:
    victory: bool
    abandoned: bool
    floors: int
    badges: int
    run_time: int
    score: int
    ascension: int
    character: str | None
    flags: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not (set(self.flags) & INVALIDATING_FLAGS)


def check_run(
    *,
    run: dict,
    victory: bool,
    abandoned: bool,
    client_score: int,
    game_version: str,
    window: dict,
    started_at: int,
    received_at: int,
) -> CheckedRun:
    seed = (run.get("rng") or {}).get("seed")
    if not seed or seed != window["seed"]:
        raise RejectedRun("run seed does not match this window")
    players = run.get("players") or []
    if len(players) != 1:
        raise RejectedRun("ranked runs must be singleplayer")
    if run.get("game_mode") != "dread":
        raise RejectedRun("ranked runs must use the Dread game mode")
    if window.get("character") and players[0].get("character_id") != window["character"]:
        raise RejectedRun(f"this window must be played as {window['character']}")

    flags: list[str] = []
    history = run.get("map_point_history") or []
    floors = sum(len(act) for act in history)
    run_time = int((run.get("win_time") if victory else run.get("run_time")) or 0)

    if int(run.get("num_reloads") or 0) > 0:
        flags.append("reloaded")
    if run.get("is_custom_game"):
        flags.append("custom_game")
    if (run.get("extra_fields") or {}).get("used_debug_command"):
        flags.append("debug_command")

    start_time = int(run.get("start_time") or 0)
    if not (started_at - START_TIME_TOLERANCE_BEFORE <= start_time <= started_at + START_TIME_TOLERANCE_AFTER):
        flags.append("start_time_mismatch")
    if received_at > window["submit_deadline"]:
        flags.append("late")
    if window.get("game_version") and game_version != window["game_version"]:
        flags.append("version_mismatch")
    if int(run.get("run_time") or 0) > (received_at - started_at) + 60:
        flags.append("run_time_exceeds_wall_clock")

    decoded = decode_daily_score(client_score)
    badges = decoded["badges"]
    if (
        not decoded["valid"]
        or decoded["victory"] != victory
        or decoded["floors"] != min(floors, 99)
        or decoded["run_time"] != min(max(run_time, 0), 9999)
    ):
        flags.append("score_mismatch")
        badges = 0

    character = players[0].get("character_id")
    return CheckedRun(
        victory=victory,
        abandoned=abandoned,
        floors=floors,
        badges=badges,
        run_time=run_time,
        score=encode_daily_score(victory, floors, badges, run_time),
        ascension=int(run.get("ascension") or 0),
        character=character,
        flags=flags,
    )
