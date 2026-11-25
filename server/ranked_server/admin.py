"""Browser admin panel at /admin.

Log in with RANKED_ADMIN_KEY, then manage recurring schedules and fixed one-off windows.

- Session: an HMAC-signed, HttpOnly, SameSite=Strict cookie (12 h), keyed on the admin key, so changing the
  key logs everyone out.
- Every form carries a CSRF token derived from the session.
- Failed logins are rate-limited per client IP (Cloudflare's CF-Connecting-IP when present).
- Times are stored and validated in UTC. The page's small script shows them in the browser's local time and
  converts the fixed-window inputs from local time.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import time
import uuid
from collections import defaultdict, deque
from typing import Callable
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import windows as win
from .config import DEFAULT_CHARACTERS, Settings
from .db import Database

COOKIE = "ranked_admin"
SESSION_SECONDS = 12 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60
MAX_FORM_BYTES = 64 * 1024

e = html.escape


def _pretty_character(model_id: str | None) -> str:
    return model_id.split(".", 1)[-1].title() if model_id else "Random"


def mount_admin(app: FastAPI, db: Database, settings: Settings, clock: Callable[[], int]) -> None:
    failures: dict[str, deque] = defaultdict(deque)
    all_characters = list(dict.fromkeys(list(DEFAULT_CHARACTERS) + list(settings.characters)))

    # ---------- auth helpers ----------

    def sign(purpose: str, value: str) -> str:
        return hmac.new(settings.admin_key.encode(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()

    def session_of(request: Request) -> str | None:
        if not settings.admin_key:
            return None
        raw = request.cookies.get(COOKIE)
        if not raw or "." not in raw:
            return None
        exp, mac = raw.split(".", 1)
        if not exp.isdigit() or int(exp) < clock():
            return None
        return raw if hmac.compare_digest(mac, sign("session", exp)) else None

    def csrf_for(session: str) -> str:
        return sign("csrf", session)

    def client_ip(request: Request) -> str:
        return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")

    def too_many_failures(ip: str) -> bool:
        q = failures[ip]
        cutoff = time.monotonic() - FAILURE_WINDOW_SECONDS
        while q and q[0] < cutoff:
            q.popleft()
        return len(q) >= MAX_FAILURES

    def harden(resp: Response) -> Response:
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    def redirect(msg: str | None = None, err: str | None = None) -> Response:
        q = {k: v for k, v in (("msg", msg), ("err", err)) if v}
        return harden(RedirectResponse("/admin" + ("?" + urlencode(q) if q else ""), status_code=303))

    async def read_form(request: Request) -> dict[str, list[str]]:
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            return {}
        return parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)

    async def authed_form(request: Request) -> tuple[dict[str, list[str]] | None, Response | None]:
        session = session_of(request)
        if not session:
            return None, redirect(err="Please log in.")
        form = await read_form(request)
        token = (form.get("csrf") or [""])[0]
        if not hmac.compare_digest(token, csrf_for(session)):
            return None, redirect(err="That form expired. Please try again.")
        return form, None

    def first(form: dict[str, list[str]], key: str) -> str:
        return (form.get(key) or [""])[0].strip()

    # ---------- form parsing ----------

    def parse_int(form, key: str, lo: int, hi: int, label: str) -> int:
        raw = first(form, key)
        if not raw.lstrip("-").isdigit() or not lo <= int(raw) <= hi:
            raise ValueError(f"{label} must be a whole number from {lo} to {hi}.")
        return int(raw)

    def parse_characters(form) -> str | None:
        chosen = [c for c in form.get("characters", []) if c in all_characters]
        if not chosen:
            raise ValueError("Pick at least one character.")
        return None if set(chosen) == set(settings.characters) else ",".join(chosen)

    def parse_version(form) -> str | None:
        v = first(form, "game_version")
        if len(v) > 64:
            raise ValueError("Game version is too long.")
        return v or None

    def parse_schedule(form, *, new: bool) -> dict:
        out: dict = {}
        if new:
            slug = first(form, "slug").lower()
            if not win.SLUG_PATTERN.match(slug):
                raise ValueError("Name must be 1–32 lowercase letters, digits or dashes (it prefixes window ids).")
            out["slug"] = slug
        start = first(form, "start_time")
        if not win.TIME_PATTERN.match(start):
            raise ValueError("Start time must be HH:MM (UTC).")
        out["start_time"] = start
        out["duration_minutes"] = parse_int(form, "duration_minutes", 1, 24 * 60, "Length")
        out["submit_grace_minutes"] = parse_int(form, "submit_grace_minutes", 0, 7 * 24 * 60, "Submit grace")
        days = "".join(sorted({d for d in form.get("weekdays", []) if d in "0123456" and len(d) == 1}))
        if not days:
            raise ValueError("Pick at least one weekday.")
        out["weekdays"] = days
        out["characters"] = parse_characters(form)
        out["game_version"] = parse_version(form)
        out["enabled"] = 1 if first(form, "enabled") else 0
        return out

    # ---------- routes ----------

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(request: Request, msg: str = "", err: str = ""):
        if not settings.admin_key:
            return harden(HTMLResponse(page("Admin disabled", "<p>Set <code>RANKED_ADMIN_KEY</code> to enable the admin panel.</p>"), 503))
        session = session_of(request)
        if not session:
            return harden(HTMLResponse(login_page(err)))
        return harden(HTMLResponse(dashboard(csrf_for(session), msg, err)))

    @app.post("/admin/login")
    async def admin_login(request: Request):
        if not settings.admin_key:
            return redirect()
        ip = client_ip(request)
        if too_many_failures(ip):
            return redirect(err="Too many failed attempts. Try again in 15 minutes.")
        key = first(await read_form(request), "key")
        if not hmac.compare_digest(key.encode(), settings.admin_key.encode()):
            failures[ip].append(time.monotonic())
            return redirect(err="Wrong admin key.")
        failures.pop(ip, None)
        exp = str(clock() + SESSION_SECONDS)
        resp = redirect()
        resp.set_cookie(
            COOKIE,
            f"{exp}.{sign('session', exp)}",
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            path="/admin",
        )
        return resp

    @app.post("/admin/logout")
    async def admin_logout(request: Request):
        form, bad = await authed_form(request)
        resp = redirect(msg="Logged out.") if form is not None else bad
        resp.delete_cookie(COOKIE, path="/admin")
        return resp

    @app.post("/admin/schedules")
    async def create_schedule(request: Request):
        form, bad = await authed_form(request)
        if bad:
            return bad
        try:
            s = parse_schedule(form, new=True)
        except ValueError as ex:
            return redirect(err=str(ex))
        with db.lock:
            if db.one("SELECT 1 FROM schedules WHERE slug = ?", (s["slug"],)):
                return redirect(err=f"A schedule named '{s['slug']}' already exists.")
            db.execute(
                "INSERT INTO schedules (slug, start_time, duration_minutes, submit_grace_minutes, weekdays, characters,"
                " game_version, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (s["slug"], s["start_time"], s["duration_minutes"], s["submit_grace_minutes"], s["weekdays"],
                 s["characters"], s["game_version"], s["enabled"], clock()),
            )
            win.ensure_recurring_windows(db, settings, clock())
        return redirect(msg=f"Schedule '{s['slug']}' created.")

    @app.post("/admin/schedules/{sid}/update")
    async def update_schedule(request: Request, sid: int):
        form, bad = await authed_form(request)
        if bad:
            return bad
        try:
            s = parse_schedule(form, new=False)
        except ValueError as ex:
            return redirect(err=str(ex))
        with db.lock:
            row = db.one("SELECT slug FROM schedules WHERE id = ?", (sid,))
            if row is None:
                return redirect(err="That schedule no longer exists.")
            db.execute(
                "UPDATE schedules SET start_time = ?, duration_minutes = ?, submit_grace_minutes = ?, weekdays = ?,"
                " characters = ?, game_version = ?, enabled = ? WHERE id = ?",
                (s["start_time"], s["duration_minutes"], s["submit_grace_minutes"], s["weekdays"], s["characters"],
                 s["game_version"], s["enabled"], sid),
            )
            dropped = win.drop_unstarted_windows(db, sid, clock())
            win.ensure_recurring_windows(db, settings, clock())
        note = f" {dropped} upcoming window(s) regenerated." if dropped else ""
        return redirect(msg=f"Schedule '{row['slug']}' saved.{note}")

    @app.post("/admin/schedules/{sid}/delete")
    async def delete_schedule(request: Request, sid: int):
        form, bad = await authed_form(request)
        if bad:
            return bad
        with db.lock:
            row = db.one("SELECT slug FROM schedules WHERE id = ?", (sid,))
            if row is None:
                return redirect(err="That schedule no longer exists.")
            dropped = win.drop_unstarted_windows(db, sid, clock())
            db.execute("DELETE FROM schedules WHERE id = ?", (sid,))
        return redirect(msg=f"Schedule '{row['slug']}' deleted ({dropped} upcoming window(s) removed; past and open ones kept).")

    @app.post("/admin/windows")
    async def create_fixed_window(request: Request):
        form, bad = await authed_form(request)
        if bad:
            return bad
        try:
            starts_at = parse_int(form, "starts_at", 0, 2**40, "Start")
            ends_at = parse_int(form, "ends_at", 0, 2**40, "End")
            if ends_at <= starts_at:
                raise ValueError("The end must be after the start.")
            if ends_at <= clock():
                raise ValueError("That window would already be over.")
            grace = parse_int(form, "submit_grace_minutes", 0, 7 * 24 * 60, "Submit grace")
            wid = first(form, "id")
            if wid and not win.WINDOW_ID_PATTERN.match(wid):
                raise ValueError("Window id may only use letters, digits, '-' and '_' (max 64).")
            character = first(form, "character")
            if character and character not in all_characters:
                raise ValueError("Unknown character.")
            seed = first(form, "seed").upper()
            if seed and not win.SEED_PATTERN.match(seed):
                raise ValueError("Seed must be 4–32 characters from 0-9 and A-Z, without O or I.")
            version = parse_version(form)
        except ValueError as ex:
            return redirect(err=str(ex))
        wid = wid or f"fixed-{uuid.uuid4().hex[:8]}"
        with db.lock:
            win.ensure_recurring_windows(db, settings, clock())
            if db.one("SELECT 1 FROM windows WHERE id = ?", (wid,)):
                return redirect(err=f"A window called '{wid}' already exists.")
            clash = win.overlapping(db, starts_at, ends_at)
            if clash:
                return redirect(err=f"Overlaps window '{clash[0]['id']}'. Delete it first or pick another time.")
            win.create_window(
                db,
                settings,
                window_id=wid,
                kind=win.KIND_FIXED,
                starts_at=starts_at,
                ends_at=ends_at,
                game_version=version,
                seed=seed or None,
                character=character or None,
                submit_grace_minutes=grace,
            )
        return redirect(msg=f"Window '{wid}' created.")

    @app.post("/admin/windows/{wid}/delete")
    async def delete_window(request: Request, wid: str):
        form, bad = await authed_form(request)
        if bad:
            return bad
        with db.lock:
            if db.one("SELECT 1 FROM runs WHERE window_id = ?", (wid,)):
                return redirect(err=f"'{wid}' already has runs, so it can't be deleted.")
            row = db.one("SELECT schedule_id, starts_at FROM windows WHERE id = ?", (wid,))
            if row is None:
                return redirect(err="That window no longer exists.")
            db.execute("DELETE FROM windows WHERE id = ?", (wid,))
        note = ""
        if row["schedule_id"] is not None:
            note = " Its schedule will recreate it on the next status check unless you disable or edit the schedule."
        return redirect(msg=f"Window '{wid}' deleted.{note}")

    # ---------- rendering ----------

    def login_page(err: str) -> str:
        body = f"""
