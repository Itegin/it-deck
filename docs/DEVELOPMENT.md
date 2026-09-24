# IT-Deck Development Guide

This is the practical guide: how the pieces fit, how to run and test them, how
to debug them, and how to extend them. It is written to be re-read months from
now.

- **Why things are the way they are** →
  [`ARCHITECTURE.md`](ARCHITECTURE.md), which records the decisions.
- **Every internal detail**: schema columns, each fixup's history, theme
  contrast numbers, the standalone launcher's Windows specifics →
  [`IT-Deck_Tech_Reference.md`](IT-Deck_Tech_Reference.md). This guide links
  into it rather than repeating it.
- **What changed and when** → [`../CHANGELOG.md`](../CHANGELOG.md).

---

## 1. Quick start

### What IT-Deck is

It turns an old iPhone (or any phone) into a Stream Deck-style control panel
for a Windows PC. You tap a tile on the phone, and the PC mutes the mic, opens
a program, switches speakers, starts the VPN and so on. It is self-hosted and
single-user, and runs on your own Wi-Fi.

### Architecture in two sentences

A small **backend** (Python/FastAPI plus SQLite) serves the web app and
relays messages. The **phone** (a static web page) and the **Windows agent**
(a Python process on the PC) each hold one WebSocket to it.

```
 iPhone (browser)          Windows PC
 ┌────────────┐            ┌──────────────────────────────────────┐
 │ Dashboard  │── Wi-Fi ──▶│ ITDeck.exe                           │
 │ index.html │  WebSocket │  ├─ backend  (FastAPI + SQLite)      │
 └────────────┘            │  │     ▲ WebSocket (127.0.0.1)       │
 Studio (PC browser) ─────▶│  └─ agent    (mutes, launches, ...)  │
                           └──────────────────────────────────────┘
```

### Two ways to run it

| | **Standalone** (main path) | **Legacy** (Docker server) |
|---|---|---|
| What | one `ITDeck.exe` holding backend, agent and frontend | backend in Docker on a Linux box ("Athlon"); the agent runs separately on the PC |
| Port | `49732` (from `config.env`) | `8000` |
| Config | `%LOCALAPPDATA%\IT-Deck\config.env` | `.env` in the repo root (server) and `agents/windows/.env` (PC) |
| Database | `%LOCALAPPDATA%\IT-Deck\controlhub.db` | `./data/controlhub.db` on the server |
| Logs | `%LOCALAPPDATA%\IT-Deck\logs\{launcher,backend,agent}.log` | `docker compose logs backend`; the agent's console window |

### Run standalone (Windows)

```powershell
# 1. Build the exe (installs Python 3.12 via winget if needed, creates a venv).
powershell -ExecutionPolicy Bypass -File standalone\build.ps1

# 2. Run it. A window opens with a QR code and the phone link.
.\standalone\dist\ITDeck.exe
```

**Stop it** with the **Quit** button in the IT-Deck window. Closing it any
other way (Task Manager included) still takes the backend and agent down with
it, because both live in a kill-on-close job object.

For development without building, you can run it straight from source:

```powershell
python -m venv standalone\venv
standalone\venv\Scripts\pip install -r standalone\requirements.txt
standalone\venv\Scripts\python standalone\launcher.py
```

### Run the backend alone (any OS, for frontend or backend work)

```bash
# One-time: a virtualenv with the backend and test dependencies.
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt pillow psutil

# Start it. The env vars replace what the launcher or .env would provide.
cd backend
AGENT_TOKEN=dev CLIENT_TOKEN=dev ITDECK_DATA_DIR=../data \
ITDECK_FRONTEND_DIR=../frontend \
../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open `http://localhost:8000/?token=dev` for the Dashboard and
`http://localhost:8000/studio.html` for Studio (its token is `dev`). With no
agent connected, the tiles show as offline. That is expected.

### Run legacy Docker (Linux server)

```bash
cp .env.example .env          # then edit AGENT_TOKEN / CLIENT_TOKEN
docker compose up -d --build  # build from ./backend and start
./check.sh                    # a green/red status summary (in Russian)
docker compose down           # stop
```

### Check that everything works

1. **Backend up?** `curl http://localhost:<port>/health` should print `{"status":"ok"}`.
2. **Agent connected?** Look for `Agent 'windows' connected` in `backend.log`
   (or in `docker compose logs backend`). If the agent is down, the phone
   greys its tiles, or shows the full-screen clock if the deck has a clock
   widget.
3. **Phone connected?** Look for `Client connected` in the same log.
4. **End to end:** tap **Mic** on the phone. The tile turns red and the PC's
   microphone is muted.

---

## 2. Architecture

### Components

| Component | Where | Responsibility |
|---|---|---|
| Dashboard | `frontend/index.html` + `js/` | The phone UI. It draws tiles, sends presses and shows live state. It never writes the catalog. |
| Studio | `frontend/studio.html` + `js/studio*.js` | The desktop editor. It creates, edits and moves tiles over REST with `X-Agent-Token`. |
| Backend | `backend/app/` | The source of truth for the catalog (SQLite). It relays commands to the agent, fans out results and state to phones, and enforces the 5-second rule. |
| Agent | `agents/windows/` | Runs commands on the PC (audio, launching, URLs, screenshot) and reports state once a second. |
| Launcher | `standalone/launcher.py` | Standalone only. It owns `config.env`, spawns and supervises backend and agent, shows the info window, and handles uninstall and Windows shutdown. |

Dependencies point one way. Phone → backend ← agent. The phone and the agent
never talk to each other directly, and the backend never initiates anything
toward the PC except relaying a command.

### Request flow (a tap)

