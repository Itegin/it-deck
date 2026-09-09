# IT-Deck — technical reference

Full architecture reference for IT-Deck. For what the project is and how to
install it, see [README.md](README.md). `CLAUDE.md` holds the working
conventions and the reasoning behind individual decisions.

The public name is **IT-Deck**. Internal identifiers — the repo folder,
logging namespaces (`controlhub`, `controlhub.api`, `controlhub.ws`) and the
SQLite filename (`controlhub.db`) — deliberately remain `controlhub`. That is
a decision from the rebrand, not an unfinished rename.

---

## Architecture

Three pieces, each holding one persistent WebSocket:

- **Backend** — a FastAPI app (`backend/app/main.py`) deployed as a single
  Docker container on the Debian/Linux host. It serves the REST snapshot
  endpoint (`GET /api/workspaces`), the static frontend, and both WebSocket
  routes. SQLite in WAL mode is the only datastore; the DB file and uploaded
  screenshots live in a bind-mounted `data/` volume.
- **Windows agent** — a Python process on the controlled PC. It holds a
  persistent WebSocket to the backend, executes incoming commands through one
  handler per command type, and polls local state (mic mute, speaker volume,
  default output device, VPN process) on a 1-second timer.
- **Frontend** — a build-step-free vanilla JS/CSS PWA, served straight off
  disk by the backend and bind-mounted into the container rather than baked
  into the image. It runs in the phone's browser and talks to the backend over
  its own WebSocket.

```
phone browser                backend container              Windows PC
┌──────────────┐   ws/client  ┌────────────────┐  ws/agent  ┌───────────┐
│  Dashboard   │◄────────────►│  FastAPI       │◄──────────►│  agent.py │
│  Studio      │   REST/HTTP  │  + SQLite(WAL) │            │  + poller │
└──────────────┘◄────────────►└────────────────┘            └───────────┘
```

The frontend serves two separate pages from the same static mount, split by
what they may do to the item catalog:

- **Dashboard** (`index.html`) — the phone-facing surface. Reads
  `GET /api/workspaces`, sends `execute`/`set_value` over `/ws/client`, and
  writes the theme via `PUT /api/settings/theme`. It never calls
  `POST`/`PUT`/`DELETE` on `/api/items`.
- **Studio** (`studio.html` + `js/studio.js`) — the desktop-only admin page,
  and the only place that mutates the catalog. It *reads* the catalog through
  the same unauthenticated `GET /api/workspaces` the Dashboard uses; the
  `X-Agent-Token` header goes only on the four mutations and on
  `list_devices`. The token is prompted for in-page and held in memory only,
  never `localStorage`.

---

## File map

### `backend/app/`

