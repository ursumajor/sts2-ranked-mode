from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ranked_server.app import create_app
from ranked_server.config import Settings
from ranked_server.validation import decode_daily_score, encode_daily_score

ADMIN = {"X-Admin-Key": "secret"}
# 2026-11-25 19:00 UTC: inside the default 18:00-22:00 daily window.
T_OPEN = int(datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc).timestamp())
T_CLOSED = int(datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc).timestamp())


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock(T_OPEN)


@pytest.fixture
def client(clock):
    settings = Settings(
        db_path=":memory:",
        admin_key="secret",
        daily_start="18:00",
        daily_duration_minutes=240,
        submit_grace_minutes=180,
        game_version="v0.113.1",
        characters=("CHARACTER.IRONCLAD",),
    )
    return TestClient(create_app(settings, clock=clock))


def register(client, steam_id="76561190000000001", name="Ironclad Main"):
    r = client.post("/api/v1/register", json={"steam_id": steam_id, "name": name})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def make_run(seed, *, floors=(3, 2), start_time=T_OPEN, run_time=600, reloads=0, victory=False, character="CHARACTER.IRONCLAD"):
    history = [[{"map_point_type": "monster", "rooms": [{"room_type": "monster"}], "player_stats": []}] * n for n in floors]
    return {
        "rng": {"seed": seed, "rngs": {}},
        "players": [{"character_id": character, "net_id": 1}],
        "game_mode": "dread",
        "ascension": 4,
        "map_point_history": history,
        "start_time": start_time,
        "run_time": run_time,
        "win_time": run_time if victory else 0,
        "num_reloads": reloads,
        "is_custom_game": False,
        "extra_fields": {},
    }


def start(client, auth):
    status = client.get("/api/v1/status", headers=auth).json()
    window = status["current_window"]
    r = client.post(
        "/api/v1/runs/start",
        headers=auth,
        json={"window_id": window["id"], "game_version": "v0.113.1", "mod_version": "0.1.0"},
    )
    return r


def finish(client, auth, run_id, run, *, victory=False, abandoned=False, badges=3):
    floors = sum(len(a) for a in run["map_point_history"])
    rt = run["win_time"] if victory else run["run_time"]
    return client.post(
        f"/api/v1/runs/{run_id}/finish",
        headers=auth,
        json={
            "victory": victory,
            "abandoned": abandoned,
            "score": encode_daily_score(victory, floors, badges, rt),
            "game_version": "v0.113.1",
            "mod_version": "0.1.0",
            "run": run,
        },
    )


def test_score_roundtrip():
    s = encode_daily_score(True, 51, 7, 3120)
    assert decode_daily_score(s) == {"valid": True, "victory": True, "floors": 51, "badges": 7, "run_time": 3120}


def test_status_hides_seed_during_window(client):
    s = client.get("/api/v1/status").json()
    w = s["current_window"]
    assert w["id"] == "daily-2026-11-25"
    assert "setup_key" in w and "seed" not in w
    assert s["next_window"]["id"] == "daily-2026-11-26"
    assert "setup_key" not in s["next_window"]


def test_closed_window(client, clock):
    clock.t = T_CLOSED
    auth = register(client)
    s = client.get("/api/v1/status").json()
    assert s["current_window"] is None
    r = client.post(
        "/api/v1/runs/start", headers=auth, json={"window_id": "daily-2026-11-25", "game_version": "v0.113.1", "mod_version": "0.1.0"}
    )
    assert r.status_code == 403


def test_register_twice_conflicts(client):
    register(client)
    r = client.post("/api/v1/register", json={"steam_id": "76561190000000001", "name": "x"})
    assert r.status_code == 409


def test_full_run_and_leaderboard(client, clock):
    a, b = register(client, "1", "Alice"), register(client, "2", "Bob")
    ra, rb = start(client, a).json(), start(client, b).json()
    assert ra["seed"] == rb["seed"]  # everyone plays the same seed
    assert client.get("/api/v1/status", headers=a).json()["current_window"]["your_run"]["status"] == "in_progress"

    clock.t += 700
    res = finish(client, a, ra["run_id"], make_run(ra["seed"], floors=(17, 10)), badges=2).json()
    assert res["valid"] and res["rank"] == 1 and res["flags"] == []
    res = finish(client, b, rb["run_id"], make_run(rb["seed"], floors=(17, 17, 10), victory=True), victory=True).json()
    assert res["valid"] and res["rank"] == 1

    board = client.get("/api/v1/leaderboard").json()
    assert [e["player"] for e in board["entries"]] == ["Bob", "Alice"]
    assert "seed" not in board["window"]
    assert "Bob" in client.get("/").text

    clock.t = board["window"]["submit_deadline"] + 1
    assert client.get("/api/v1/leaderboard?window_id=daily-2026-11-25").json()["window"]["seed"] == ra["seed"]


