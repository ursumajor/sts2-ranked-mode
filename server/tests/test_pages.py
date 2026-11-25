from datetime import datetime, timezone

from fastapi.testclient import TestClient

from ranked_server.app import create_app
from ranked_server.config import Settings
from ranked_server.validation import encode_daily_score

T = int(datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc).timestamp())  # inside 18:00-22:00


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def client(clock):
    s = Settings(db_path=":memory:", admin_key="k", daily_start="18:00", daily_duration_minutes=240,
                 submit_grace_minutes=180, game_version="", characters=("CHARACTER.IRONCLAD",))
    return TestClient(create_app(s, clock=clock), follow_redirects=False)


def section(page, title):
    start = page.index(f"<h2>{title}</h2>")
    end = page.find("<h2>", start + 1)
    return page[start:end if end != -1 else None]


def test_home_groups_windows():
    c = client(Clock(T))
    page = c.get("/").text
    assert "daily-2026-11-25" in section(page, "Open now")
    assert "daily-2026-11-26" in section(page, "Upcoming")
    assert "daily-2026-11-24" in section(page, "Completed")
    assert "data-countdown" in section(page, "Open now")
    assert "Ironclad" in page


def test_home_shows_leader_and_window_page():
    clock = Clock(T)
    c = client(clock)
    tok = c.post("/api/v1/register", json={"steam_id": "1", "name": "Alice"}).json()["token"]
    auth = {"Authorization": f"Bearer {tok}"}
    start = c.post("/api/v1/runs/start", headers=auth,
                   json={"window_id": "daily-2026-11-25", "game_version": "v", "mod_version": "m"}).json()
    clock.t += 600
    run = {"rng": {"seed": start["seed"]}, "players": [{"character_id": "CHARACTER.IRONCLAD"}], "game_mode": "dread",
           "map_point_history": [[{}] * 12], "start_time": T, "run_time": 500, "win_time": 0, "num_reloads": 0}
    r = c.post(f"/api/v1/runs/{start['run_id']}/finish", headers=auth, json={
        "victory": False, "abandoned": False, "score": encode_daily_score(False, 12, 0, 500),
        "game_version": "v", "mod_version": "m", "run": run})
    assert r.json()["valid"]
    open_now = section(c.get("/").text, "Open now")
    assert "Alice" in open_now and "floor 12" in open_now
    page = c.get("/windows/daily-2026-11-25")
    assert page.status_code == 200 and "Alice" in page.text and "tag open" in page.text


def test_old_links_redirect_and_404():
    c = client(Clock(T))
    r = c.get("/?window_id=daily-2026-11-25")
    assert r.status_code == 301 and r.headers["location"] == "/windows/daily-2026-11-25"
    assert c.get("/windows/nope").status_code == 404