| File | Purpose |
| --- | --- |
| `main.py` | FastAPI app: logging setup, `load_dotenv()`, startup migrations, `/health`, `GET /api/workspaces`, both WebSocket routes, router registration, and the catch-all `StaticFiles` mount at `/` (which must stay last — Starlette matches routes in registration order). |
| `config.py` | `SERVER_PORT` read from the environment. **Currently imported by nothing** — the container's real port comes from the Dockerfile's `uvicorn --port 8000`. |
| `db.py` | SQLite connection (`foreign_keys = ON` per connection), schema for `workspace` / `item` / `setting`, `seed_if_empty()`, and five idempotent `fixup_*` migrations that build the shipped tile catalog. |
| `models.py` | `Item` / `Workspace` pydantic models and the query helpers `get_workspaces_with_items()`, `get_item()`, `bump_press_count()`. |
| `state.py` | In-memory current-state snapshot plus diffing: `update_state()` returns only the keys whose values actually changed, `get_state()` returns a copy. Not persisted — it is rebuilt from the agent's next poll tick. |
| `pending.py` | Per-`req_id` timeout timers. `track(req_id, seconds, on_timeout)` schedules a synthetic failure; `resolve(req_id)` cancels it. Broadcast-only — it never hands a value back to a specific caller. |
| `agent_requests.py` | The request/response half, deliberately separate from `pending.py`: an HTTP handler parks on an `asyncio.Future` keyed by `req_id` and gets the agent's actual reply dict back. |
| `ws/hub.py` | `ConnectionHub` singleton: a `set` of client sockets, a `dict` of one socket per agent name, `broadcast_to_clients()`, `send_to_agent()`. |
| `ws/agent.py` | `/ws/agent` handler — hello/token check, agent registration, result fan-out (timer cancel → future resolve → broadcast), and state ingestion with agent-name namespacing. |
| `ws/client.py` | `/ws/client` handler — initial full-state push, then `execute` and `set_value` dispatch to the target agent, each with a 5-second timeout. |
| `api/items.py` | Token-gated CRUD for the item catalog: `GET/POST/PUT/DELETE /api/items[/{id}]`. Validates that `params` parses as JSON at write time. Every mutation broadcasts `workspace_update`. |
| `api/workspaces.py` | Token-gated `POST /api/workspaces` and `POST /api/workspaces/{id}/compact`. `_pack_items()` is a pure placement pass (kept DB-free so it can be exercised directly). |
| `api/screenshot.py` | Token-gated `POST /api/screenshot` — accepts a PNG up to 10 MB and writes it beside the DB under `data/screenshots/`, named from the server clock. **Retained but no longer called by anything** (see Known limitations). |
| `api/agents.py` | Token-gated `POST /api/agents/{agent_name}/list_devices` — a request/response proxy that makes a connected agent enumerate audio devices and returns its reply verbatim, with a 5-second budget. Used by Studio's device picker. |
| `api/settings.py` | `GET /api/settings` and `PUT /api/settings/theme`. The theme is constrained to `flat` / `pastel` / `glossy`; a write broadcasts `settings_update`. The one write endpoint with no token — see Known limitations. |

### `agents/windows/`

| File | Purpose |
| --- | --- |
| `agent.py` | Connects to the backend, sends `hello`, runs the receive loop and the poll loop concurrently, and reconnects with exponential backoff (1s → 30s). Owns the `HANDLERS` dispatch table and a `Global\ITDeckAgentSingleton` named mutex that prevents two agents registering under one name. |
| `poller.py` | Polls local state every second and pushes the whole snapshot unconditionally; the backend does the deduplication. A transient audio-stack error skips the tick rather than dropping the connection. |
| `handlers/audio.py` | `audio_mute_toggle`, `audio_volume_set`, `audio_switch`, `list_devices`, plus the read functions the poller uses. Volume/mute go through pycaw's raw device enumerator; device switching and enumeration shell out to the bundled `tools/SoundVolumeView.exe` with a 4-second subprocess timeout. |
| `handlers/process.py` | `launch_app` (via `os.startfile`, so a UAC-manifested exe can actually elevate), `process_toggle` (start/stop the configured VPN), and `force_stop`, which derives a process name from the *original* item's type and params. |
| `handlers/screenshot.py` | `screenshot` — grabs the primary monitor with `mss`, converts to a CF_DIB (a BMP with its 14-byte file header sliced off) and puts it on the Windows clipboard. |
| `start_agent.bat` | The launcher. Creates the desktop shortcut on first run and `pause`s only on a non-zero exit, so a window left open means the agent crashed. |
| `install_task.ps1` / `uninstall_task.ps1` | Register/remove an "IT-Deck Agent" logon Scheduled Task, always as the interactive user. |
| `tools/` | `SoundVolumeView.exe` and helpers. |

### `frontend/`