```
 phone                       backend                               agent
   │  {"cmd":"execute",         │                                    │
   │   "item_id":7,"req_id":R}  │                                    │
   ├───────────────────────────▶│ look up item 7 (SQLite)            │
   │                            │ agent connected? no ─▶ "agent offline" (at once)
   │                            │ {"cmd":"launch_app","params":{…},  │
   │                            │  "req_id":R,"item_id":7} ─────────▶│ run handler
   │                            │ start 5 s timer for R              │
   │                            │◀──── {"type":"result","req_id":R,  │
   │                            │       "status":"ok"} ──────────────┤
   │◀── result (to EVERY phone) ┤ cancel timer R                     │
   │ ws.js matches R ─▶ tile    │                                    │
   │ flashes ok / shows error   │  (no reply in 5 s ─▶ "timeout")    │
```

### State flow (the tiles' live colours)

```
 agent poller (1/s) ─▶ {"type":"state","data":{"mic.muted":true,…}}
 backend: namespace ("windows:mic.muted"), diff against last snapshot
          ─▶ broadcast ONLY the changed keys to every phone
 phone:   updateTileState() colours every tile whose data-state-key matches,
          and remembers the value so a redraw can re-apply it
```

### Catalog flow (a Studio edit)

```
 Studio ── PUT /api/items/7 (X-Agent-Token) ─▶ backend validates + writes SQLite
        ◀─ 200 + the item                     └─▶ broadcast {"type":"workspace_update"}
 phone:  GET /api/workspaces ─▶ full redraw (state and offline marks re-applied)
```

---

## 3. Repository structure

```
backend/                 FastAPI app (Docker image = this folder)
  app/main.py            app, lifespan (startup migrations), static mount (last!)
  app/db.py              schema, seed, startup fixups, schema_migration
  app/models.py          read helpers: workspaces+items, one item, press count
  app/auth.py            the two shared-secret checks (hmac.compare_digest)
  app/config_file.py     line-preserving writer for the launcher's config.env
  app/state.py           in-memory state snapshot + diff
  app/pending.py         per-req_id 5 s timeout timers
  app/agent_requests.py  HTTP request/response to the agent (futures)
  app/api/*.py           REST routes: items, workspaces (+ deck export/import), settings,
                         access (tokens), agents, widgets, screenshot
  app/ws/protocol.py     shared: hello handshake, frame parsing, close codes
  app/ws/agent.py        /ws/agent
  app/ws/client.py       /ws/client (+ ALLOWED_OVERRIDES)
  app/ws/hub.py          connection registry + broadcast
frontend/                static PWA, no build step
  index.html, studio.html
  js/app.js              Dashboard boot and wiring
  js/ws.js               Dashboard socket, reconnect, req_id bookkeeping
  js/render.js           all Dashboard DOM, ICONS, tile state, clock takeover
  js/tile-catalog.js     what tile types exist (Studio builds its editor from it)
  js/widgets/            widget registry (mount + live-state API), clock/weather, PC load
  js/contextmenu.js      the phone's bottom sheet: long-press menu, "Run?", Send text
  js/swipe.js            swipe between decks
  js/theme.js            theme + light/dark mode
  js/studio*.js          Studio (controller, inspector, preview, guide, access,
                         what's new, decks, i18n); js/dom.js is their element builder
  whats-new.json         the "What's new" notes, one entry per public version
  templates/*.json       deck templates Studio offers
  css/                   base, grid (Dashboard only), button, themes, widgets, studio
agents/windows/          the agent
  agent.py               connect, reconnect, HANDLERS table, singleton mutex
  dispatch.py            frame → handler → result (pure, testable anywhere)
  config_file.py         re-reads AGENT_TOKEN from config.env on every connect
  poller.py              state readers, 1 s loop (paused while nobody watches)
  handlers/              audio, process (launch/url/vpn/force stop), apps, screenshot,
                         input (hotkeys/media keys), power, clipboard, system (PC load)
  tools/SoundVolumeView.exe  audio device switching (NirSoft)
standalone/              launcher.py, build.ps1, requirements.txt
tests/                   pytest (backend, ws, db, access, decks, agent, launcher,
                         release) + node --test (frontend, keep-in-step checks)
scripts/check_release.py the gate a manual release runs first
docs/                    this guide, ARCHITECTURE.md, the Tech Reference
docker-compose.yml, deploy.sh, check.sh, backup.sh, ansible/   legacy server path
.github/workflows/       ci.yml (tests), release.yml (exe on tag), build.yml (GHCR image)
```

`.claude/` and `.agents/` hold Claude Code tooling (hooks, skills). They are
not part of the app.

---

## 4. Frontend architecture

**No framework and no build step.** It uses ES modules loaded straight from
disk. The backend serves them with `Cache-Control: no-cache`, so an update is
picked up on the next load (the ETag makes that a cheap 304).

### State (all module-level, all in the page)

| State | Where | Lifetime |
|---|---|---|
| Last reported agent state | `render.js` `knownState` | page; re-applied after each redraw |
| Offline agents, connection down | `render.js` `offlineAgents`, `connectionDown` | page; re-applied after each redraw |
| In-flight commands | `ws.js` `inFlight` (req_id → item), `inFlightPerItem` | until result or 8 s backstop |
| Press feedback timers | `render.js` `commandTimers` | per item; re-query tiles when they fire |
| Mounted widgets | `widgets/index.js` `active` | until `destroyWidgets()` before a grid wipe |
| Widget state subscriptions | `widgets/index.js` `stateListeners` | removed with their widget |
| Decks from the last fetch, the one on screen | `app.js` `deckList`, `currentDeckId` | page; what a swipe moves through |

**Persistence** (`localStorage`, always wrapped in try/catch because
it throws when storage is blocked):

