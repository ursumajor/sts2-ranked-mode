import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ranked_server.app import create_app
from ranked_server.config import Settings

# Wednesday 2026-11-25 19:00 UTC
T = int(datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc).timestamp())


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make(clock, **kw):
    s = dict(
        db_path=":memory:",
        admin_key="secret",
        daily_start="18:00",
        daily_duration_minutes=240,
        submit_grace_minutes=180,
        game_version="",
        cookie_secure=False,
    )
    s.update(kw)
    return TestClient(create_app(Settings(**s), clock=clock), follow_redirects=False)


@pytest.fixture
def clock():
    return Clock(T)


@pytest.fixture
def client(clock):
    return make(clock)


def login(client, key="secret"):
    return client.post("/admin/login", data={"key": key})


def csrf(client):
    page = client.get("/admin").text
    return re.search(r"name=csrf value='([0-9a-f]+)'", page).group(1)


def db(client):
    return client.app.state.db


def test_login_page_and_wrong_key(client):
    assert "Admin key" in client.get("/admin").text
    r = login(client, "nope")
    assert r.status_code == 303 and "Wrong" in r.headers["location"]
    assert "ranked_admin" not in client.cookies


def test_login_rate_limited(client):
    for _ in range(5):
        login(client, "nope")
    r = login(client, "secret")
    assert "Too+many" in r.headers["location"]


def test_login_sets_hardened_cookie(client):
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/admin" in cookie
    page = client.get("/admin")
    assert "Recurring schedules" in page.text
    assert page.headers["cache-control"] == "no-store"


def test_secure_cookie_flag():
    r = make(Clock(T), cookie_secure=True).post("/admin/login", data={"key": "secret"})
    assert "secure" in r.headers["set-cookie"].lower()


def test_forged_cookie_rejected(client):
    client.cookies.set("ranked_admin", f"{T + 999}.deadbeef", path="/admin")
    assert "Admin key" in client.get("/admin").text


def test_session_expires(client, clock):
    login(client)
    clock.t += 13 * 3600
    assert "Admin key" in client.get("/admin").text


def test_post_requires_csrf(client):
    login(client)
    r = client.post("/admin/schedules", data={"slug": "x", "start_time": "10:00"})
    assert "expired" in r.headers["location"]
    assert db(client).one("SELECT 1 FROM schedules WHERE slug = 'x'") is None


def test_env_daily_seeded_once(client, clock):
    rows = db(client).all("SELECT slug, start_time FROM schedules")
    assert [tuple(r) for r in rows] == [("daily", "18:00")]
    login(client)
    sid = db(client).one("SELECT id FROM schedules WHERE slug='daily'")["id"]
    client.post(f"/admin/schedules/{sid}/delete", data={"csrf": csrf(client)})
    # A fresh app on the same DB must not recreate it from env.
    from ranked_server import windows as win
    win.seed_schedules_from_env(db(client), client.app.state.settings)
    assert db(client).all("SELECT * FROM schedules") == []


def schedule_form(token, **over):
    form = {
        "csrf": token,
        "slug": "weekend",
        "start_time": "20:00",
        "duration_minutes": "120",
        "submit_grace_minutes": "60",
        "weekdays": ["5", "6"],
        "characters": ["CHARACTER.DEFECT"],
        "game_version": "",
        "enabled": "1",
    }
    form.update(over)
    return form


def test_create_schedule_respects_weekdays(client, clock):
    login(client)
    # Disable the seeded 18:00 daily so it doesn't clash.
    db(client).execute("UPDATE schedules SET enabled = 0")
    db(client).execute("DELETE FROM windows")
    r = client.post("/admin/schedules", data=schedule_form(csrf(client), weekdays=["2", "3"]))  # Wed, Thu
    assert "created" in r.headers["location"]
    ids = {r["id"] for r in db(client).all("SELECT id FROM windows")}
    assert ids == {"weekend-2026-11-25", "weekend-2026-11-26"}  # Tue (yesterday) skipped
    w = db(client).one("SELECT * FROM windows WHERE id = 'weekend-2026-11-25'")
    assert w["character"] == "CHARACTER.DEFECT" and w["ends_at"] - w["starts_at"] == 7200
    assert w["submit_deadline"] == w["ends_at"] + 3600


