"""Public HTML pages: the home page (list of ranked windows) and a window's leaderboard."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from fastapi.responses import Response

from pathlib import Path

VIEWER_HTML = (Path(__file__).parent / "static" / "viewer.html").read_text(encoding="utf-8")

e = html.escape


def run_file_response(content: str, window_id: str, player_name: str) -> Response:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", f"{window_id}_{player_name}").strip("-") or "run"
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{safe}.run"', "Cache-Control": "no-store"},
    )


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%a %d %b %H:%M UTC")


def _duration(seconds: int | None) -> str:
    if seconds is None:
        return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _character(model_id: str | None) -> str:
    return model_id.split(".", 1)[-1].replace("_", " ").title() if model_id else "-"


def _time(ts: int) -> str:
    """A timestamp the page script rewrites into the viewer's local time (UTC is the no-JS fallback)."""
    return f"<time data-ts={ts}>{_utc(ts)}</time>"


def window_state(w: dict, now: int) -> str:
    """open → grace (no new runs, started runs can still be submitted) → completed."""
    if now < w["starts_at"]:
        return "upcoming"
    if now < w["ends_at"]:
        return "open"
    return "grace" if now < w["submit_deadline"] else "completed"


def _tag(state: str) -> str:
    return f"<span class='tag {state}'>{'grace period' if state == 'grace' else state}</span>"


def _results_final_in(w: dict) -> str:
    return f"no new runs · results final in <span data-countdown={w['submit_deadline']}></span>"


def _result(r: dict) -> str:
    return "Victory" if r["victory"] else ("Abandoned" if r["abandoned"] else "Defeat")


def render_home(windows: list[dict], now: int) -> str:
    """windows: dicts with window columns plus players, finished, leader_name, leader_victory, leader_floors."""
    groups = {"open": [], "grace": [], "upcoming": [], "completed": []}
    for w in windows:
        groups[window_state(w, now)].append(w)
    groups["upcoming"].sort(key=lambda w: w["starts_at"])
    groups["completed"].sort(key=lambda w: -w["starts_at"])

    def row(w: dict) -> str:
        state = window_state(w, now)
        if state == "open":
            when = f"closes in <span data-countdown={w['ends_at']}></span>"
        elif state == "grace":
            when = _results_final_in(w)
        elif state == "upcoming":
            when = f"{_time(w['starts_at'])} · opens in <span data-countdown={w['starts_at']}></span>"
        else:
            when = _time(w["starts_at"])
        if w["leader_name"]:
            how = "Victory" if w["leader_victory"] else "floor " + str(w["leader_floors"])
            leader = f"{e(w['leader_name'])} <span class=muted>· {how}</span>"
        else:
            leader = "<span class=muted>-</span>"
        players = "-" if state == "upcoming" else f"{w['finished']}" + (
            f" <span class=muted>(+{w['players'] - w['finished']} playing)</span>" if w["players"] > w["finished"] else ""
        )
        return (
            f"<tr><td>{_tag(state)}</td>"
            f"<td><a href='/windows/{e(w['id'])}'>{e(w['id'])}</a></td>"
            f"<td>{e(_character(w['character']))}</td><td>{when}</td>"
            f"<td class=n>{players}</td><td>{leader}</td></tr>"
        )

    def section(title: str, items: list[dict], empty: str) -> str:
        body = "".join(row(w) for w in items) or f"<tr><td colspan=6 class=empty>{empty}</td></tr>"
        return (
            f"<h2>{title}</h2><table class=runs><thead><tr><th>Status</th><th>Run</th><th>Character</th><th>When</th>"
            f"<th class=n>Players</th><th>Leader</th></tr></thead><tbody>{body}</tbody></table>"
        )

    body = f"""
<header><h1>STS2 Ranked</h1></header>
{section("Open now", groups["open"] + groups["grace"], "No run is open right now.")}
{section("Upcoming", groups["upcoming"], "Nothing scheduled yet.")}
{section("Completed", groups["completed"], "No completed runs yet.")}
"""
    return _page("STS2 Ranked", body)