<form method=post action=/admin/login class=card style="max-width:380px">
  <h1>Ranked admin</h1>
  {f'<p class=err>{e(err)}</p>' if err else ''}
  <label>Admin key<input type=password name=key autocomplete=current-password required autofocus></label>
  <button>Log in</button>
</form>"""
        return page("Ranked admin · login", body)

    def character_checkboxes(selected: set[str]) -> str:
        return "".join(
            f"<label class=chk><input type=checkbox name=characters value='{e(c)}'{' checked' if c in selected else ''}>"
            f"{e(_pretty_character(c))}</label>"
            for c in all_characters
        )

    def weekday_checkboxes(selected: str) -> str:
        return "".join(
            f"<label class=chk><input type=checkbox name=weekdays value={i}{' checked' if str(i) in selected else ''}>{d}</label>"
            for i, d in enumerate(win.WEEKDAY_NAMES)
        )

    def schedule_fields(s: dict | None) -> str:
        s = s or {}
        pool = set(s["characters"].split(",")) if s.get("characters") else set(settings.characters)
        return f"""
  <div class=grid>
    <label>Start time (UTC)<input type=time name=start_time data-utc-time required value="{e(s.get('start_time', '18:00'))}">
      <small class=local-hint></small></label>
    <label>Open for (minutes)<input type=number name=duration_minutes min=1 max=1440 required value="{s.get('duration_minutes', 240)}">
      <small>how long players can <b>start</b> a run</small></label>
    <label>Submit grace (minutes)<input type=number name=submit_grace_minutes min=0 max=10080 required value="{s.get('submit_grace_minutes', settings.submit_grace_minutes)}">
      <small>extra time to finish after it closes</small></label>
    <label>Game version<input name=game_version placeholder="any" value="{e(s.get('game_version') or '')}">
      <small>e.g. v0.113.1, blank = any</small></label>
  </div>
  <fieldset><legend>Days (UTC)</legend>{weekday_checkboxes(s.get('weekdays', '0123456'))}</fieldset>
  <fieldset><legend>Character pool (one is picked per window)</legend>{character_checkboxes(pool)}</fieldset>
  <label class=chk><input type=checkbox name=enabled value=1{' checked' if s.get('enabled', 1) else ''}>Enabled</label>"""

    def dashboard(csrf: str, msg: str, err: str) -> str:
        now = clock()
        win.ensure_recurring_windows(db, settings, now)
        hidden = f"<input type=hidden name=csrf value='{csrf}'>"

        schedules = [dict(r) for r in db.all("SELECT * FROM schedules ORDER BY start_time, slug")]
        sched_rows = ""
        for s in schedules:
            days = "Every day" if s["weekdays"] == "0123456" else ", ".join(win.WEEKDAY_NAMES[int(d)] for d in s["weekdays"])
            pool = "All" if not s["characters"] else ", ".join(_pretty_character(c) for c in s["characters"].split(","))
            sched_rows += f"""
