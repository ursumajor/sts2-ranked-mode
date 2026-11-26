"""Public HTML pages: the home page (list of ranked windows) and a window's leaderboard."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from fastapi.responses import Response

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
    if w["starts_at"] <= now < w["ends_at"]:
        return "open"
    return "upcoming" if now < w["starts_at"] else "completed"


def _result(r: dict) -> str:
    return "Victory" if r["victory"] else ("Abandoned" if r["abandoned"] else "Defeat")


def render_home(windows: list[dict], now: int) -> str:
    """windows: dicts with window columns plus players, finished, leader_name, leader_victory, leader_floors."""
    groups = {"open": [], "upcoming": [], "completed": []}
    for w in windows:
        groups[window_state(w, now)].append(w)
    groups["upcoming"].sort(key=lambda w: w["starts_at"])
    groups["completed"].sort(key=lambda w: -w["starts_at"])

    def row(w: dict) -> str:
        state = window_state(w, now)
        if state == "open":
            when = f"closes in <span data-countdown={w['ends_at']}></span>"
        elif state == "upcoming":
            when = f"{_time(w['starts_at'])} · opens in <span data-countdown={w['starts_at']}></span>"
        else:
            when = _time(w["starts_at"])
            if now < w["submit_deadline"]:
                when += " · <span class=muted>results not final yet</span>"
        if w["leader_name"]:
            how = "Victory" if w["leader_victory"] else "floor " + str(w["leader_floors"])
            leader = f"{e(w['leader_name'])} <span class=muted>· {how}</span>"
        else:
            leader = "<span class=muted>-</span>"
        players = "-" if state == "upcoming" else f"{w['finished']}" + (
            f" <span class=muted>(+{w['players'] - w['finished']} playing)</span>" if w["players"] > w["finished"] else ""
        )
        return (
            f"<tr><td><span class='tag {state}'>{state}</span></td>"
            f"<td><a href='/windows/{e(w['id'])}'>{e(w['id'])}</a></td>"
            f"<td>{e(_character(w['character']))}</td><td>{when}</td>"
            f"<td class=n>{players}</td><td>{leader}</td></tr>"
        )

    def section(title: str, items: list[dict], empty: str) -> str:
        body = "".join(row(w) for w in items) or f"<tr><td colspan=6 class=empty>{empty}</td></tr>"
        return (
            f"<h2>{title}</h2><table><thead><tr><th>Status</th><th>Run</th><th>Character</th><th>When</th>"
            f"<th class=n>Players</th><th>Leader</th></tr></thead><tbody>{body}</tbody></table>"
        )

    body = f"""
<header><h1>STS2 Ranked</h1></header>
{section("Open now", groups["open"], "No run is open right now.")}
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
            file_cell = (f"<td><a href='/runs/{e(r['run_id'])}.run'>.run</a></td>" if r.get("has_run_file")
                         else "<td class=muted>-</td>")
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
    else:
        when = "completed"
    seed = f" · seed <code>{e(w['seed'])}</code>" if "seed" in w else ""
    body = f"""
<p><a href="/">← All runs</a></p>
<h1>{e(w['id'])} <span class='tag {state}'>{state}</span></h1>
<p class=muted>{e(_character(w.get('character')))} · {_time(w['starts_at'])} → {_time(w['ends_at'])}<br>
{when} · {board['in_progress']} in progress{seed}</p>
<table><thead><tr><th class=n>#</th><th>Player</th><th>Result</th><th class=n>Floors</th>
<th class=n>Time</th>{"<th>Run file</th>" if files_public else ""}</tr></thead><tbody>{rows}</tbody></table>
"""
    return _page(f"{w['id']} · STS2 Ranked", body)


def render_not_found() -> str:
    return _page("Not found · STS2 Ranked", "<p><a href='/'>← All runs</a></p><h1>No such run</h1>")


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<style>{CSS}</style></head><body>{body}<script>{JS}</script></body></html>"""


CSS = """
:root{color-scheme:light dark;--fg:#1d1b18;--bg:#f7f4ee;--muted:#6b645a;--line:#ddd5c8;--accent:#9c3d1f;--ok:#2f6b3a}
@media(prefers-color-scheme:dark){:root{--fg:#ece6dc;--bg:#191714;--muted:#a39a8c;--line:#3a342c;--accent:#e0875e;--ok:#7fc28c}}
body{font:15px/1.5 system-ui,sans-serif;color:var(--fg);background:var(--bg);max-width:960px;margin:2rem auto;padding:0 1rem}
h1{margin:.2rem 0;font-size:1.6rem} h2{font-size:1.1rem;margin:2rem 0 .3rem}
table{width:100%;border-collapse:collapse;margin:.5rem 0} th,td{padding:.45rem .5rem;border-bottom:1px solid var(--line);text-align:left}
th{font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)} .n{text-align:right;font-variant-numeric:tabular-nums}
.empty{text-align:center;color:var(--muted)} .muted{color:var(--muted)} a{color:var(--accent)} code{font-size:.9em}
.tag{font-size:.75rem;border:1px solid var(--line);border-radius:999px;padding:0 .5rem;white-space:nowrap;vertical-align:middle;font-weight:normal}
.tag.open{color:var(--ok);border-color:var(--ok)} .tag.upcoming{color:var(--accent);border-color:var(--accent)} .tag.completed{color:var(--muted)}
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