def render_window(board: dict, now: int) -> str:
    w = board["window"]
    state = window_state(w, now)
    rows = ""
    files_public = "seed" in w  # the .run file contains the seed, so it's only offered once that is public
    for r in board["entries"]:
        file_cell = ""
        if files_public:
            view = f"<a href='/runs/{e(r['run_id'])}/view'>View</a>"
            dl = f" · <a href='/runs/{e(r['run_id'])}.run'>.run</a>" if r.get("has_run_file") else ""
            file_cell = f"<td>{view}{dl}</td>"
        rows += (
            f"<tr><td class=n>{r['rank']}</td><td>{e(r['player'])}</td><td>{_result(r)}</td>"
            f"<td class=n>{r['floors']}</td><td class=n>{_duration(r['run_time'])}</td>{file_cell}</tr>"
        )
    if not rows:
        rows = f"<tr><td colspan={6 if files_public else 5} class=empty>No finished runs yet.</td></tr>"
    if state == "open":
        when = f"open · closes in <span data-countdown={w['ends_at']}></span>"
    elif state == "upcoming":
        when = f"opens in <span data-countdown={w['starts_at']}></span>"
    elif state == "grace":
        when = "grace period · " + _results_final_in(w)
    else:
        when = "completed"
    seed = f" · seed <code>{e(w['seed'])}</code>" if "seed" in w else ""
    body = f"""
<p><a href="/">← All runs</a></p>
<h1>{e(w['id'])} {_tag(state)}</h1>
<p class=muted>{e(_character(w.get('character')))} · {_time(w['starts_at'])} → {_time(w['ends_at'])}<br>
{when} · {board['in_progress']} in progress{seed}</p>
<table><thead><tr><th class=n>#</th><th>Player</th><th>Result</th><th class=n>Floors</th>
<th class=n>Time</th>{"<th>Run</th>" if files_public else ""}</tr></thead><tbody>{rows}</tbody></table>
"""
    return _page(f"{w['id']} · STS2 Ranked", body)


def render_not_found() -> str:
    return _page("Not found · STS2 Ranked", "<p><a href='/'>← All runs</a></p><h1>No such run</h1>")


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<style>{CSS}</style></head><body>{body}<script>{JS}</script></body></html>"""


CSS = """
/* Same palette as the run viewer (static/viewer.html): navy panels, parchment text, gold accents. */
:root{color-scheme:dark;--bg:#1a1a2e;--panel:#16213e;--fg:#e0d7c6;--muted:#888;--line:#c9a84c22;--row:#ffffff08;
--gold:#c9a84c;--blue:#7ec8e3;--ok:#5cb85c;--bad:#e05c5c}
*{box-sizing:border-box}
body{font:15px/1.5 'Segoe UI',system-ui,sans-serif;color:var(--fg);background:var(--bg);max-width:960px;margin:2rem auto;padding:0 1rem}
header{border-bottom:1px solid #c9a84c44;padding-bottom:.6rem;margin-bottom:.5rem}
h1{margin:.2rem 0;font-size:1.6rem;color:var(--gold);font-weight:normal;text-shadow:0 0 20px #c9a84c55}
h2{font-size:.85rem;margin:2rem 0 .5rem;color:var(--gold);text-transform:uppercase;letter-spacing:1px;font-weight:600}
table{width:100%;border-collapse:separate;border-spacing:0;margin:.5rem 0;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
th,td{padding:.5rem .7rem;border-bottom:1px solid var(--row);text-align:left}
tbody tr:last-child td{border-bottom:none} tbody tr:hover td{background:#c9a84c0d}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:1px;color:var(--gold);font-weight:600;border-bottom:1px solid var(--line)}
.n{text-align:right;font-variant-numeric:tabular-nums}
.empty{text-align:center;color:#555} .muted{color:var(--muted)}
a{color:var(--gold);text-decoration:none} a:hover{text-decoration:underline} code{font-size:.9em;color:var(--blue)}
.tag{font-size:.75rem;padding:2px 8px;border-radius:10px;white-space:nowrap;vertical-align:middle;font-weight:normal;
background:#88888822;color:var(--muted);border:1px solid #88888844}
.tag.open{background:#5cb85c22;color:var(--ok);border-color:#5cb85c55}
.tag.upcoming{background:#c9a84c22;color:var(--gold);border-color:#c9a84c44}
.tag.completed{background:#88888822;color:var(--muted);border-color:#88888844}
.tag.grace{background:#5cb85c11;color:var(--ok);border-color:#5cb85c55;border-style:dashed}
/* Home page: identical column widths in every section so they line up. */
table.runs{table-layout:fixed} table.runs td{overflow:hidden;text-overflow:ellipsis}
table.runs th:nth-child(1){width:110px} table.runs th:nth-child(2){width:20%} table.runs th:nth-child(3){width:13%}
table.runs th:nth-child(5){width:130px} table.runs th:nth-child(6){width:19%}
"""

JS = """
const fmt = {weekday:'short', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'};
document.querySelectorAll('time[data-ts]').forEach(el => {
  const d = new Date(el.dataset.ts * 1000); el.textContent = d.toLocaleString([], fmt); el.title = d.toISOString();
});
const countdowns = [...document.querySelectorAll('[data-countdown]')];
function tick() {
  const now = Date.now() / 1000;
  for (const el of countdowns) {
    let s = Math.max(0, Math.round(el.dataset.countdown - now));
    if (s === 0 && !el.dataset.done) { el.dataset.done = 1; setTimeout(() => location.reload(), 3000); }
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); s %= 60;
    el.textContent = d ? `${d}d ${h}h` : h ? `${h}h ${String(m).padStart(2,'0')}m` : `${m}m ${String(s).padStart(2,'0')}s`;
  }
}
if (countdowns.length) { tick(); setInterval(tick, 1000); }
"""