| File | Purpose |
| --- | --- |
| `index.html` | Dashboard shell. Carries the one inline script in the project: it reads the cached theme from `localStorage` synchronously so the first paint isn't a flash of the wrong theme. |
| `studio.html` | Desktop-only admin page, self-contained styling, deliberately **not** linked to `css/themes.css` — that omission is the whole mechanism keeping Studio off the Dashboard's themes. |
| `js/app.js` | Boot: fetch workspaces, resolve which deck to show (localStorage → `?workspace=` → selector), render, and wire the WebSocket callbacks. Turns tagged failures into messages that name what to do next. |
| `js/api.js` | REST fetches plus per-item `params` JSON parsing. Tags each failure kind (`unreachable`, `status`, `badReply`, `badParams`) so the UI can name the actual problem. |
| `js/ws.js` | The client WebSocket: connect/reconnect with backoff, `sendExecute`, `sendSetValue`, and the in-flight `req_id` bookkeeping that turns raw result frames into per-tile `pending`/`ok`/`error` phases. |
| `js/render.js` | All DOM writes: grid and tiles, the volume slider's pointer handling, live state → tile colour/fill/subtitle, command-feedback classes, the workspace selector, and the error state. |
| `js/studio.js` | The admin UI: item table, add/edit form, delete, layout compaction, and the audio-device picker backed by `POST /api/agents/{name}/list_devices`. |
| `js/theme.js` | Fetch/PUT the theme, cycle it, and apply it to `<html data-theme>`. Re-validates every value against the allowlist before it reaches the DOM. |
| `js/longpress.js` | 500 ms stationary hold → long press; movement past 10 px cancels it. |
| `js/contextmenu.js` / `js/toast.js` | The long-press menu (Force Stop / Cancel) and transient error messages. |
| `css/` | `base.css`, `grid.css`, `button.css`, `themes.css`, `contextmenu.css`, `toast.css`. |
| `manifest.webmanifest`, `icons/` | PWA metadata for add-to-home-screen. |

### Repo root

`deploy.sh` (pull, redeploy, verify container code matches disk — backend
only), `backup.sh` (nightly `sqlite3 .backup`, 14 kept), `check.sh` (system
status readout), `docker-compose.yml`, `ansible/`, and
`.github/workflows/build.yml` (builds and pushes the GHCR image on `v*.*.*`
tags only, never on a push to `main`). Also `gen_icon.py` and
`scripts/token_fingerprint.py`.

---

## Data model

Three tables, created by `init_db()`:

- **`workspace`** — `id`, `name`, `position`, `grid_cols` (default 3),
  `grid_rows` (default 5).
- **`item`** — `id`, `workspace_id` (FK, `ON DELETE CASCADE`), `row`, `col`,
  `width`, `height`, `label`, `icon`, `color`, `kind`, `type`, `target`,
  `params`, `state_key`, `press_count`, `last_pressed`.
- **`setting`** — `key`/`value`. Rows are created on first write; an absent
  row means "never set" and each reader supplies its own default.

`item.type` is the command name the agent dispatches on. `item.target` is the
agent name. `item.params` is stored as a **JSON string** and parsed at the
point of use (backend `ws/client.py`, frontend `js/api.js`); `api/items.py`
validates that it parses on write, and the `db.py` fixups are the only other
writer.

Recognised `params` keys today: `path`, `device`, `value` (injected by
`set_value`, not stored), `active_style` (`normal` | `alert`),
`active_color`, `alert_color`, `false_color`, `output_device_primary`,
`output_device_secondary`, `process_name`.

`item.state_key` names the state value that colours the tile — `mic.muted`,
`speaker.volume`, `speaker.muted`, `speaker.device_name`, `vpn.running`. The
frontend prefixes it with the item's `target` to match what the backend
broadcasts (see below).

The seed and the five `fixup_*` functions in `db.py` run on **every** startup
and are written to be idempotent, because `seed_if_empty()` only ever fires on
a fresh DB and older installs still need backfilling. `fixup_volume_item()`
and `fixup_audio_switch_state_key()` are additionally guarded on a
pre-migration value so they cannot silently revert a Studio edit on the next
container restart.

---

## Protocol

Two WebSocket routes, both registered in `main.py` before the static mount.

### `/ws/agent`

The agent's first frame must be a hello, or the socket is closed with code
`4001`:

```json
{"type": "hello", "agent": "windows", "version": "0.1.0", "token": "<AGENT_TOKEN>"}
```