| Key | Purpose |
|---|---|
| `itdeck.client_token` | the phone's CLIENT_TOKEN, from `?token=` or a prompt |
| `itdeck:workspaceId` | which deck this phone shows |
| `itdeck:theme`, `itdeck:mode` | cache for the inline boot script (no theme flash); the server value wins a moment later |
| `itdeck:onboarded` | first-run tour seen |
| `itdeck:studio-guide-seen` | Studio guide seen |
| `itdeck.agent_token` | Studio's copy of the Studio (agent) token |
| `itdeck:whats-new-seen` | the newest "What's new" version Studio has shown |

### Rendering

`renderWorkspace()` wipes and redraws the whole grid. That happens only on the
first load and on `workspace_update`, never per state tick. The order inside
it matters:

1. `destroyWidgets()`, then wipe the grid.
2. Append every tile.
3. Apply ink to every tile in one batch. Reads come after all writes, so this
   costs one style recalculation.
4. Re-apply `knownState`.
5. Mount the widgets. They need a sized, attached tile.
6. Re-apply the offline greying and the clock takeover.

State updates are incremental. `updateTileState()` touches only the tiles
whose `data-state-key` changed.

### Events

- Taps use pointer events plus `longpress.js` (500 ms). A long press opens the
  Force Stop menu. A pointer that moved more than 10 px is a drag, not a tap.
- `app.js` `handleTileTap` decides what a tap does:
  - a tile with `params.confirm` gets the Run / Cancel sheet first;
  - a Send-text tile opens its text sheet;
  - everything else executes straight away.

  All sheets are one component (`contextmenu.js` `openSheet`).
- A horizontal swipe on the deck (touch or pen, at least 60 px, clearly
  sideways, under 0.8 s) moves to the next or previous deck. The dots under
  the header do the same.
- The slider handles pointer drag and the arrow keys.
- The empty `touchstart` listener on `body` is required for iOS `:active`.
  Keep it.

### Talking to the backend

- **REST** (`api.js`): `GET /api/workspaces` and `GET/PUT /api/settings`.
  `api.js` tags each failure (`unreachable`, `status`, `badReply`,
  `badParams`), and `app.js` turns each tag into its own actionable message.
- **WebSocket** (`ws.js`), in order:
  1. The token is resolved *before* the socket opens.
  2. The socket opens and sends `hello`.
  3. Any inbound frame means the hello was accepted.
  4. Reconnects use backoff: 1 s, 2 s and so on up to 30 s. A tab coming back
     into view, or the `online` event, skips the wait.
  5. A `4001` close drops the stored token. The user is re-asked once, and
     after that there is a single toast.

### Lifecycle and cleanup rules

- A widget's `mount()` returns `destroy()`, which must clear every timer,
  listener and fetch the widget started.
- Global listeners are added once at module load, never inside a render.
- Everything that is re-created on render is attached to elements the render
  wipes.

### Error handling

- A load failure shows a full-deck message naming the fix (`renderError`).
- A command failure shows a toast naming the tile and a red ring on the tile.
- Background widget failures (weather) are silent: the widget shows a dash
  and marks itself stale.

---

## 5. Backend architecture

**Routes** (full table in Tech Reference §4):

| Route | Auth | Purpose |
|---|---|---|
| `GET /health` | none | liveness probe |
| `GET /api/workspaces` | none | the catalog (the phone reads it) |
| `POST/PUT/DELETE /api/items[/{id}]`, `GET /api/items/{id}` | `X-Agent-Token` | Studio CRUD; validates JSON-object params, placement, dock rules |
| `POST /api/workspaces`, `POST /api/workspaces/{id}/compact` | `X-Agent-Token` | new deck, pack tiles |
| `GET /api/workspaces/{id}/export`, `POST /api/workspaces/import` | `X-Agent-Token` | a deck as a file (`{"format": "itdeck-deck", "version": 1, …}`); import always creates a new deck, validated all-or-nothing |
| `GET /api/settings`, `PUT /api/settings/theme\|mode` | none (deliberate) | shared theme / light-dark |
| `POST /api/agents/{name}/list_devices\|list_apps\|fetch_icon` | `X-Agent-Token` | Studio asks the agent, 5 s budget |
| `GET /api/widgets/weather` | none (phone) | Open-Meteo proxy with snapped-coordinate cache |
| `GET /api/widgets/geocode` | `X-Agent-Token` | city search for Studio |
| `GET/PUT /api/access` | `X-Agent-Token` | Studio's Access dialog: read the phone token, change either token (standalone only; `409` on Docker) |
| `POST /api/screenshot` | `X-Agent-Token` | retained, unused |
| `/ws/agent`, `/ws/client` | hello token | see §7 |

**Database.**
- SQLite in WAL mode, with one short-lived connection per call.
- Every connection sets `foreign_keys=ON` and `synchronous=NORMAL`.
- Tables: `workspace`, `item`, `setting`, `schema_migration`.
- Sync SQLite calls inside `async def` routes are accepted: it is a local
  file, a single user, and queries take well under a millisecond.

**Startup** (`main.py` `lifespan` → `run_startup_migrations()`):
1. `init_db` (the schema, plus additive columns).
2. `seed_if_empty`.
3. The `fixup_*` sequence, in a load-bearing order.