def test_schedule_validation(client):
    login(client)
    token = csrf(client)
    for bad in (dict(slug="Bad Name"), dict(start_time="25:00"), dict(weekdays=[]), dict(characters=[]),
                dict(duration_minutes="0")):
        r = client.post("/admin/schedules", data=schedule_form(token, **bad))
        assert "err=" in r.headers["location"], bad
    assert db(client).one("SELECT 1 FROM schedules WHERE slug = 'weekend'") is None


def test_update_regenerates_unstarted_windows_only(client, clock):
    login(client)
    d = db(client)
    sid = d.one("SELECT id FROM schedules WHERE slug='daily'")["id"]
    client.get("/api/v1/status")
    tomorrow_before = d.one("SELECT seed FROM windows WHERE id = 'daily-2026-11-26'")["seed"]
    today_before = d.one("SELECT starts_at FROM windows WHERE id = 'daily-2026-11-25'")["starts_at"]
    r = client.post(f"/admin/schedules/{sid}/update", data=schedule_form(csrf(client), start_time="21:30", weekdays=list("0123456")))
    assert "regenerated" in r.headers["location"]
    tomorrow = d.one("SELECT * FROM windows WHERE id = 'daily-2026-11-26'")
    assert tomorrow["seed"] != tomorrow_before
    assert datetime.fromtimestamp(tomorrow["starts_at"], tz=timezone.utc).strftime("%H:%M") == "21:30"
    # Today's window is already open: untouched.
    assert d.one("SELECT starts_at FROM windows WHERE id = 'daily-2026-11-25'")["starts_at"] == today_before


def test_disable_schedule_removes_upcoming(client):
    login(client)
    d = db(client)
    sid = d.one("SELECT id FROM schedules WHERE slug='daily'")["id"]
    client.get("/api/v1/status")
    form = schedule_form(csrf(client), start_time="18:00", weekdays=list("0123456"))
    form.pop("enabled")
    client.post(f"/admin/schedules/{sid}/update", data=form)
    assert d.one("SELECT 1 FROM windows WHERE id = 'daily-2026-11-26'") is None
    assert d.one("SELECT 1 FROM windows WHERE id = 'daily-2026-11-25'") is not None


def fixed_form(token, start, end, **over):
    form = {"csrf": token, "starts_at": str(start), "ends_at": str(end), "submit_grace_minutes": "30",
            "character": "CHARACTER.FOOL", "game_version": "", "id": "cup", "seed": ""}
    form.update(over)
    return form


def test_fixed_window_create_overlap_delete(client, clock):
    login(client)
    token = csrf(client)
    # Clashes with today's 18:00-22:00 daily window.
    r = client.post("/admin/windows", data=fixed_form(token, T, T + 600))
    assert "Overlaps" in r.headers["location"]
    start = T + 4 * 3600  # 23:00
    r = client.post("/admin/windows", data=fixed_form(token, start, start + 3600, seed="abcd2345"))
    assert "created" in r.headers["location"]
    w = db(client).one("SELECT * FROM windows WHERE id = 'cup'")
    assert w["kind"] == "manual" and w["character"] == "CHARACTER.FOOL" and w["seed"] == "ABCD2345"
    assert "cup" in client.get("/admin").text
    r = client.post("/admin/windows/cup/delete", data={"csrf": token})
    assert "deleted" in r.headers["location"]
    assert db(client).one("SELECT 1 FROM windows WHERE id = 'cup'") is None


def test_fixed_window_validation(client):
    login(client)
    token = csrf(client)
    start = T + 4 * 3600
    for bad in (dict(ends_at=str(start)), dict(seed="OOOO"), dict(character="CHARACTER.NOPE"), dict(id="bad id")):
        r = client.post("/admin/windows", data=fixed_form(token, start, start + 60, **bad))
        assert "err=" in r.headers["location"], bad
    r = client.post("/admin/windows", data=fixed_form(token, T - 7200, T - 3600))
    assert "already+be+over" in r.headers["location"]


def test_window_with_runs_cannot_be_deleted(client):
    reg = client.post("/api/v1/register", json={"steam_id": "1", "name": "A"}).json()
    auth = {"Authorization": f"Bearer {reg['token']}"}
    w = client.get("/api/v1/status").json()["current_window"]
    client.post("/api/v1/runs/start", headers=auth, json={"window_id": w["id"], "game_version": "v", "mod_version": "m"})
    login(client)
    r = client.post(f"/admin/windows/{w['id']}/delete", data={"csrf": csrf(client)})
    assert "already+has+runs" in r.headers["location"]


def test_admin_disabled_without_key(clock):
    c = make(clock, admin_key="")
    assert c.get("/admin").status_code == 503
