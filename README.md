# STS2 Ranked Mode

A Slay the Spire 2 mod plus a small server that run **time-windowed ranked Dread runs**:

- A **Ranked** button in the singleplayer menu works only while a ranked window is open.
- Every player in a window plays **Dread mode (Ascension 10) as the same server-assigned character on the same
  secret seed**. The server sends the seed only when they press Embark.
- **One attempt** per player per window.
- **The seed is hidden** in the run UI, in run history and in the game log.
- **Ranked runs can't be continued from a save.** You can only abandon them, which submits the result.
- At the end of the run (victory, death or abandon), the **full run data is sent to the server**. The server
  checks it and ranks it.

Built against game **v0.113.1** (Early Access). Patches target `sts2.dll` internals and may need updating
after game patches.

```
mod/RankedMode/      C# mod (Harmony patches, uses the game's built-in mod loader)
server/              FastAPI + SQLite server (windows, seeds, submissions, leaderboard page)
dist/                packaged mod zip (mod/package.sh)
```

## How a ranked run works

```
Ranked button ──GET /status──► window open? not played yet?
     │                          (returns the window's assigned character)
     ▼
Dread screen in "ranked" mode: a banner shows the window, character and countdown. Selection is locked to the
assigned character (even if not unlocked yet), and the rotating-character option is hidden. The player's
Dread preferences are left alone.
     │ Embark
     ▼
POST /runs/start ──► uses up the attempt, returns the secret seed → set on the lobby → Dread run begins
     │
     ▼
RunManager.OnEnded / main-menu Abandon ──► POST /runs/{id}/finish  (full SerializableRun JSON)
```

The seed is only fetched at Embark, so a player can't open the screen, read the seed, back out and scout
it in Custom mode.

## Installing the mod

You need the .NET 9 SDK. It was installed for this machine at `~/.dotnet`: `export PATH=~/.dotnet:$PATH DOTNET_ROOT=~/.dotnet`.

```bash
cd mod/RankedMode
dotnet build -c Release -t:Install      # copies RankedMode.dll + RankedMode.json to <game>/mods/RankedMode/
# or: ../package.sh  → dist/RankedMode-0.1.0.zip, which players unzip into <game>/mods/
```

On first launch the game asks you to agree to loading mods. Note that **when STS2 first runs modded, it
copies your saves into a separate modded profile**. That is base-game behaviour, not something this mod does.

The mod talks to **https://spire.ursumajor.com** by default. To use a different server (for example a
local one during development), create `~/.local/share/SlayTheSpire2/ranked_mode/config.json`, or set the
`RANKED_SERVER_URL` environment variable:

```json
{ "server_url": "http://127.0.0.1:8000" }
```

The Ranked button is in **Singleplayer** next to Daily (it uses the Dread icon). The base game hides Singleplayer while a run save
exists, so finish or abandon your current run first.

Local mod state is in `~/.local/share/SlayTheSpire2/ranked_mode/state.json`: your player token, the
active ranked run, results waiting to upload, and the ranked seeds you've played.

## Running the server

The server reads its settings from environment variables and from `server/.env`
(copy `server/.env.example`). Relative `RANKED_DB_PATH` values are inside `server/`.

### Windows (the live server)

From the repo folder in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File server\windows\setup.ps1        # venv + packages + .env with a new admin key
server\.venv\Scripts\python.exe -m ranked_server                       # test run; Ctrl+C to stop
```

Run it at boot as a scheduled task. It runs as SYSTEM, needs no login, restarts if it crashes, and logs
to `server\server.log`. Use an **Administrator** PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File server\windows\install-task.ps1
powershell -ExecutionPolicy Bypass -File server\windows\update.ps1       # later: git pull + restart
```

### Linux

```bash
cd server && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env    # set RANKED_ADMIN_KEY
.venv/bin/python -m ranked_server            # or run it as a systemd service
```