The token is checked against `AGENT_TOKEN` in the backend's environment
*before* the agent is registered in the hub, and an unset `AGENT_TOKEN` fails
closed (so a missing env var can't equal a missing token). On success the
backend broadcasts `agent_status` to every client and registers the socket
under `hello.agent` (default `"windows"`). Only one socket per agent name — a
second registration logs a warning and overwrites the first.

**Backend → agent**, from an `execute`:

```json
{"cmd": "<override_type or item.type>",
 "item_type": "<item.type>",
 "params": {...},
 "req_id": "...",
 "item_id": 7}
```

`item_type` always carries the item's own type even when `cmd` was overridden;
`force_stop` is the one handler that reads it, to derive a process name from
the original item.

From a `set_value` (no `item_type`; the live value is merged into params):

```json
{"cmd": "<item.type>", "params": {...item params, "value": 42}, "req_id": "...", "item_id": 7}
```

From `POST /api/agents/{name}/list_devices` (no `item_id` at all):

```json
{"cmd": "list_devices", "params": {}, "req_id": "api-1725800000000-a1b2c3d4"}
```

**Agent → backend**, a result. The handler's own dict is spread last, so extra
keys ride along (`list_devices` returns a `devices` array) and nothing
validates `status`:

```json
{"type": "result", "req_id": "...", "item_id": 7, "status": "ok"}
{"type": "result", "req_id": "...", "item_id": 7, "status": "error", "message": "..."}
{"type": "result", "req_id": "...", "status": "error", "message": "unknown command: toggle"}
```

The unknown-command reply omits `item_id` — one of several paths that do,
which is why the client correlates on `req_id` and never on `item_id`.

**Agent → backend**, polled state, sent every tick regardless of change:

```json
{"type": "state", "data": {"mic.muted": false, "speaker.volume": 34,
                           "speaker.muted": false,
                           "speaker.device_name": "Speakers (Realtek...)",
                           "vpn.running": false}}
```

The backend **namespaces every key with the reporting agent's name**
(`windows:mic.muted`, `windows:speaker.volume`, …) before diffing, so two
connected agents can't overwrite each other in the single flat state dict.
Clients only ever see the namespaced form, and `render.js` builds each tile's
`data-state-key` as `` `${item.target}:${item.state_key}` `` to match.
Unchanged ticks broadcast nothing.

### `/ws/client`

No handshake and no auth — the socket is accepted and registered immediately,
and the client is sent the full state snapshot at once, because it has missed
every diff broadcast so far.

**Client → backend:**

```json
{"cmd": "execute", "item_id": 7, "req_id": "1725800000000-k3n9x"}
{"cmd": "execute", "item_id": 7, "req_id": "...", "override_type": "force_stop"}
{"cmd": "set_value", "item_id": 5, "value": 42, "req_id": "..."}
```

`execute` bumps `press_count`/`last_pressed`; `set_value` deliberately does
not, since a single slider drag fires it dozens of times.

Both check `item["target"] in hub.agents` *before* sending. A single
persistent socket per agent means absence is a definitive answer, not a race,
so a missing agent is answered synchronously with `"agent offline"` rather
than left to time out.

**Backend → client** — results and state diffs are broadcast to *every*
connected client verbatim (the backend has no client identity to filter by, so
each client filters on the `req_id`s it actually sent):

```json
{"type": "state", "data": {...}}
{"type": "result", "req_id": "...", "item_id": 7, "status": "ok"}
{"type": "agent_status", "agent": "windows", "status": "online" | "offline"}
{"type": "workspace_update"}
{"type": "settings_update", "settings": {"theme": "pastel"}}
```

`workspace_update` is a bare signal — the client refetches and re-renders.
`settings_update` carries its payload instead, because there is one word to
deliver and nothing to re-render.

### Timeouts

Every execute resolves within 5 seconds — ok, error, or timeout. A `req_id`
that never gets a matching result is a bug, not an edge case. Four budgets
stack deliberately:

| Where | Budget | What it does |
| --- | --- | --- |
| `ws/client.py` `track(req_id, 5.0)` | 5 s | Broadcasts a synthetic `"timeout"` result. Started only *after* the command actually reached an agent. |
| `js/ws.js` `COMMAND_TIMEOUT_MS` | 8 s | Client-side backstop, longer on purpose so the server's real reason wins. Covers the two cases that produce no server reply at all: a command dropped by the `readyState` guard, and a socket down past the 5 s broadcast. |
| `api/agents.py` `AGENT_REQUEST_TIMEOUT` | 5 s | HTTP caller parked on a future; exceeding it returns `504`. |
| `handlers/audio.py` `subprocess.run(timeout=4)` | 4 s | Kept under the backend's 5 s so a wedged `SoundVolumeView.exe` surfaces as the handler's own error rather than a synthetic one. The agent's receive loop is single-threaded, so an unbounded subprocess would stall everything. |

A late result never crashes anything: `pending.resolve()` and
`agent_requests.resolve_future()` both treat an unknown `req_id` as a silent
no-op, and the client ignores `req_id`s it doesn't recognise.

---

## HTTP endpoints

| Endpoint | Auth | Notes |
| --- | --- | --- |
| `GET /health` | none | `{"status": "ok"}` |
| `GET /api/workspaces` | **none** | Workspaces with their items — the Dashboard's snapshot read |
| `GET /api/settings` | **none** | `{"theme": "..."}` |
| `PUT /api/settings/theme` | **none** | The one unauthenticated *write*; value constrained to three literals |
| `GET /api/items/{id}` | `X-Agent-Token` | |
| `POST /api/items` | `X-Agent-Token` | |
| `PUT /api/items/{id}` | `X-Agent-Token` | `exclude_unset`, so omitted ≠ explicit null |
| `DELETE /api/items/{id}` | `X-Agent-Token` | |
| `POST /api/workspaces` | `X-Agent-Token` | |
| `POST /api/workspaces/{id}/compact` | `X-Agent-Token` | Repacks items into the grid |
| `POST /api/screenshot` | `X-Agent-Token` | PNG ≤ 10 MB; currently unused |
| `POST /api/agents/{name}/list_devices` | `X-Agent-Token` | Proxies to a live agent |
| `/` (everything else) | none | Static frontend, `html=True` |

A 4xx/5xx from `list_devices` means the *request* failed (agent offline, no
reply in time). A handler's own `{"status": "error"}` is a successful
round-trip reporting a failed operation and comes back as a normal `200` —
the same distinction `ws/client.py` draws.

---

## Adding a new agent command

1. Add a handler in `agents/windows/handlers/` returning `{"status": "ok"}` or
   `{"status": "error", "message": ...}`.
2. Register it in `HANDLERS` in `agents/windows/agent.py`.
3. Point an `item` row at it: `type` = the command name, `target` = the agent
   name, `params` = a JSON string of static config. Studio Mode can do this,
   or add a `fixup_*` in `db.py`.
4. Restart the agent — close its console window and relaunch the desktop
   shortcut. `./deploy.sh` only touches the backend container; a stale agent
   keeps running old code silently.

If the command needs a live value the DB row can't hold, note that
`sendExecute` carries only `item_id` + `req_id`. `set_value` is the existing
path for one such value (the volume slider); anything else means extending the
message shape deliberately.

---

## Dashboard themes

Three themes — `flat` (the original look), `pastel`, `glossy` — selected by a
`data-theme` attribute on `<html>` and implemented as CSS custom property
overrides in `frontend/css/themes.css`, which is linked from `index.html`
only.

Every tile state resolves to one custom property, `--tile-state-color`
(`button.css`), written by all four state mechanisms: the default
`--tile-color`, `.state-active`, `.state-alert`, and `render.js`'s inline
write for `false_color`. Themes only change container-level presentation on
top of that one value; none of them touch the state logic.

The choice is **server-side** (the `setting` table), so it is shared by every
panel rather than being per-browser. `localStorage` holds only a cache of it,
read by the inline script in `index.html`'s `<head>` so the first paint isn't a
flash of the wrong theme; the server value always wins a moment later. The
allowlist is enforced three times — in `api/settings.py` before storage, in
`theme.js` before the value reaches the DOM, and again in the inline script —
because the value arrives from three sources that can each lie.

---

## Platform constraints

- **The agent is Windows-only, by design.** It depends on `pycaw`/`comtypes`
  (Windows COM audio APIs) for mute and volume, `pywin32` for the clipboard
  DIB and the singleton mutex, and the bundled `SoundVolumeView.exe` for
  device switching.
- **Python 3.7–3.12 for the agent.** `comtypes` supports no further; 3.13/3.14
  caused a real crash.
- **It must run in the interactive user session,** never as a SYSTEM-context
  service: session 0 cannot access a logged-in user's audio session (Windows
  Session 0 Isolation). `install_task.ps1` registers the task as the current
  interactive user for this reason.