<tr class="{'' if s['enabled'] else 'muted'}">
  <td><b>{e(s['slug'])}</b>{'' if s['enabled'] else ' <span class=tag>disabled</span>'}</td>
  <td><span data-utc-label="{e(s['start_time'])}">{e(s['start_time'])} UTC</span></td>
  <td>{s['duration_minutes'] // 60}h {s['duration_minutes'] % 60:02d}m</td>
  <td>{days}</td><td>{e(pool)}</td><td>{e(s['game_version'] or 'any')}</td>
  <td class=actions>
    <details><summary>Edit</summary>
      <form method=post action="/admin/schedules/{s['id']}/update" class=card>{hidden}{schedule_fields(s)}
        <button>Save</button>
        <small>Saving regenerates this schedule's upcoming windows that have no runs (new seed and character).</small>
      </form>
    </details>
    <form method=post action="/admin/schedules/{s['id']}/delete" onsubmit="return confirm('Delete schedule {e(s['slug'])}? Upcoming windows without runs are removed.')">{hidden}<button class=danger>Delete</button></form>
  </td>
</tr>"""
        if not sched_rows:
            sched_rows = "<tr><td colspan=7 class=empty>No recurring schedules. Add one below.</td></tr>"

        rows = db.all(
            "SELECT w.*, s.slug AS schedule_slug,"
            " (SELECT COUNT(*) FROM runs r WHERE r.window_id = w.id) AS runs,"
            " (SELECT COUNT(*) FROM runs r WHERE r.window_id = w.id AND r.status = 'finished') AS finished"
            " FROM windows w LEFT JOIN schedules s ON s.id = w.schedule_id"
            " WHERE w.ends_at > ? ORDER BY w.starts_at LIMIT 100",
            (now - 7 * 24 * 3600,),
        )
        win_rows = ""
        for w in rows:
            state = "open" if w["starts_at"] <= now < w["ends_at"] else ("upcoming" if now < w["starts_at"] else "closed")
            source = f"recurring · {e(w['schedule_slug'])}" if w["kind"] == win.KIND_RECURRING and w["schedule_slug"] else (
                "recurring" if w["kind"] == win.KIND_RECURRING else "fixed")
            delete = (
                f"<form method=post action='/admin/windows/{e(w['id'])}/delete' onsubmit=\"return confirm('Delete window {e(w['id'])}?')\">"
                f"{hidden}<button class=danger>Delete</button></form>"
                if w["runs"] == 0 else ""
            )
            win_rows += f"""
<tr>
  <td><a href="/?window_id={e(w['id'])}">{e(w['id'])}</a></td><td><span class="tag {state}">{state}</span></td><td>{source}</td>
  <td><time data-ts={w['starts_at']}></time></td><td><time data-ts={w['ends_at']}></time></td>
  <td>{e(_pretty_character(w['character']))}</td><td>{e(w['game_version'] or 'any')}</td>
  <td class=n>{w['finished']}/{w['runs']}</td>
  <td><details><summary>show</summary><code>{e(w['seed'])}</code></details></td>
  <td class=actions>{delete}</td>
</tr>"""
        if not win_rows:
            win_rows = "<tr><td colspan=10 class=empty>No windows.</td></tr>"

        char_options = "<option value=''>Random</option>" + "".join(
            f"<option value='{e(c)}'>{e(_pretty_character(c))}</option>" for c in all_characters
        )
        body = f"""
<header><h1>Ranked admin</h1>
  <nav><a href="/">Leaderboard</a>
  <form method=post action=/admin/logout>{hidden}<button class=link>Log out</button></form></nav></header>
{f'<p class=ok>{e(msg)}</p>' if msg else ''}{f'<p class=err>{e(err)}</p>' if err else ''}
<p class=muted>Your timezone: <span class=tz></span>. Recurring schedules are defined in UTC; everything else is shown in your local time.</p>

<h2>Recurring schedules</h2>
<table><thead><tr><th>Name</th><th>Starts</th><th>Open for</th><th>Days</th><th>Characters</th><th>Version</th><th></th></tr></thead>
<tbody>{sched_rows}</tbody></table>
<details class=add><summary>Add recurring schedule</summary>
  <form method=post action=/admin/schedules class=card>{hidden}
    <label>Name<input name=slug required pattern="[a-z0-9][a-z0-9-]{{0,31}}" placeholder="e.g. weekend">
      <small>lowercase; window ids become name-YYYY-MM-DD</small></label>
    {schedule_fields(None)}
    <button>Create schedule</button>
  </form>
</details>

<h2>Add fixed window</h2>
<form method=post action=/admin/windows class=card data-local-times>{hidden}
  <div class=grid>
    <label>Starts (your time)<input type=datetime-local name=start_local required></label>
    <label>Ends (your time)<input type=datetime-local name=end_local required></label>
    <label>Character<select name=character>{char_options}</select></label>
    <label>Submit grace (minutes)<input type=number name=submit_grace_minutes min=0 max=10080 value="{settings.submit_grace_minutes}" required></label>
    <label>Game version<input name=game_version placeholder="any" value="{e(settings.game_version)}"></label>
    <label>Window id<input name=id placeholder="auto" pattern="[A-Za-z0-9_-]{{1,64}}"></label>
    <label>Seed<input name=seed placeholder="random" pattern="[0-9A-HJ-NP-Za-hj-np-z]{{4,32}}"></label>
  </div>
  <input type=hidden name=starts_at><input type=hidden name=ends_at>
  <button>Create window</button>
</form>

<h2>Windows</h2>
<p class=muted>Open, upcoming, and the last 7 days. Recurring windows are created up to a day ahead. Windows with runs can't be deleted.</p>
<table><thead><tr><th>Id</th><th>State</th><th>Source</th><th>Starts</th><th>Ends</th><th>Character</th><th>Version</th>
<th class=n>Done/runs</th><th>Seed</th><th></th></tr></thead><tbody>{win_rows}</tbody></table>
"""
        return page("Ranked admin", body)

    def page(title: str, body: str) -> str:
        return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content=noindex><title>{e(title)}</title>
<style>{ADMIN_CSS}</style></head><body>{body}<script>{ADMIN_JS}</script></body></html>"""


ADMIN_CSS = """
:root{color-scheme:light dark;--fg:#1d1b18;--bg:#f7f4ee;--card:#fffdf9;--muted:#6b645a;--line:#ddd5c8;--accent:#9c3d1f;--ok:#2f6b3a;--err:#a32020}
@media(prefers-color-scheme:dark){:root{--fg:#ece6dc;--bg:#191714;--card:#221f1b;--muted:#a39a8c;--line:#3a342c;--accent:#e0875e;--ok:#7fc28c;--err:#ff8a80}}
*{box-sizing:border-box} body{font:15px/1.5 system-ui,sans-serif;color:var(--fg);background:var(--bg);max-width:1180px;margin:2rem auto;padding:0 1rem}
header{display:flex;justify-content:space-between;align-items:center} nav{display:flex;gap:1rem;align-items:center}
h1{font-size:1.5rem;margin:.2rem 0} h2{font-size:1.15rem;margin:2rem 0 .5rem}
a{color:var(--accent)} code{font-size:.9em}
table{width:100%;border-collapse:collapse} th,td{padding:.4rem .5rem;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)} .n{text-align:right;font-variant-numeric:tabular-nums}
.empty,.muted{color:var(--muted)} tr.muted td{opacity:.6}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:1rem;margin:.6rem 0;display:flex;flex-direction:column;gap:.7rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:.7rem}
label{display:flex;flex-direction:column;gap:.2rem;font-size:.85rem;color:var(--muted)} label small{font-size:.75rem}
label.chk{flex-direction:row;align-items:center;gap:.35rem;color:var(--fg);display:inline-flex;margin-right:.9rem}
input,select{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:.35rem .5rem}
input[type=checkbox]{padding:0} fieldset{border:1px solid var(--line);border-radius:6px;padding:.5rem .7rem} legend{font-size:.8rem;color:var(--muted)}
button{font:inherit;cursor:pointer;background:var(--accent);color:#fff;border:0;border-radius:6px;padding:.4rem .9rem;align-self:flex-start}
button.danger{background:transparent;color:var(--err);border:1px solid var(--err);padding:.2rem .6rem}
button.link{background:none;color:var(--accent);padding:0;text-decoration:underline}
.actions{display:flex;gap:.5rem;align-items:flex-start} .actions details[open]{flex-basis:100%}
details summary{cursor:pointer;color:var(--accent)} details.add{margin-top:.6rem}
.tag{font-size:.75rem;border:1px solid var(--line);border-radius:999px;padding:0 .45rem}
.tag.open{color:var(--ok);border-color:var(--ok)} .tag.upcoming{color:var(--accent);border-color:var(--accent)}
.ok{color:var(--ok)} .err{color:var(--err)}
"""

ADMIN_JS = """
const fmt = {weekday:'short', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'};
document.querySelectorAll('time[data-ts]').forEach(el => {
  const d = new Date(el.dataset.ts * 1000); el.textContent = d.toLocaleString([], fmt); el.title = d.toISOString();
});
document.querySelectorAll('.tz').forEach(el => el.textContent = Intl.DateTimeFormat().resolvedOptions().timeZone);
function utcToLocal(hm) {
  const [h, m] = hm.split(':'); const d = new Date(); d.setUTCHours(+h, +m, 0, 0);
  return d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
}
document.querySelectorAll('[data-utc-label]').forEach(el => el.textContent = `${el.dataset.utcLabel} UTC (${utcToLocal(el.dataset.utcLabel)} local)`);
document.querySelectorAll('input[data-utc-time]').forEach(inp => {
  const hint = inp.parentElement.querySelector('.local-hint');
  const upd = () => { if (inp.value) hint.textContent = `= ${utcToLocal(inp.value)} your time`; };
  inp.addEventListener('input', upd); upd();
});
document.querySelectorAll('form[data-local-times]').forEach(form => form.addEventListener('submit', () => {
  for (const [src, dst] of [['start_local', 'starts_at'], ['end_local', 'ends_at']]) {
    const v = form.elements[src].value;
    form.elements[dst].value = v ? Math.floor(new Date(v).getTime() / 1000) : '';
  }
}));
"""