| env var | default | meaning |
|---|---|---|
| `RANKED_DB_PATH` | `ranked.db` | SQLite file |
| `RANKED_ADMIN_KEY` | *(empty: admin disabled)* | password for `/admin` and the `X-Admin-Key` header |
| `RANKED_COOKIE_SECURE` | `1` | set `0` only for local http testing of `/admin` |
| `RANKED_DAILY_START` | `18:00` | **first run only:** creates the initial `daily` schedule (UTC `HH:MM`); empty = none. Manage schedules at `/admin` afterwards |
| `RANKED_DAILY_DURATION_MINUTES` | `240` | how long you can **start** a run |
| `RANKED_SUBMIT_GRACE_MINUTES` | `180` | how long after close a started run may still be **submitted** |
| `RANKED_GAME_VERSION` | *(empty)* | pin auto-created daily windows to a game version, e.g. `v0.113.1` |
| `RANKED_CHARACTERS` | all six | comma-separated character ids a window is randomly assigned from, e.g. `CHARACTER.IRONCLAD,CHARACTER.SILENT` |

In production, put it behind HTTPS, because player tokens travel in the `Authorization` header. The live
server runs behind a Cloudflare tunnel (`spire.ursumajor.com` → `http://localhost:8000`). Keep Cloudflare
Access, Bot Fight Mode and challenge rules off `/api/*`, because the mod can't solve challenges.

Home page `GET /` lists runs as open, upcoming or completed. Each run's leaderboard is at `GET /windows/<id>`. A window's seed is shown publicly once its
submit deadline has passed.

### Admin panel

Open **https://spire.ursumajor.com/admin** and log in with `RANKED_ADMIN_KEY`. From there you can:

- **Recurring schedules:** start time (UTC, with your local equivalent shown), how long it's open, submit
  grace, weekdays, the character pool (one is drawn per window), an optional game-version pin, and on/off.
  Saving a schedule regenerates its upcoming windows that have no runs yet. Windows that are open or already
  have runs are never touched.
- **Fixed windows:** start and end in your local time, character (or random), optional seed, version and id.
- **Windows list:** open, upcoming and last-7-days windows with source, character, runs and a hidden seed
  reveal. Windows without runs can be deleted.

The first time the server starts on a database, `RANKED_DAILY_*` creates a schedule called `daily`. After
that the database is the source of truth and those env vars are ignored.

Security: the session is a signed, HttpOnly, Secure, SameSite=Strict cookie that lasts 12 hours, every form
has a CSRF token, and failed logins are limited to 5 per IP per 15 minutes. For an extra layer you can put
a Cloudflare Access policy on `/admin*` only. Don't put one on `/api/*`.

### API

| | | |
|---|---|---|
| `GET /api/v1/status` | optional auth | server time, current window (with `setup_key`, and `your_run` if authed), next window |
| `POST /api/v1/register` | – | `{steam_id, name}` → `{token}` (409 if that Steam ID is already registered) |
| `POST /api/v1/runs/start` | bearer | `{window_id, game_version, mod_version}` → `{run_id, seed}`. Uses up the attempt. |
| `POST /api/v1/runs/{id}/finish` | bearer | `{victory, abandoned, score, game_version, mod_version, run}` → `{valid, flags, rank}` |
| `GET /api/v1/leaderboard?window_id=` | – | ranked entries for a window (default: latest started) |
| `GET /api/v1/windows` | – | recent windows |
| `POST /api/v1/admin/windows` | admin | `{starts_at, ends_at, id?, seed?, character?, game_version?, submit_grace_minutes?}` (no overlaps) |
| `GET /api/v1/admin/windows`, `DELETE /api/v1/admin/windows/{id}` | admin | list / delete (only if no runs) |
| `GET /api/v1/admin/runs?window_id=&flagged_only=` , `GET /api/v1/admin/runs/{id}/json` | admin | review submissions |

Example of an ad-hoc tournament window:

```bash
curl -X POST localhost:8000/api/v1/admin/windows -H 'X-Admin-Key: change-me' -H 'content-type: application/json' \
  -d "{\"id\":\"friday-cup\",\"starts_at\":$(date -d 'fri 20:00' +%s),\"ends_at\":$(date -d 'fri 22:00' +%s)}"
```