- **One socket per agent connection.** `ConnectionHub` keeps one `WebSocket`
  per agent name, so "not in `hub.agents`" is a definitive offline signal.
- **No secure context on the phone.** The Dashboard is reached over plain
  `http://<lan-ip>:8000`, so APIs like `crypto.randomUUID()` are unavailable;
  `ws.js` generates request IDs manually.
- **iOS Safari applies `:active` only if a touch listener exists** somewhere on
  the page — `app.js` ends with an empty `touchstart` listener on
  `document.body` purely to enable that.
- **The frontend is bind-mounted, not baked in.** `backend/Dockerfile`
  deliberately does not `COPY frontend/`, so a frontend change needs no image
  rebuild — but it also means the container is useless without the repo
  checkout beside it.
- **The port lives in three places** — `.env`'s `SERVER_PORT`,
  `docker-compose.yml`'s mapping, and the Dockerfile's `uvicorn --port`. Only
  the Windows agent and the Ansible firewall rule actually read a variable;
  `backend/app/config.py` is imported by nothing today.

---

## Known limitations

- **Three placeholder tiles are not wired.** `Lights`, `Spotify` and
  `Sleep PC` still carry the prototype types `toggle`, `launch` and `run`,
  none of which are in the agent's `HANDLERS`. Pressing one returns
  `unknown command: <type>`.