The rules for fixups are in [§15](#add-a-startup-data-migration-fixup).

**Validation.** Pydantic types come first. Then come explicit checks:
- `params` must be a JSON object;
- no explicit `null` for NOT NULL fields;
- placement must be inside the grid and not overlap another tile;
- dock slots must be in `0..DOCK_MAX-1`, hold a 1×1 action, and be free.

**Errors.** `HTTPException` returns 400/401/404/504 with a `detail` string.
Pydantic returns 422 lists.

**Lifecycle.**
- The hub and the state snapshot are process-level singletons, which is
  intentional (see ARCHITECTURE.md).
- State is not persisted; it rebuilds from the agent's next tick.

**Logging.**
- `controlhub.*` loggers log at INFO for connects, disconnects, state
  *changes*, one line per command result, and item writes.
- Per-frame logs are DEBUG.
- In uvicorn's access log, `?token=` is masked and successful `/health`
  probes are dropped.

---

## 6. Agent architecture

**Startup** (`agent.main`):
1. Take the `Global\ITDeckAgentSingleton` mutex. If another agent holds it,
   exit with code `3`; the launcher does not respawn that code.
2. Loop: connect, send `hello`, run, and reconnect on any error.

**Connection** (`agent.run`):
- `_receive_loop` answers commands.
- `poll_loop` sends state every second.
- Whichever ends first cancels the other.

**Reconnect.**
- Backoff runs 1 → 2 → 4 → … → 30 s.
- It resets to 1 s after a connection that lasted at least 10 s. A backend
  restart therefore gets a prompt retry, while a rejected token (closed
  instantly) keeps backing off instead of hammering.

**Commands** (`dispatch.py`):
1. The frame is parsed. Junk is skipped.
2. The handler is found in `HANDLERS` and called with `params`.
3. The result dict is sent back with the `req_id`.
4. An unknown command, bad params or a raising handler becomes an **error
   result**, never a dropped connection.

**Handlers** are synchronous and run on the event loop. That is deliberate:
COM (pycaw) must stay on this thread (see ARCHITECTURE.md). Each has its own
budget under 5 s:
- launches: 2 s, then detached;
- device export: 4 s;
- switch steps: 2 s each.

**Launching** uses `CreateProcess` with breakaway flags, so programs outlive
IT-Deck. `os.startfile` is the fallback, for UAC elevation.

**Other commands:**
- `send_keys` / `media_key` press keys through `keybd_event`. `parse_keys`
  validates key names; some games ignore synthetic input by design.
- `power` answers first and acts 0.5 s later, because sleep and shutdown
  would otherwise swallow the reply.
- `clipboard_set` takes the text from `set_value`, checked and capped at
  100k characters.

**State readers** (`poller.READERS`) are read one by one. A failing key is
skipped and logged once, not every second.

- **They pause while no Dashboard is connected** (the `watchers` frame,
  §7). A PC nobody is looking at does no polling at all.
- **The VPN check** (`handlers/process.py` `ProcessWatch`):
  - it remembers the PID once the VPN is found, and checks that PID
    directly;
  - while the VPN isn't running, it walks the process list every 3 s
    instead of every second;
  - a launch from the deck triggers an immediate look.

**Priority.** The agent's own thread runs below normal, so a game in the
foreground always wins the CPU. Only the thread is lowered, not the
process: Windows passes a below-normal *process* class on to the programs
it starts, and a game launched from a tile must run at normal priority.

**Shutdown.**
- The `agent_shutdown` command replies `ok`, waits 0.2 s, then `os._exit(0)`.
  Exit code 0 is not respawned.
- In standalone, the launcher's job object kills the agent when IT-Deck exits.

---

## 7. WebSocket protocol

Both sockets carry JSON text frames. The **first frame is always a hello**
(within 5 s). A frame that is not a JSON object is logged and ignored; it does
not close the socket.

| Close code | Meaning | Client reaction |
|---|---|---|
| `4001` | hello missing or wrong, bad token, or token unset on server | phone forgets its stored token and asks again once |
| `4008` | no hello within 5 s | reconnect |
| `1012` | server restarting | reconnect |

### `/ws/agent`

```json
→ {"type": "hello", "agent": "windows", "version": "0.1.0", "token": "<AGENT_TOKEN>"}
← {"cmd": "launch_app", "item_type": "launch_app", "params": {"path": "wt.exe"}, "req_id": "1725800000000-k3n9x", "item_id": 7}
← {"cmd": "audio_volume_set", "params": {"device": "speaker", "value": 42}, "req_id": "…", "item_id": 5}
← {"cmd": "list_apps", "params": {}, "req_id": "api-1725800000000-a1b2c3d4"}
← {"type": "watchers", "active": false}   // no Dashboard connected: pause state reads
→ {"type": "result", "req_id": "1725800000000-k3n9x", "item_id": 7, "status": "ok"}
→ {"type": "result", "req_id": "…", "item_id": 7, "status": "error", "message": "…"}
→ {"type": "state", "data": {"mic.muted": false, "speaker.volume": 34, "vpn.running": false}}
```

- A second connection under the same agent name **replaces** the first, and
  phones are **not** told "offline" in between. The old socket is left for
  uvicorn's keepalive to reap. Give every PC its own `AGENT_NAME`: with two
  agents on one name, only the newer one receives commands, and both write
  state into the same keys.
- A result's extra keys ride along (`devices`, `apps`, `icon`).
- `watchers` tells the agent whether any Dashboard is connected.
  - When it is sent: to each agent as it connects, and again whenever the
    first phone (or PC browser) arrives or the last one leaves.
  - What the agent does: it reads audio and VPN state only while
    `active` is true, and reads at once when a viewer arrives.
  - What keeps working: commands, including Studio's queries, because they
    never pause.
  - Compatibility, both ways: an older agent ignores the frame, and a newer
    agent keeps polling against a backend that never sends it.

### `/ws/client` (the phone)

```json
→ {"type": "hello", "token": "<CLIENT_TOKEN>"}
← {"type": "state", "data": {"windows:mic.muted": true, …}}        // full snapshot, first
← {"type": "agent_status", "agent": "windows", "status": "online"} // one per referenced agent
→ {"cmd": "execute", "item_id": 7, "req_id": "1725800000000-k3n9x"}
→ {"cmd": "execute", "item_id": 7, "req_id": "…", "override_type": "force_stop"}
→ {"cmd": "set_value", "item_id": 5, "value": 42, "req_id": "…"}
← {"type": "result", "req_id": "…", "item_id": 7, "status": "ok"}
← {"type": "result", "req_id": "…", "status": "error", "message": "agent offline"}
← {"type": "state", "data": {"windows:speaker.volume": 50}}        // changes only
← {"type": "agent_status", "agent": "windows", "status": "offline"}
← {"type": "workspace_update"}                                     // re-fetch the catalog
← {"type": "settings_update", "settings": {"theme": "pastel"}}
```

**Request/response rules:**
- Every `req_id` gets exactly one result within 5 s: `ok`, `error`, or a
  synthetic `"timeout"`.
- Results are **broadcast to every phone**. Each phone keeps only the
  `req_id`s it sent.
- The phone's own 8 s backstop covers a socket that was down when the result
  went out.

The errors the backend itself produces:

| `message` | Meaning |
|---|---|
| `agent offline` | the agent isn't connected, or the send to it failed |
| `timeout` | sent, but no reply in 5 s (it may still have run) |
| `item not found` | a bad or missing `item_id` |
| `command not allowed` | an `override_type` other than `force_stop` |
| `tile settings are invalid` | the stored params are not a JSON object |
| `unknown command: <cmd>` | sent only to the socket that asked |

**Reconnect behaviour:**
- The phone backs off from 1 s up to 30 s, and reconnects at once on
  `visibilitychange` or `online`. It re-sends hello every time.
- The agent backs off the same way, with the 10 s-healthy reset.
- On reconnect the phone gets a full snapshot and the agent statuses again,
  so nothing is stale.

**Known limit:** there is no application-level heartbeat. uvicorn pings every
20 s and drops dead phones server-side. A phone whose socket is silently dead
(it can happen after iOS suspends the tab) only finds out when it next becomes
visible or the OS closes the socket. See ARCHITECTURE.md for why this is not
simply fixed.

---

## 8. Configuration

| Variable | Used by | Default | Notes |
|---|---|---|---|
| `AGENT_TOKEN` | backend, agent | standalone: `admin` | gates every write endpoint and `/ws/agent` |
| `CLIENT_TOKEN` | backend | standalone: `admin` | gates `/ws/client`; unset = every phone refused |
| `SERVER_PORT` | launcher, agent | standalone: `49732`, legacy `8000` | never hardcode; see Docker note below |
| `SERVER_IP` | agent | standalone: `127.0.0.1` | where the agent connects |
| `AGENT_NAME` | agent | `windows` | must match the items' `target` |
| `VPN_PROCESS_NAME`, `VPN_PATH` | agent | none | the launcher also derives the name from the VPN tile at agent spawn |
| `OUTPUT_DEVICE_PRIMARY/SECONDARY` | agent | none | Audio Switch fallback pair |
| `ITDECK_DATA_DIR` | backend, launcher | `/app/data` (Docker), `%LOCALAPPDATA%\IT-Deck` | DB, logs, screenshots |
| `ITDECK_FRONTEND_DIR` | backend | `frontend` (cwd-relative) | the launcher passes an absolute path |
| `ITDECK_CONFIG_FILE` | backend, agent | standalone: `%LOCALAPPDATA%\IT-Deck\config.env`; unset on Docker | where a token changed in Studio is saved; the agent re-reads its token from it on every reconnect |

- **Standalone** reads `config.env`, **load-if-exists**. It is written once
  and never regenerated, because that would break the phone's saved URL. Edit
  it by hand, then restart IT-Deck.
- **Legacy**:
  - Copy `.env.example` → `.env` on the server.
  - Copy `agents/windows/.env.example` → `agents/windows/.env` on the PC.
  - `.env` files are gitignored, and a Claude Code hook blocks editing them.
- **Secrets**:
  - Never commit a real token.
  - Only `.env.example` files with placeholders live in git.
  - `scripts/token_fingerprint.py` shows a token's length and hash prefix, so
    two machines can be compared without printing the secret.

---

## 9. Docker (legacy server)

| | |
|---|---|
| Service | `backend`, built from `./backend` (`python:3.11-slim`), also published as `ghcr.io/itegin/it-deck-backend` |
| Port | `8000:8000`. Keep it equal to the Dockerfile's `--port` and `.env`'s `SERVER_PORT`; change all three together |
| Volumes | `./data:/app/data` (DB, screenshots), `./frontend:/app/frontend` (**bind-mounted**: a frontend change is a `git pull`, no rebuild) |
| Healthcheck | the Dockerfile's `HEALTHCHECK` polls `/health` every 30 s; `docker ps` shows `healthy` |
| Restart | `unless-stopped` |
| Logs | json-file, 3 × 10 MB |
| Network | the default bridge; optional nginx TLS proxy from `ansible/` (proxies `/ws/` with upgrade headers, 1 h read timeout) |

**Startup order:**
- The agent and phones do not need the backend to be up first. Both retry
  with backoff until it is.
- Nothing relies on `depends_on`.

**Deploy:**
- Run `ssh athlon ./deploy.sh`. It pulls, rebuilds, waits for `/health` and
  checks that the code in the container matches the disk.
- For themes, deploy the backend **before** the frontend; for the `/ws/client`
  handshake it is the other way round (Tech Reference §5, §7).

**Troubleshooting:**
- A restart loop: run `docker compose logs --tail 100 backend` and look for an
  import error, or a missing `.env` (`env_file` is required).
- `unhealthy`: run `docker compose exec backend python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"`.

---

## 10. Development workflow

```
change → lint → test → (build) → run → verify → commit
```

```bash
# lint (the same ruff version a Claude Code hook runs on every edit)
ruff check backend agents standalone tests

# tests
.venv/bin/python -m pytest tests -q
node --test tests/frontend.test.mjs
for f in $(find frontend/js -name '*.js'); do node --check "$f"; done
```

- **Build**: the frontend has no build step; the backend has none unless you
  use Docker. The exe is `standalone/build.ps1` on Windows. If you change the
  PyInstaller call, change `release.yml` with it.
- **Run and verify**: see §1. If you change the agent, **restart the agent**
  (the Close Agent tile, then **Start the agent** in the IT-Deck window). A
  running agent keeps executing old code.
- **Commit**: use small, focused commits. The version lives in
  `standalone/launcher.py` `ITDECK_VERSION` and is bumped in the same commit
  as a `v*.*.*` tag. A tag only builds the exe (kept as a CI artifact);
  publishing a release is a manual workflow run (CONTRIBUTING.md,
  "Releasing").

---

## 11. Testing

| Suite | Runs on | Covers |
|---|---|---|
| `tests/test_backend.py` | any OS | dock rules, auth on agent queries and item writes, null/params validation, static caching headers, access-log filter |
| `tests/test_ws.py` | any OS | handshakes (4001), bad frames, unknown commands, offline answers, the override allowlist, state namespacing, **the agent reconnect race**, pending timers |
| `tests/test_db.py` | any OS | startup fixups on scratch DBs: fresh seed, user tiles named like old placeholders, deletions that must stick, a pre-`schema_migration` database |
| `tests/test_access.py` | any OS | token changes: auth, Docker read-only, validation, line-preserving `config.env` write, phones signed out |
| `tests/test_decks.py` | any OS | deck export → import round trip, refused files, all-or-nothing import, every template imports |
| `tests/test_agent.py` | any OS (needs Pillow, psutil) | URL allowlist, private-host check, dispatch, key parsing, power/clipboard, VPN watcher, PC-load readers, token re-read |
| `tests/test_launcher.py` | any OS | "What's new" selection and file shape, PINs, start-with-Windows (fake `winreg`) |
| `tests/test_release.py` | any OS | `scripts/check_release.py` |
| `tests/frontend.test.mjs` | Node 22 | URL cleaning, brand logos, no Russian services, EN/RU key parity, theme allowlists agree, **every catalog tile has a handler, an icon and its strings** |

CI (`.github/workflows/ci.yml`) runs all of it on Windows with Python 3.12 on
every push to `main` and on every PR.

**Not covered automatically** (these need Windows and real hardware): audio,
launching, the Tk window, the job object, and uninstall. Use the manual
checklist below.

### Manual checklist (after anything touching those areas)

1. Build the exe and launch it. The window shows a QR code; scan it with the
   phone.
2. Tap Mic (turns red, PC muted), Volume (drag), Audio Switch, a website tile
   and a program tile.
3. Long-press a program tile, choose Force Stop, and check the program closes.
4. Press Close Agent. The tiles grey out, or the clock takes over. Press
   **Start the agent** in the window, and the tiles come back.
5. Lock the phone, restart IT-Deck, then unlock. The deck recovers within
   about 2 s.
6. Edit a tile in Studio. The phone redraws and keeps the tile colours.
7. Delete a seeded tile (e.g. Screenshot) and restart IT-Deck. It stays
   deleted.
8. Rotate the phone. The quick-launch bar moves under the grid (portrait) or
   to the left (landscape).
9. Cycle the themes with the pill button, and switch light/dark in Studio.

### A live end-to-end check without Windows

The real agent loop can run on Linux with the Windows modules stubbed. That is
how this refactor was verified. The shape:
1. Start the backend with uvicorn.
2. Run a script that puts stub modules into `sys.modules` for `win32api`,
   `win32event`, `winerror`, `handlers.audio` and `handlers.screenshot`, then
   runs `agent.main()`.
3. Drive `/?token=…` with Playwright at iPhone size.

---

## 12. Debugging

### Backend does not start
- **Standalone**:
  - Read `logs\backend.log`.
  - A port that is already taken produces a dialog. Change `SERVER_PORT` in
    `config.env`, or quit the other copy of IT-Deck.
- **Docker**: see §9.
- **Import error after editing**: run
  `python -c "import app.main"` from `backend/` with the venv.

### The phone does not connect
1. Open `http://<pc-ip>:<port>/health` in the phone's browser. If that fails,
   it is the network: Windows Firewall (allow ITDeck.exe on private networks),
   a different Wi-Fi, or client isolation on the router.
2. A token prompt keeps appearing, or you see the "Token rejected" toast:
   `CLIENT_TOKEN` changed. Re-open the link from the IT-Deck window, which
   carries `?token=`.
3. It works over http but not https: make sure you are on current code (older
   builds hardcoded `ws://`).

### Agent offline (tiles greyed, or the clock shows)
- `agent.log` says `Another instance is already running`: a second agent is
  alive. Look in Task Manager for another ITDeck.exe or python agent.
- `agent.log` shows `Disconnected (…4001…)`: the agent token doesn't match the
  backend's. In legacy mode, compare them with `scripts/token_fingerprint.py`.
- The agent was stopped by the Close Agent tile: use **Start the agent** in
  the window.

### WebSocket keeps disconnecting
- Look at `backend.log` for connects and disconnects with timestamps.
- Behind nginx, check `/ws/` still has the `Upgrade`/`Connection` headers and
  `proxy_read_timeout 3600s`.
- A phone that drops every ~5 s is probably running an old cached `ws.js` that
  sends no hello (close `4008`). Hard-reload it.

### Commands do nothing
- The toast says why:
  - **agent offline**: see above.
  - **timeout**: the handler took over 5 s. It may still have run.
  - **unknown command**: the tile type isn't in the agent's `HANDLERS`; the
    agent may be older than the tile.
- `agent.log` has one `Received <cmd> (<req_id>)` line per press. If the line
  is missing, the command never reached the agent.
- For a program tile, check the path in Studio. An elevated (admin) program
  needs the UAC prompt answered on the PC.

### Docker container restart loop
See §9 troubleshooting: logs, then `.env`, then the `/health` probe from
inside the container.

### The UI is slow
- A full redraw only happens on Studio edits. If the deck redraws constantly,
  something is sending `workspace_update` in a loop; look at the backend log
  for item writes.
- The weather widget refreshes every 10 min and on wake. The backend serves
  it from a 10 min cache, so extra phones cost nothing upstream.
- On very old iPhones the Liquid Glass theme's `backdrop-filter` is the
  heaviest thing on screen. Flat is the cheapest theme.

---

## 13. Performance

| Decision | Why |
|---|---|
| The agent sends a full snapshot every 1 s; the backend diffs it | the agent stays stateless; phones only get the keys that changed |
| No full redraw on state, only per-key tile updates | a state tick costs a few class toggles |
| Ink computed in one pass after all tiles are appended | one style recalculation instead of one per tile |
| Widgets align timers to minute/second boundaries and pause on hidden | no drift, no wasted ticks |
| Weather: snapped coordinates, 10 min fresh, 6 h stale-on-failure, 64-entry cap | one upstream fetch per place per 10 min |
| SQLite in WAL + `synchronous=NORMAL` | readers never block the writer; no fsync per press |
| The `/api/workspaces` read does 2 queries, no N+1 | the whole catalog in one round trip |
| Per-frame logs at DEBUG, 5 MB log rotation, Docker log caps | logs stay useful and bounded |
| State reads pause while no Dashboard is connected (`watchers`) | an idle IT-Deck costs next to nothing |
| Backend process and agent thread below normal priority | games keep their frame rate when the CPU is contended |
| VPN state from a remembered PID, rescanned every 3 s when absent | no process-list walk every second |
| Static files with `no-cache` + ETag | an update applies on reload at 304 cost |

---

## 14. Security

**Trust boundaries:**

```
 LAN ──▶ backend (0.0.0.0:<port>)
          ├─ unauthenticated: /health, GET /api/workspaces, GET/PUT settings,
          │                   weather, static files
          ├─ CLIENT_TOKEN:    /ws/client  → may press existing tiles, drag sliders
          └─ AGENT_TOKEN:     all writes, Studio agent queries, /ws/agent
 agent ──▶ runs whatever the stored tiles say (paths, args, URLs)
```

- **The phone cannot run arbitrary commands.** It can only press existing
  tiles. `params` always come from the database, and `override_type` is
  limited to `force_stop`.
- **Studio can run anything.** Whoever holds `AGENT_TOKEN` can create a
  `launch_app` tile with any program and arguments and press it. That is
  arbitrary code execution on the PC, **by design**, because it is the whole
  feature.
- **Standalone defaults both tokens to `admin`.** It is a deliberate
  convenience for a single-user home network (CLAUDE.md). Anyone on the same
  Wi-Fi who knows this can use Studio. On a shared network, change them. There
  are three ways to do it:
  - **Studio → Access**: either token, with a "Random PIN" button;
  - **the IT-Deck window**: "New phone PIN";
  - **by hand** in `config.env`.

  A change applies at once, without a restart:
  - `PUT /api/access` writes `config.env` line by line (comments and other
    keys are kept) and updates the backend's environment;
  - a new phone token disconnects every phone with `4001`, and each asks for
    the new token once;
  - the agent picks up a new Studio token on its next connect.
- **`open_url` allowlist** (`agents/windows/handlers/process.py`):
  - allowed schemes: http(s), discord, tg, steam, spotify, zoommtg, slack,
    ms-settings;
  - no `file:`, `javascript:` or `ms-msdt:`;
  - the characters a shell would read specially are refused.
- **`fetch_icon`** only fetches http(s), refuses private/LAN addresses, and
  caps sizes.
- **No shell anywhere.** Every subprocess call takes an argument list with a
  timeout. PowerShell strings go through `_ps_quote()`.
- **Tokens:**
  - compared in constant time (`app/auth.py`);
  - masked in access logs;
  - stripped from the phone's URL after first use.
- **Exposure:**
  - The backend listens on all interfaces, with no TLS in standalone.
  - It is meant for a home LAN. Do not port-forward it to the internet.
- **XSS:** only module-authored SVG goes through `innerHTML`. User text goes
  through `textContent`, and site icons are checked against a data-URI regex.
- **Accepted risks** (documented, not fixed):
  - The unauthenticated weather endpoint can be used to keep the backend
    fetching from Open-Meteo.
  - `GET /api/workspaces` exposes tile params (program paths) to the LAN.

---

## 15. Extension guide

Each recipe lists every file that must change. CLAUDE.md's "keep in step" list
is the short form of these.

### Add a new tile type (a new button kind)

1. **Agent command**: if no existing handler fits, first follow
   [Add a new agent command](#add-a-new-agent-command-capability).
2. **Catalog**: add an entry to `CATALOG` in `frontend/js/tile-catalog.js`.
   Fill in:
   - `id`, `group`;
   - `kind: "action"`, `type` (= the handler name), `target: "windows"`;
   - `stateKey` (or `null`), `icon` (a key in `ICONS`);
   - `fixedParams`, `fields`.
3. **Strings**: add `type.<id>.name`, `type.<id>.desc` and any `field.<param>`
   to **both** `en` and `ru` in `js/studio-i18n.js`. A test enforces parity.
4. **Icon**: if you need a new glyph, add an SVG to `ICONS` in `js/render.js`.
   Stroke with `currentColor`.
5. **Live state** (optional): add a reader to `READERS` in
   `agents/windows/poller.py`, and set `stateKey` to that key.
6. Run the tests, restart the agent, and create the tile in Studio.

### Add a new agent command (capability)

1. Write `handle_<name>(params: dict) -> dict` in the right
   `agents/windows/handlers/*.py`.
   - Return `{"status": "ok", …}` or `{"status": "error", "message": "…"}`.
   - Catch your own exceptions. (`dispatch.py` catches the rest, but a
     specific message is better.)
   - Finish well under 5 s. Anything slower must detach, like `launch_app`.
   - Any subprocess gets an argument list and a `timeout=`.
2. Register it in `HANDLERS` in `agents/windows/agent.py`.
3. If a tile should use it, add a catalog entry (above).
4. If Studio needs to *ask* the agent something (a query rather than a press),
   add a route in `backend/app/api/agents.py` using `_ask_agent()`.
5. Test the pure parts in `tests/test_agent.py`, then restart the agent.

### Add a new widget

1. Create `frontend/js/widgets/<name>.js` exporting
   `mountX(tile, item, ctx) -> destroy`.
   - For live agent data, use `ctx.onState((changed) => …)`: it gets
     everything known at once, then every change, with keys like
     `"windows:pc.cpu"`. The subscription ends with the widget.
   - Add the readers to `agents/windows/poller.py` `READERS`. They only run
     while a phone is watching.
   - Draw only inside `tile`.
   - Use `currentColor` for colours.
   - `destroy()` must clear every timer, listener and fetch.
2. Register it in `WIDGETS` in `js/widgets/index.js`.
3. Add a catalog entry with `kind: "widget"`, plus the i18n strings.
4. Put styles in `css/widgets.css`, using container queries against the tile.
5. **Check `fixup_widget_types()` in `backend/app/db.py`.** It assumes
   `clock_weather` is the only widget. Teach it the new type or retire it.

### Make a tile ask before it runs

Add `{ ...CONFIRM_FIELD }` to its catalog entry's `fields` in
`tile-catalog.js`. Give it `default: true` if a mis-tap costs something,
or `advanced: true` to tuck it under More settings. The phone reads
`params.confirm`. Nothing changes on the backend or the agent.

### Add a "What's new" entry (every public release)

1. Add an entry at the top of `frontend/whats-new.json`: `{"version": "X.Y.Z",
   "en": [...], "ru": [...]}` with 1–5 short bullets that a user, not a
   developer, would care about. Use the same number of bullets in both
   languages.
2. `tests/test_launcher.py` checks the shape.
3. `scripts/check_release.py vX.Y.Z` (the release workflow runs it) refuses
   a release without the entry.

The PC window shows it once after the update, and Studio's What's new
button gets its dot.

### Add a new theme

The slug must appear in **four** places; `tests/frontend.test.mjs` fails if
they disagree:
1. `THEMES` in `backend/app/api/settings.py`;
2. `THEMES` in `frontend/js/theme.js`, plus its `NATIVE_MODE` entry;
3. the inline boot script in `frontend/index.html`, plus its native-ground
   line;
4. a `[data-theme="<slug>"]` block in `frontend/css/themes.css`.

Deploy the backend first, then the frontend (Tech Reference §7). Check
contrast in both light and dark.

### Add a new animation

1. Declare `@keyframes` next to the component's CSS (e.g. `css/button.css`).
2. Animate only `opacity` and `transform`, which stay off the layout path.
3. Write the resting state as the element's own style, and give the keyframes
   only the moving part. The reduced-motion block in `css/base.css` then
   leaves it at rest.
4. If the animation matters for meaning (like the pending dot), keep a
   non-animated signal as well.

### Add a new backend endpoint

1. Add a route to the matching `backend/app/api/*.py`, or a new router that
   you include in `main.py` **above** the static mount. The mount must stay
   last.
2. For writes, gate it with `check_agent_token(x_agent_token)` from
   `app/auth.py`.
3. Use `def` for blocking work (it runs in the threadpool), or `async def`
   with `run_in_threadpool` for slow I/O.
4. If phones should refresh, `await hub.broadcast_to_clients({"type": "workspace_update"})`.
5. Add a test in `tests/test_backend.py` with `TestClient`.

### Add a new WebSocket message

**Phone → backend** (a new `cmd`):
1. Add a branch in `client_ws` in `backend/app/ws/client.py`.
2. Always answer a `req_id` with a result.
3. Add a sender in `frontend/js/ws.js`.

   **Deploy order matters.** An older backend replies `unknown command` to a
   new cmd, so ship the backend first.

**Backend → phone** (a new `type`):
1. Add a branch in `ws.js`'s message handler, with its own callback list and
   `onX()` export.
2. Unknown types are ignored by older phones, so this is safe in either order.

**Agent ↔ backend**: see *Add a new agent command*. A new frame `type` from
the agent needs a branch in `backend/app/ws/agent.py`, which ignores unknown
types.

Document the message in §7 of this file, and add a case to `tests/test_ws.py`.

### Add a startup data migration (fixup)

- **A schema change**: add a column in `init_db()`, guarded on the column
  being absent (see `dock`).
- **A one-shot insert or backfill**:
  1. Wrap it in `_already_applied(conn, "<name>")` / `_mark_applied(conn, "<name>")`.
  2. Commit the mark together with the change.
- **An upgrade UPDATE**: guard it on the **value it upgrades from**, never on
  the label alone. A bare `WHERE label = …` rewrites user edits on every
  start.
- Add a case to `tests/test_db.py`: fresh, legacy, and user-edited.
