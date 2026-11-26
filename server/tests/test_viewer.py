import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from ranked_server.app import create_app
from ranked_server.config import Settings
from ranked_server.validation import encode_daily_score
from ranked_server.viewer import build_viewer, pretty

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dread_run.json").read_text())
T = int(datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc).timestamp())


def test_pretty_names():
    assert pretty("CARD.STRIKE_IRONCLAD") == "Strike Ironclad"
    assert pretty("ENCOUNTER.BYRDONIS_ELITE") == "Byrdonis"
    assert pretty("NONE.NONE") == "" and pretty(None) == ""


def test_reconstructs_paths_from_seed_maps():
    v = build_viewer(FIXTURE["run"], None, FIXTURE["window_maps"], {})
    a1, a2, a3 = v["acts"]
    assert a1["quality"] == "reconstructed" and a2["quality"] == "reconstructed"
    assert a3["quality"] == "not_reached" and a3["nodes"]
    hist = FIXTURE["run"]["map_point_history"]
    for act, entries in ((a1, hist[0]), (a2, hist[1])):
        visited = sorted((n for n in act["nodes"] if n.get("visited")), key=lambda n: n["row"])
        assert [n["row"] for n in visited] == list(range(len(entries)))  # one point per row
        assert [n["type"].replace("_", "") for n in visited] == [e["map_point_type"].replace("_", "") for e in entries]
        # consecutive path points are parent -> child on the map
        for a, b in zip(visited, visited[1:]):
            assert b["id"] in a["children"]
    assert a2["nodes"][0]["row"] == 0 and a1["path"][0] == next(n["id"] for n in a1["nodes"] if n.get("role") == "start")
    assert v["deck"] and v["relics"]


def test_exact_path_from_map_log_wins():
    run = FIXTURE["run"]
    m = FIXTURE["window_maps"]["0"]["map"]
    v = build_viewer(run, None, FIXTURE["window_maps"], {})
    path = v["acts"][0]["path"]
    visited = [{"col": int(p.split(",")[0]), "row": int(p.split(",")[1])} for p in path]
    log = {"acts": {"0": {"map": m, "visited": visited}}}
    v2 = build_viewer(run, log, None, {})
    assert v2["acts"][0]["quality"] == "exact" and v2["acts"][0]["path"] == path
    assert v2["acts"][1]["quality"] == "no_map"  # no map for act 2 without backfill
    assert v2["acts"][1]["floors"]  # but its floors are still listed


def test_bogus_visited_falls_back_to_reconstruction():
    run = FIXTURE["run"]
    m = FIXTURE["window_maps"]["0"]["map"]
    log = {"acts": {"0": {"map": m, "visited": [{"col": 0, "row": 0}, {"col": 6, "row": 9}]}}}
    assert build_viewer(run, log, None, {})["acts"][0]["quality"] == "reconstructed"


def make_client():
    clock = type("C", (), {"t": T, "__call__": lambda self: self.t})()
    s = Settings(db_path=":memory:", admin_key="k", daily_start="18:00", daily_duration_minutes=240,
                 submit_grace_minutes=180, game_version="", characters=("CHARACTER.IRONCLAD",), cookie_secure=False)
    return TestClient(create_app(s, clock=clock), follow_redirects=False), clock