def test_one_attempt_per_window(client):
    a = register(client)
    assert start(client, a).status_code == 201
    r = start(client, a)
    assert r.status_code == 409


def test_finish_twice_conflicts(client, clock):
    a = register(client)
    r = start(client, a).json()
    clock.t += 700
    assert finish(client, a, r["run_id"], make_run(r["seed"])).status_code == 200
    assert finish(client, a, r["run_id"], make_run(r["seed"])).status_code == 409


def test_wrong_seed_rejected(client, clock):
    a = register(client)
    r = start(client, a).json()
    clock.t += 700
    assert finish(client, a, r["run_id"], make_run("ABCDEFGHJKLM")).status_code == 422


def test_other_players_run_is_404(client, clock):
    a, b = register(client, "1", "A"), register(client, "2", "B")
    r = start(client, a).json()
    assert finish(client, b, r["run_id"], make_run(r["seed"])).status_code == 404


@pytest.mark.parametrize(
    "mutate,flag",
    [
        (lambda run: run.update(num_reloads=1), "reloaded"),
        (lambda run: run.update(start_time=T_OPEN - 3600), "start_time_mismatch"),
        (lambda run: run.update(run_time=99999), "run_time_exceeds_wall_clock"),
        (lambda run: run["extra_fields"].update(used_debug_command=True), "debug_command"),
    ],
)
def test_suspicious_runs_flagged(client, clock, mutate, flag):
    a = register(client)
    r = start(client, a).json()
    clock.t += 700
    run = make_run(r["seed"])
    mutate(run)
    res = finish(client, a, r["run_id"], run).json()
    assert not res["valid"] and flag in res["flags"]
    assert client.get("/api/v1/leaderboard").json()["entries"] == []


def test_lying_about_floors_flagged(client, clock):
    a = register(client)
    r = start(client, a).json()
    clock.t += 700
    run = make_run(r["seed"], floors=(3,))
    res = client.post(
        f"/api/v1/runs/{r['run_id']}/finish",
        headers=a,
        json={
            "victory": False,
            "abandoned": False,
            "score": encode_daily_score(False, 40, 5, 600),
            "game_version": "v0.113.1",
            "mod_version": "0.1.0",
            "run": run,
        },
    ).json()
    assert "score_mismatch" in res["flags"] and not res["valid"]


def test_late_submission_flagged(client, clock):
    a = register(client)
    r = start(client, a).json()
    clock.t = r["window"]["submit_deadline"] + 10
    res = finish(client, a, r["run_id"], make_run(r["seed"])).json()
    assert "late" in res["flags"]


def test_version_pin(client):
    a = register(client)
    w = client.get("/api/v1/status").json()["current_window"]
    r = client.post("/api/v1/runs/start", headers=a, json={"window_id": w["id"], "game_version": "v0.114.0", "mod_version": "0.1.0"})
    assert r.status_code == 409


def test_admin_windows(client, clock):
    assert client.post("/api/v1/admin/windows", json={"starts_at": 1, "ends_at": 2}).status_code == 403
    # Overlaps today's daily window.
    r = client.post("/api/v1/admin/windows", headers=ADMIN, json={"starts_at": T_OPEN, "ends_at": T_OPEN + 60})
    assert r.status_code == 409
    start_ = T_OPEN + 6 * 3600  # 01:00 next day, after the daily window
    r = client.post(
        "/api/v1/admin/windows",
        headers=ADMIN,
        json={"id": "tourney-1", "starts_at": start_, "ends_at": start_ + 3600, "seed": "TESTSEED1234"},
    )
    assert r.status_code == 201, r.text
    clock.t = start_ + 10
    s = client.get("/api/v1/status").json()
    assert s["current_window"]["id"] == "tourney-1"
    a = register(client)
    assert start(client, a).json()["seed"] == "TESTSEED1234"
    assert client.delete("/api/v1/admin/windows/tourney-1", headers=ADMIN).status_code == 409


def test_window_assigns_character_and_rejects_others(client, clock):
    w = client.get("/api/v1/status").json()["current_window"]
    assert w["character"] == "CHARACTER.IRONCLAD" and w["game_mode"] == "dread"
    a = register(client)
    r = start(client, a).json()
    clock.t += 700
    assert finish(client, a, r["run_id"], make_run(r["seed"], character="CHARACTER.SILENT")).status_code == 422


def test_non_dread_rejected(client, clock):
    a = register(client)
    r = start(client, a).json()
    clock.t += 700
    run = make_run(r["seed"])
    run["game_mode"] = "daily"
    assert finish(client, a, r["run_id"], run).status_code == 422


def test_admin_sets_character(client, clock):
    start_ = T_OPEN + 6 * 3600
    r = client.post(
        "/api/v1/admin/windows",
        headers=ADMIN,
        json={"id": "fool-night", "starts_at": start_, "ends_at": start_ + 3600, "character": "CHARACTER.FOOL"},
    )
    assert r.status_code == 201 and r.json()["character"] == "CHARACTER.FOOL"
