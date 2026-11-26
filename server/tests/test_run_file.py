import json
import re
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ranked_server.app import create_app
from ranked_server.config import Settings
from ranked_server.validation import encode_daily_score

T = int(datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc).timestamp())


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def setup():
    clock = Clock(T)
    s = Settings(db_path=":memory:", admin_key="k", daily_start="18:00", daily_duration_minutes=240,
                 submit_grace_minutes=180, game_version="", characters=("CHARACTER.IRONCLAD",), cookie_secure=False)
    c = TestClient(create_app(s, clock=clock), follow_redirects=False)
    tok = c.post("/api/v1/register", json={"steam_id": "1", "name": "Alice B"}).json()["token"]
    auth = {"Authorization": f"Bearer {tok}"}
    start = c.post("/api/v1/runs/start", headers=auth,
                   json={"window_id": "daily-2026-11-25", "game_version": "v", "mod_version": "m"}).json()
    clock.t += 600
    return c, clock, auth, start


def run_for(seed):
    return {"rng": {"seed": seed}, "players": [{"character_id": "CHARACTER.IRONCLAD"}], "game_mode": "dread",
            "map_point_history": [[{}] * 5], "start_time": T, "run_time": 400, "win_time": 0, "num_reloads": 0}


def run_file_for(seed, **over):
    h = {"seed": seed, "start_time": T, "game_mode": "dread", "win": False, "was_abandoned": True,
         "players": [{"character": "CHARACTER.IRONCLAD"}], "map_point_history": [[{}] * 5]}
    h.update(over)
    return json.dumps(h, indent=2)


def finish(c, auth, start, run_file):
    body = {"victory": False, "abandoned": True, "score": encode_daily_score(False, 5, 0, 400),
            "game_version": "v", "mod_version": "m", "run": run_for(start["seed"])}
    if run_file is not None:
        body["run_file"] = run_file
    return c.post(f"/api/v1/runs/{start['run_id']}/finish", headers=auth, json=body)


def test_run_file_stored_and_released_after_deadline():
    c, clock, auth, start = setup()
    content = run_file_for(start["seed"])
    res = finish(c, auth, start, content).json()
    assert res["valid"] and res["flags"] == []
    url = f"/runs/{start['run_id']}.run"
    assert c.get(url).status_code == 404  # seed still secret
    assert ".run</a>" not in c.get("/windows/daily-2026-11-25").text
    clock.t = c.get("/api/v1/leaderboard").json()["window"]["submit_deadline"] + 1
    r = c.get(url)
    assert r.status_code == 200 and r.text == content  # verbatim
    assert 'filename="daily-2026-11-25_Alice-B.run"' in r.headers["content-disposition"]
    assert f"/runs/{start['run_id']}.run" in c.get("/windows/daily-2026-11-25").text


def test_mismatched_run_file_flags_run():
    c, clock, auth, start = setup()
    res = finish(c, auth, start, run_file_for("ABCDEFGHJKLM")).json()
    assert not res["valid"] and "run_file_mismatch" in res["flags"]


def test_garbage_run_file_flags_run():
    c, clock, auth, start = setup()
    res = finish(c, auth, start, "not json").json()
    assert "run_file_invalid" in res["flags"]


def test_missing_run_file_is_fine():
    c, clock, auth, start = setup()
    res = finish(c, auth, start, None).json()
    assert res["valid"]
    assert c.get(f"/runs/{start['run_id']}.run").status_code == 404


def test_admin_can_download_any_time():
    c, clock, auth, start = setup()
    content = run_file_for(start["seed"])
    finish(c, auth, start, content)
    url = f"/admin/runs/{start['run_id']}.run"
    assert c.get(url).status_code == 303  # not logged in
    c.post("/admin/login", data={"key": "k"})
    page = c.get("/admin/windows/daily-2026-11-25")
    assert page.status_code == 200 and "Alice B" in page.text and url in page.text
    r = c.get(url)
    assert r.status_code == 200 and r.text == content