def test_viewer_endpoints_respect_deadline_and_backfill():
    c, clock = make_client()
    tok = c.post("/api/v1/register", json={"steam_id": "1", "name": "Alice"}).json()["token"]
    auth = {"Authorization": f"Bearer {tok}"}
    start = c.post("/api/v1/runs/start", headers=auth,
                   json={"window_id": "daily-2026-11-25", "game_version": "v", "mod_version": "m"}).json()
    clock.t += 3000
    run = dict(FIXTURE["run"], rng={"seed": start["seed"]}, start_time=T, run_time=900, win_time=0, num_reloads=0)
    run["players"] = [dict(run["players"][0], character_id="CHARACTER.IRONCLAD")]
    floors = sum(len(a) for a in run["map_point_history"])
    r = c.post(f"/api/v1/runs/{start['run_id']}/finish", headers=auth, json={
        "victory": False, "abandoned": False, "score": encode_daily_score(False, floors, 0, 900),
        "game_version": "v", "mod_version": "m", "run": run}).json()
    assert r["valid"], r
    rid = start["run_id"]
    assert c.get(f"/runs/{rid}/view").status_code == 404           # seed still secret
    assert c.get(f"/runs/{rid}/viewer.json").status_code == 404

    # Admin sees it any time, and the backfill list offers this window.
    c.post("/admin/login", data={"key": "k"})
    assert c.get(f"/admin/runs/{rid}/viewer.json").status_code == 200
    assert "fetch('viewer.json'" in c.get(f"/admin/runs/{rid}/view").text
    todo = c.get("/api/v1/admin/backfill", headers={"X-Admin-Key": "k"}).json()
    assert todo == [{"window_id": "daily-2026-11-25", "run_id": rid}]
    assert c.get(f"/admin/runs/{rid}/viewer.json").json()["acts"][0]["quality"] == "no_map"
    r = c.put("/api/v1/admin/windows/daily-2026-11-25/maps", headers={"X-Admin-Key": "k"}, json={"acts": FIXTURE["window_maps"]})
    assert r.status_code == 200
    assert c.get("/api/v1/admin/backfill", headers={"X-Admin-Key": "k"}).json() == []

    clock.t = c.get("/api/v1/leaderboard").json()["window"]["submit_deadline"] + 1
    v = c.get(f"/runs/{rid}/viewer.json").json()
    assert v["run"]["player"] == "Alice" and v["acts"][0]["quality"] == "reconstructed"
    assert "steam" not in json.dumps(v).lower()
    page = c.get("/windows/daily-2026-11-25").text
    assert f"/runs/{rid}/view" in page


def _details_by_floor(v):
    return {n["floor"]: n["detail"] for a in v["acts"] for n in a["nodes"] if n.get("detail")}


def test_deck_and_relics_going_into_each_floor():
    v = build_viewer(FIXTURE["run"], None, FIXTURE["window_maps"], {})
    by_floor = _details_by_floor(v)
    assert sorted(by_floor) == list(range(31)) and not any(d.get("deck_approximate") for d in by_floor.values())
    # Going into the first floor: the Regent starting deck (plus the Dread curse) and starter relic only.
    start = by_floor[0]
    assert sorted(start["deck_before"]) == sorted(["Strike Regent"] * 4 + ["Defend Regent"] * 4
                                                 + ["Falling Star", "Venerate", "Ascenders Bane"])
    assert start["relics_before"] == ["Divine Right"]
    # Parrying Shield was picked on floor 11 (floor_added_to_deck 12): not held going into 11, held going into 12.
    assert "Parrying Shield" not in by_floor[11]["relics_before"] and "Parrying Shield" in by_floor[12]["relics_before"]
    # Deck sizes only change by what each floor logs, and the last floor leads to the final deck's size.
    assert [len(by_floor[g]["deck_before"]) for g in (0, 1, 30)] == [11, 12, 31] and len(v["deck"]) == 31


def test_unlogged_deck_change_marks_earlier_floors_approximate():
    run = json.loads(json.dumps(FIXTURE["run"]))
    # Floor 5 says a card was gained that isn't in the final deck and was never logged as removed.
    run["map_point_history"][0][5]["player_stats"][0].setdefault("cards_gained", []).append({"id": "CARD.MYSTERY"})
    by_floor = _details_by_floor(build_viewer(run, None, FIXTURE["window_maps"], {}))
    assert all(by_floor[g].get("deck_approximate") for g in range(6))
    assert not any(by_floor[g].get("deck_approximate") for g in range(6, 31))