- **Single-user, single-process by design.** No multi-tenancy, no per-user
  data, no horizontal scaling. `ConnectionHub` and `state.py` are module-level
  singletons in one process; running a second backend replica would split the
  agent registry and the state snapshot in half.
- **Auth is one shared secret.** `POST /api/screenshot`, all four `/api/items`
  endpoints (including `GET`), the two `/api/workspaces` mutations, and
  `POST /api/agents/{name}/list_devices` are gated by the same `AGENT_TOKEN`
  the WebSocket agent connection uses, via an `X-Agent-Token` header. There is
  no per-caller identity, no expiry, and no rate limiting.
- **`/ws/client` has no auth at all.** `client_ws()` accepts and registers
  every connection unconditionally, so anything that can reach the backend on
  the LAN can open it and send `execute` for any item in the catalog. This is
  the widest hole in the current design, and it is what the token on
  `/api/items` does *not* cover.
- **`PUT /api/settings/theme` is an unauthenticated write.** Deliberate: the
  only client that changes the theme is the Dashboard on the phone, which has
  no token and nowhere safe to keep one over plain HTTP on the LAN. The value
  is constrained to three literals before storage, so what it concedes is that
  anyone on the network can change how the deck looks — not run a command,
  reach an agent, or touch the catalog.
- **`GET /api/workspaces` is unauthenticated** and returns the full catalog,
  including every item's `params` — which can contain executable paths and
  device identifiers.
- **`POST /api/screenshot` is retained but unused.** The screenshot handler
  copies to the PC's local clipboard now instead of uploading for remote
  viewing; the endpoint is kept in case a remote-viewable-screenshot feature
  wants it back. It still accepts uploads from any token-bearing caller and
  writes them to disk.
- **Mixed content over the nginx proxy.** `js/ws.js` opens
  `ws://${location.host}/ws/client` unconditionally. Reached directly on
  `:8000` that is correct; reached through the TLS reverse proxy the Ansible
  playbook installs, a plain `ws://` from an `https://` page is blocked by the
  browser as mixed content. Only the direct HTTP path works end to end today.
- **`params` is a JSON string, validated only on the API path.** The
  `db.py` fixups write it directly, and a malformed row takes the whole
  Dashboard down (by design — `api.js` names the offending item so it can be
  fixed in Studio).
- **No test suite in CI.** `.github/workflows/build.yml` only builds and
  pushes the backend image on version tags — nothing is run against the code.
  The only test file in the repo is `agents/windows/test_mic.py`, a manual
  probe; `check.sh` and `deploy.sh` are the other manual verification.