Tests: `.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest`

## Scoring and server-side checks

Score is the game's own daily score (`ScoreUtility.CalculateDailyScore`). In order of priority it counts
victory, then floors reached, then badges, then a faster time. The server **recomputes victory, floors and
time from the run data**. Only the badge count comes from the client.

A run is rejected outright (422) if its seed isn't the window's seed, if it isn't singleplayer, if it isn't
Dread mode, or if it wasn't played as the window's character. Otherwise it's stored. These flags keep it off the leaderboard so an admin can
review it:

| flag | meaning |
|---|---|
| `reloaded` | `num_reloads > 0`: the save was loaded at some point |
| `start_time_mismatch` | the run's start time doesn't match when the seed was issued |
| `run_time_exceeds_wall_clock` | in-game time is longer than the real time since the seed was issued |
| `late` | submitted after the window's submit deadline |
| `version_mismatch` | game version differs from the window's pinned version |
| `score_mismatch` | client score disagrees with the recomputed victory, floors or time |
| `custom_game`, `debug_command` | self-explanatory |

## Anti-cheat: what this does and doesn't stop

All of this runs on the player's machine, so it raises the effort needed to cheat but can't prevent it.

**Stopped or caught:**
- seeing or sharing the seed casually (UI, run history and log are redacted);
- scouting the seed before your attempt (it's only revealed at Embark, and Embark uses up the attempt);
- save-scumming (Continue is blocked, and any reload shows up as `num_reloads`);
- playing outside the window or retrying;
- uploading a different seed;
- inflating floors or victory.

**Not stopped:**
- someone who has already played sharing the seed with a friend before the friend starts. The seed is in
  `current_run.save` and in memory. Mitigate with short windows;
- a modified client that fakes an entire run JSON;
- impersonation. Registration trusts the Steam ID the client sends, and the first registrant wins.
  Upgrading to Steam OpenID or Discord linking is the natural next step.

**Fairness caveat:** the acts and card pools depend on each player's unlocks
(`ActModel.GetRandomList(rng, unlockState)`). Players with different unlock progress can get somewhat
different runs even on the same seed and character.

**Dread side effects:** a ranked run is a real Dread run, so winning or losing it counts toward that
character's Dread win streak in your profile.

**Stronger verification later:** the game writes replays (`profile1/replays/latest.mcr`,
`MegaCrit.Sts2.Core.Multiplayer.Replay`). Uploading the replay and checking it on the server would close
the "fake JSON" gap, but it needs a headless copy of the game.

## After a game update

```bash
dotnet build -c Release mod/RankedMode && dotnet run -c Release --project mod/PatchCheck
```

This applies every Harmony patch headlessly against the installed `sts2.dll` and lists any patch whose target
moved. Then bump `min_game_version` and, if you pin windows, `RANKED_GAME_VERSION`.

## Manual in-game test checklist

The patches were checked headlessly: all 19 Harmony targets resolve against v0.113.1. The UI flow still
needs a real playthrough:

1. Start the server with `RANKED_DAILY_START` set to a few minutes ago. Set `server_url` to it.
2. With no run save, go to Singleplayer and check the **Ranked** button appears and the row of buttons fits.
3. Ranked opens the Dread screen with the RANKED banner and countdown. Only the assigned character can be selected, and the rotation tickbox is hidden.
4. Back out, then reopen. It should still be allowed, because no attempt is used before Embark.
5. Embark: the run starts. The bottom-corner seed label (version-info hotkey) shows `RANKED`, and `godot.log` shows `<ranked-seed>`.
6. Save & quit: on the main menu Continue is disabled and clicking it shows the popup. Abandon: a toast shows "submitted", and the run appears on `/`.
7. Ranked again in the same window: "Already played".
8. Run history: the seed shows `HIDDEN (ranked)`.
9. Normal Dread still lets you pick any character and shows the rotation option.
10. Close the window (admin API, or wait), then press Ranked: "Ranked is closed. Next window opens …".
