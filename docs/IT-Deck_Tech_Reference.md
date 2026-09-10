# IT-Deck — Technical Reference

Full technical reference for IT-Deck, written by reading the code in the
working tree at commit `eac5a69` plus the uncommitted changes on top of it.

- **What the project is and how to install it** → [`README.md`](../README.md)
- **What Claude Code needs every session** (core rules, deploy patterns,
  naming) → [`CLAUDE.md`](../CLAUDE.md)
- **This file** — schema, API surface, tile/theme internals, agent internals,
  deploy pipeline, tech debt.

Anything I could not confirm from the code is marked **unverified** rather
than guessed.

The public name is **IT-Deck**. Internal identifiers — the repo folder, the
logging namespaces (`controlhub`, `controlhub.api`, `controlhub.ws`) and the
SQLite filename (`controlhub.db`) — deliberately remain `controlhub`. That is
a decision from the rebrand, not an unfinished rename.

---

## 1. Architecture overview

Three pieces, each holding one persistent WebSocket.

```
phone browser                backend container              Windows PC
┌──────────────┐   ws/client  ┌────────────────┐  ws/agent  ┌───────────┐
│  Dashboard   │◄────────────►│  FastAPI       │◄──────────►│  agent.py │
│  Studio      │   REST/HTTP  │  + SQLite(WAL) │            │  + poller │
└──────────────┘◄────────────►└────────────────┘            └───────────┘
```

### Backend

FastAPI app (`backend/app/main.py`), deployed as a single Docker container on
a Debian host. It serves:

- the REST snapshot endpoint `GET /api/workspaces`,
- the static frontend (a catch-all `StaticFiles` mount at `/`, `html=True`),
- two WebSocket routes, `/ws/agent` and `/ws/client`.

SQLite in WAL mode is the only datastore. The DB file and uploaded screenshots
live under a bind-mounted `data/` volume (`/app/data` in the container).

Route registration order is load-bearing: Starlette matches in registration
order, so the `/` mount must stay last in `main.py` or it swallows `/ws/*`,
`/health` and `/api/*`. `StaticFiles` asserts `scope["type"] == "http"` and
would 500 on a WebSocket upgrade.

### Frontend

Vanilla JS + CSS PWA, **no build step**, served straight off disk and
**bind-mounted** into the container (`backend/Dockerfile` deliberately does not
`COPY frontend/`). Two pages off the same static mount, split by what they are
allowed to do to the item catalog:

| | Dashboard (`index.html`) | Studio (`studio.html`) |
|---|---|---|
| Audience | the phone | desktop only |
| Reads catalog | `GET /api/workspaces` (unauthenticated) | `GET /api/workspaces` (unauthenticated) |
| Mutates catalog | never | `POST`/`PUT`/`DELETE /api/items` |
| WebSocket | `/ws/client` — `hello`, then `execute`, `set_value` | none |
| Token | `CLIENT_TOKEN` in the `hello` frame; stored in `localStorage`, seeded from `?token=` or a prompt | `X-Agent-Token`, prompted per page load, held in memory only |
| Themed | yes (`css/themes.css` linked) | no (`themes.css` deliberately not linked) |

### Windows agent

Python process on the controlled PC (`agents/windows/agent.py`). Holds one
persistent WebSocket to the backend, dispatches incoming commands through a
`HANDLERS` table (one module per command family), and pushes a polled local
state snapshot every second.

---

## 2. File map

### `backend/app/`

| File | Responsibility |
| --- | --- |
| `main.py` | Logging setup (`force=True`, or uvicorn's own dictConfig silences app loggers), `load_dotenv()`, the startup migration sequence, `/health`, `GET /api/workspaces`, both WebSocket routes, router registration, and the catch-all `StaticFiles` mount **last**. |
| `config.py` | `SERVER_PORT` read from the environment. **Imported by nothing** — see §11. |
| `db.py` | `DB_PATH = /app/data/controlhub.db`, connection factory (`PRAGMA foreign_keys = ON` per connection), schema for `workspace` / `item` / `setting`, `seed_if_empty()`, and **seven** idempotent `fixup_*` migrations. |
| `models.py` | `Item` / `Workspace` pydantic models and the query helpers `get_workspaces_with_items()`, `get_item()`, `bump_press_count()`. |
| `state.py` | In-memory current-state snapshot plus diffing. `update_state()` returns only keys whose values actually changed; `get_state()` returns a copy. Not persisted — rebuilt from the agent's next poll tick. |
| `pending.py` | Per-`req_id` timeout timers. `track(req_id, seconds, on_timeout)` schedules a synthetic failure; `resolve(req_id)` cancels it. Broadcast-only — it never hands a value back to one caller. |
| `agent_requests.py` | The request/response half, deliberately separate from `pending.py`: an HTTP handler parks on an `asyncio.Future` keyed by `req_id` and gets the agent's actual reply dict back. |
| `ws/hub.py` | `ConnectionHub` module-level singleton: a `set` of client sockets, a `dict` of one socket per agent name, `broadcast_to_clients()`, `send_to_agent()`. |
| `ws/agent.py` | `/ws/agent` — hello/token check *before* hub registration, result fan-out (cancel timer → resolve future → broadcast), state ingestion with agent-name namespacing. |
| `ws/client.py` | `/ws/client` — initial full-state push, then `execute` / `set_value` dispatch, each with a 5-second timeout started only after the command actually reached an agent. |
| `api/items.py` | Token-gated item CRUD. Validates `params` parses as JSON (`_validate_params_json`) **and** validates grid placement (`_validate_placement`). Every mutation broadcasts `workspace_update`. |
| `api/workspaces.py` | Token-gated `POST /api/workspaces` and `POST /api/workspaces/{id}/compact`. `_pack_items()` is a pure placement pass, kept DB-free so it can be exercised directly. |
| `api/screenshot.py` | Token-gated `POST /api/screenshot` — PNG ≤ 10 MB written beside the DB under `data/screenshots/`, named from the server clock. **Retained but called by nothing** (see §11). |
| `api/agents.py` | Token-gated `POST /api/agents/{agent_name}/list_devices` — request/response proxy that makes a connected agent enumerate audio devices and returns its reply verbatim, 5-second budget. **There is no agent-status endpoint** (agent status travels over the WebSocket only). |
| `api/settings.py` | `GET /api/settings`, `PUT /api/settings/theme`. Holds the canonical `THEMES` allowlist. The one write endpoint with no token. |

### `agents/windows/`

| File | Responsibility |
| --- | --- |
| `agent.py` | Connects, sends `hello`, runs the receive loop and the poll loop under one `asyncio.gather`, reconnects with exponential backoff (1 s → 30 s cap). Owns `HANDLERS` and a `Global\ITDeckAgentSingleton` named mutex that stops two agents registering under one name. |
| `poller.py` | Polls local state every **1 s** and pushes the whole snapshot unconditionally; the backend deduplicates. A transient audio-stack error skips the tick rather than dropping the connection. |
| `handlers/audio.py` | `audio_mute_toggle`, `audio_volume_set`, `audio_switch`, `list_devices`, plus the read functions the poller uses. Mute/volume go through pycaw's raw device enumerator; enumeration and switching shell out to the bundled `tools/SoundVolumeView.exe`. |
| `handlers/process.py` | `launch_app` (via `os.startfile`, so a UAC-manifested exe can actually elevate), `process_toggle` (start/stop the configured VPN), `force_stop` (derives a process name from the *original* item's type and params). |
| `handlers/screenshot.py` | `screenshot` — grabs the primary monitor with `mss`, converts to a CF_DIB (a BMP with its 14-byte file header sliced off) and puts it on the **PC's own clipboard**. |
| `start_agent.bat` | The launcher. Creates the desktop shortcut and icon on first run; `pause`s only on a non-zero exit, so a window left open means the agent crashed and the text in it is the error. |
| `install_task.ps1` / `uninstall_task.ps1` | Register/remove an "IT-Deck Agent" logon Scheduled Task, always as the interactive user. Per `CLAUDE.md` the task is **disabled on both PCs** and is not what runs the agent. |
| `tools/` | `SoundVolumeView.exe`, `make_icon.py`. |
| `test_mic.py` | A manual probe, not a test suite. |

### `frontend/`

| File | Responsibility |
| --- | --- |
| `index.html` | Dashboard shell. Carries the **only inline script in the project**: it reads the cached theme from `localStorage` synchronously so the first paint isn't a flash of the wrong theme. |
| `studio.html` | Desktop-only admin page; self-contained styling, five form groups (Identity / Action / Placement / Appearance / Advanced). Deliberately **not** linked to `css/themes.css`. |
| `js/app.js` | Boot: fetch workspaces, resolve which deck to show (`localStorage` → `?workspace=` → selector), render, wire WebSocket callbacks, map tagged failures to actionable messages. Ends with the empty `touchstart` listener iOS needs for `:active`. |
| `js/api.js` | REST fetches plus per-item `params` JSON parsing. Tags each failure kind (`unreachable`, `status`, `badReply`, `badParams`). |
| `js/ws.js` | Client WebSocket: connect/reconnect with backoff (1 s → 30 s), `sendExecute`, `sendSetValue`, manual `req_id` generation, and the in-flight bookkeeping that turns raw result frames into per-tile `pending`/`ok`/`error` phases. |
| `js/render.js` | Every DOM write: grid and tiles, the icon table, the WCAG ink calculation, the volume slider's pointer + keyboard handling, live state → colour/fill/subtitle, command-feedback classes, the workspace selector, the error state. |
| `js/studio.js` | Admin UI: item table (filtered by workspace), add/edit form, delete, layout compaction, and the audio-device picker backed by `list_devices`. |
| `js/theme.js` | Fetch/PUT the theme, cycle it, apply it to `<html data-theme>`; derives the display label from the slug; re-validates every value against the allowlist before it reaches the DOM. |
| `js/longpress.js` | 500 ms stationary hold → long press; movement past 10 px cancels. |
| `js/contextmenu.js` | The long-press dialog (Force Stop / Cancel), `role="dialog" aria-modal="true"`, focus restored on dismiss. |
| `js/toast.js` | One shared `role="status"` element, 3500 ms, newest message wins. |
| `css/base.css` | The palette tokens (`--color-*`, `--tile-default-color`, `--color-scrim`), page/body rules, reduced-motion block. Linked by **both** pages. |
| `css/grid.css` | Dashboard only. Pins `html, body` (kills iOS rubber-band), makes `body` a flex column so `#grid` has real height, sizes explicit **and implicit** tracks. |
| `css/button.css` | The tile: the `--tile-state-color` resolution, `--tile-ink`, the sheen, the slider fill, and the four external-condition modifiers. |
| `css/themes.css` | The three themes that need blocks — Flat is the base stylesheets untouched and has none. Linked from `index.html` only. |
| `css/contextmenu.css`, `css/toast.css` | The two overlays. |
| `manifest.webmanifest`, `icons/` | PWA metadata for add-to-home-screen. |

### Repo root

`deploy.sh`, `check.sh`, `backup.sh`, `docker-compose.yml`, `ansible/`,
`.github/workflows/build.yml`, `gen_icon.py`,
`scripts/token_fingerprint.py`, plus the docs (`README.md`, `CLAUDE.md`,
`DOCUMENTATION.md`, `AUDIT_REPORT.md`).

---

## 3. Database schema

Three tables, created by `init_db()` with `CREATE TABLE IF NOT EXISTS`.
`PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL` are set at init;
`PRAGMA foreign_keys = ON` is set on **every** connection (SQLite defaults it
off per-connection, and `item.workspace_id`'s `ON DELETE CASCADE` only fires
when it is on).

### `workspace`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `name` | TEXT NOT NULL | Shown in the Dashboard header as `IT-Deck <name>` |
| `position` | INTEGER NOT NULL | Sort order; `POST /api/workspaces` assigns `MAX(position)+1` |
| `grid_cols` | INTEGER NOT NULL DEFAULT 3 | Enforced by `_validate_placement` |
| `grid_rows` | INTEGER NOT NULL DEFAULT 5 | Enforced by `_validate_placement` |

No `CHECK` constraint backs `grid_cols`, so `compact_workspace` clamps it with
`max(1, ...)` — a 0 would make the placement scan spin forever.

### `item`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK | |
| `workspace_id` | INTEGER NOT NULL | FK → `workspace(id)`, `ON DELETE CASCADE` |
| `row`, `col` | INTEGER NOT NULL | 0-based; `render.js` writes `row+1`/`col+1` into `grid-row`/`grid-column` |
| `width`, `height` | INTEGER DEFAULT 1 | Span counts |
| `label` | TEXT NOT NULL | Tile caption, and the accessible name of a slider |
| `icon` | TEXT | Key into `render.js`'s `ICONS` table. A key with no entry renders no icon — never a placeholder |
| `color` | TEXT DEFAULT `#2a2f38` | The tile's default state colour. **Unvalidated** — `ItemUpdate.color` is a bare `str \| None` |
| `kind` | TEXT NOT NULL | The code only ever tests `=== "action"`; any other value renders as an inert tile with no listener at all. Studio's Kind select offers exactly `action` and `widget`, and every seeded row is `action` |
| `type` | TEXT NOT NULL | The command name the agent dispatches on |
| `target` | TEXT NOT NULL DEFAULT `windows` | The agent name |
| `params` | TEXT NOT NULL DEFAULT `{}` | A **JSON string**, parsed at the point of use |
| `state_key` | TEXT | Names the polled state value that colours the tile |
| `press_count` | INTEGER NOT NULL DEFAULT 0 | Bumped by `execute`, never by `set_value` |
| `last_pressed` | TEXT | `datetime('now')` |

**Icon keys registered in `render.js`:** `lightbulb`, `music`, `moon`,
`terminal`, `mic`, `speaker`, `headphones`, `audio-switch`, `camera`,
`shield`. Icon markup is module-authored SVG only — `item.icon` is used as a
lookup key and never interpolated into `innerHTML`.

**`params` keys recognised somewhere in the codebase today:**

| Key | Read by | Meaning |
| --- | --- | --- |
| `path` | `handle_launch_app`, `handle_force_stop` | Executable to launch |
| `device` | `handle_audio_mute_toggle`, `handle_audio_volume_set` | `microphone` \| `speaker` |
| `value` | `handle_audio_volume_set` | **Injected by `set_value` at send time, never stored** |
| `active_style` | `render.js` | `normal` → `.state-active`, `alert` → `.state-alert` |
| `active_color` | `render.js` | Inline `--active-color` |
| `alert_color` | `render.js` | Inline `--alert-color` |
| `false_color` | `render.js` | Inline `--tile-state-color` on the false side of a boolean |
| `output_device_primary` / `output_device_secondary` | `handle_audio_switch` | SoundVolumeView Command-Line Friendly IDs; both present ⇒ A/B toggle |
| `process_name` | `handle_force_stop` | Explicit override for the derived name |

Nothing validates *which* keys a given `type` understands. `params` is checked
for **parseability only**, and only on the API path.

**`state_key` values the poller actually produces:** `mic.muted`,
`speaker.volume`, `speaker.muted`, `speaker.device_name`, `vpn.running`. The
frontend prefixes each with the item's `target` (`windows:mic.muted`) to match
what the backend broadcasts.

### `setting`

| Column | Type | Notes |
| --- | --- | --- |
| `key` | TEXT PRIMARY KEY | Two keys exist: `theme` and `mode` |
| `value` | TEXT NOT NULL | |

Rows are created on first write via `INSERT … ON CONFLICT(key) DO UPDATE`, so
there is no seed and no fixup: an absent row means "never set", and each
reader supplies its own default. Deliberately not a column on `workspace` —
both are one choice shared by every panel, so putting either on a workspace
row would make switching decks silently change how the app looks.

`theme` holds a `THEMES` slug (`flat` … `liquid-glass`); `mode` holds
`auto` | `light` | `dark` and is the *light/dark axis*, independent of the
theme — see §7.

### Seed and fixups

`seed_if_empty()` inserts one workspace (`Home`, 3×5) and six placeholder
items — but only on a genuinely fresh DB. Everything after it is an
**idempotent fixup that runs on every startup**, because older installs still
need backfilling. Order in `main.py` is load-bearing:

| # | Function | What it does | Idempotency guard |
|---|---|---|---|
| 1 | `fixup_legacy_seed` | `Terminal` → `launch_app` / `notepad.exe` | Re-applies identical values forever (harmless) |
| 2 | `fixup_mic_item` | `Camera` → `Mic` / `audio_mute_toggle`, then a second unguarded backfill of `params`/`icon` keyed on `label = 'Mic'` | Label flip + a second always-on UPDATE |
| 3 | `fixup_volume_item` | `Volume` → `audio_volume_set`, width 2, moved to (2,0) | `AND type <> 'audio_volume_set'` — added after an always-on version silently reverted Studio edits on every restart |
| 4 | `fixup_day4_items` | Inserts `Headphones`, `Audio Switch`, `Screenshot` | Insert-if-label-missing. **Must run after #3** — Headphones takes the cell Volume's move vacates |
| 5 | `fixup_vpn_item` | Inserts `VPN` at (3,1) | `WHERE NOT EXISTS`; `workspace_id` hardcoded to `1` (unlike #4's dynamic lookup) |
| 6 | `fixup_audio_switch_state_key` | Sets `Audio Switch`'s `state_key = speaker.device_name` | `AND state_key IS NULL` |
| 7 | `fixup_toggle_off_colors` | Repaints `Mic` and `VPN` off-state colour to the neutral `#2a2f38` | Guarded on the exact colour being replaced. **Runs last** — it must see the rows the earlier fixups create |

The fixups write to SQLite directly and therefore **bypass**
`_validate_placement`; their placements are hand-verified in their own
comments.

Of the ten seeded tiles, seven are wired to a real handler. `Lights`,
`Spotify` and `Sleep PC` still carry the prototype types `toggle`, `launch`
and `run`, none of which are in `HANDLERS` — pressing one returns
`unknown command: <type>`.

---

## 4. HTTP API surface

Every endpoint in `backend/app/` and `backend/app/api/**`.

| Method + path | Auth | Request | Response |
| --- | --- | --- | --- |
| `GET /health` | none | — | `{"status": "ok"}` |
| `GET /api/workspaces` | **none** | — | `[{id, name, position, grid_cols, grid_rows, items: [item, …]}]` — every column of every item, `params` still a JSON **string** |
| `GET /api/settings` | **none** | — | `{"theme": "flat"\|"pastel"\|"glossy"\|"liquid-glass", "mode": "auto"\|"light"\|"dark"}` — both keys always present; an unknown *stored* value logs a warning and serves that key's default (`flat` / `auto`) |
| `PUT /api/settings/theme` | **none** | `{"theme": "<slug>"}` | `{"theme": "<slug>"}`; `422` if not in the allowlist. Broadcasts `settings_update` |
| `PUT /api/settings/mode` | **none** | `{"mode": "auto"\|"light"\|"dark"}` | `{"mode": "<value>"}`; `422` otherwise. Broadcasts `settings_update`. Written by Studio's "Deck background" picker; unauthenticated for the same reason the theme is (§7) |
| `GET /api/items/{id}` | `X-Agent-Token` | — | The item row, or `404` |
| `POST /api/items` | `X-Agent-Token` | `ItemCreate` (`workspace_id`, `row`, `col`, `width`=1, `height`=1, `label`, `icon?`, `color`=`#2a2f38`, `kind`, `type`, `target`=`windows`, `params`=`"{}"`, `state_key?`) | The created row. `400` on bad params JSON, bad placement, or FK failure. Broadcasts `workspace_update` |
| `PUT /api/items/{id}` | `X-Agent-Token` | `ItemUpdate` — every field optional, `exclude_unset` so *omitted* ≠ *explicit null* | The updated row. Placement is re-checked against the **merged** rectangle, not just the submitted fields. `400` if nothing to update. Broadcasts `workspace_update` |
| `DELETE /api/items/{id}` | `X-Agent-Token` | — | `{"status": "ok"}`, or `404`. Broadcasts `workspace_update` |
| `POST /api/workspaces` | `X-Agent-Token` | `{"name", "grid_cols"=3, "grid_rows"=5}` | The created workspace; `position` is assigned server-side. Broadcasts `workspace_update` |
| `POST /api/workspaces/{id}/compact` | `X-Agent-Token` | — | The full re-packed item list. `404` if the workspace is missing. Broadcasts `workspace_update` |
| `POST /api/screenshot` | `X-Agent-Token` | `multipart/form-data`, `file` — must declare `image/png`, ≤ 10 MB | `{"status":"ok","filename":"YYYYmmdd_HHMMSS.png"}`; `400` wrong type, `413` too large. Filename comes from the **server clock**, never from `file.filename` |
| `POST /api/agents/{agent_name}/list_devices` | `X-Agent-Token` | — | The agent's reply **verbatim**: `{"status":"ok","devices":[{name,id,direction,is_default,is_active}]}`. `404` agent offline, `504` no reply in 5 s |
| `/` and everything else | none | — | Static frontend, `html=True` |

Notes that matter:

- **The token check is one shared secret**, duplicated as a `_check_agent_token`
  helper in `items.py`, `workspaces.py` and `agents.py` and inline in
  `screenshot.py`. All four fail closed when `AGENT_TOKEN` is unset — without
  the `not expected_token` guard, a missing env var (`None`) would equal a
  missing header (`None`). `/ws/client` applies the same guard to its own
  separate `CLIENT_TOKEN` (§5).
- **There is no `GET /api/items` list endpoint.** Studio reads the catalog
  through the unauthenticated `GET /api/workspaces`; the token goes only on
  the four mutations and on `list_devices`.
- **`list_devices` draws a distinction worth keeping:** a `4xx`/`5xx` means the
  *request* failed (agent offline, no reply in time). A handler's own
  `{"status": "error"}` is a successful round-trip reporting a failed
  operation and comes back as a normal `200`.
- **`_validate_placement`** rejects `width`/`height` < 1, negative `row`/`col`,
  a placement outside the workspace's `grid_cols`×`grid_rows`, and any
  rectangle intersection with another item in the same workspace. The reason is
  concrete: every tile is placed *explicitly*, and CSS Grid stacks
  explicitly-placed items rather than pushing them apart — an overlap renders
  one tile painted over another, and the buried one still receives no taps.
  `grid.css` handles the other half by sizing implicit tracks
  `minmax(0, 1fr)`: an out-of-bounds row otherwise lands in an `auto` track,
  which `.tile`'s `container-type: size` measures as **0px**. Since
  `renderWorkspace` started sizing the track count from the rows the items
  actually occupy (§6, "Row sizing"), an out-of-bounds item gets an *explicit*
  1fr track and cannot reach that failure from this path at all — the
  `grid-auto-rows` guard stays as the backstop, not as the only defence.

---

## 5. Protocol

### `/ws/agent`

First frame must be a hello, or the socket closes with code `4001`:

```json
{"type": "hello", "agent": "windows", "version": "0.1.0", "token": "<AGENT_TOKEN>"}
```

The token is checked **before** hub registration, so a bad token never reaches
the hub even for an instant. On success the backend broadcasts `agent_status`
and registers the socket under `hello.agent` (default `"windows"`). One socket
per agent name — a second registration logs a warning and overwrites the first.

**Backend → agent**, from an `execute`:

```json
{"cmd": "<override_type or item.type>",
 "item_type": "<item.type>",
 "params": {"...": "..."},
 "req_id": "...",
 "item_id": 7}
```

`item_type` always carries the item's own type even when `cmd` was overridden.
`force_stop` is the one handler that reads it, to derive a process name from
the original item.

From a `set_value` (no `item_type`; the live value is merged into params):

```json
{"cmd": "<item.type>", "params": {"device": "speaker", "value": 42},
 "req_id": "...", "item_id": 7}
```

From `list_devices` (no `item_id` at all; `req_id` carries an `api-` prefix so
a server-issued id can't be confused with a client-issued one):

```json
{"cmd": "list_devices", "params": {}, "req_id": "api-1725800000000-a1b2c3d4"}
```

**Agent → backend**, a result. The handler's own dict is spread **last**, so
extra keys ride along (`list_devices` returns a `devices` array) and nothing
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
(`windows:mic.muted`, …) before diffing, so two connected agents can't
overwrite each other in the single flat state dict. Clients only ever see the
namespaced form. Unchanged ticks broadcast nothing.

### `/ws/client`

**Authenticated by a `hello` handshake**, modelled on `/ws/agent`'s. The socket
is accepted, then the first frame must be:

```json
{"type": "hello", "token": "<CLIENT_TOKEN>"}
```

Only once that validates is the socket registered in the hub and sent the full
state snapshot (which it needs, having missed every diff broadcast so far).
The ordering is the point: a socket in `hub.clients` already receives every
broadcast, and the snapshot is itself part of what the gate protects.

| Condition | Close code |
| --- | --- |
| First frame is not a `hello`, or the token is wrong | `4001` |
| `CLIENT_TOKEN` unset on the server (fails closed) | `4001` |
| No frame at all within `HELLO_TIMEOUT` (5 s) | `4008` |

**Deploy ordering for this handshake is the reverse of the theme rule** in §7.
A new `ws.js` against an old backend is harmless — the `hello` frame carries no
`cmd`, so the old message loop ignores it — but a new backend against a *cached*
old `ws.js` sends no `hello` and every connection dies at `4008` after 5 s.
Frontend first, and **Ctrl+Shift+R on the phone before restarting the backend**.

`CLIENT_TOKEN` is deliberately a **different secret from `AGENT_TOKEN`**: it
ships to a phone browser over plain http, so leaking it must not also hand over
`/api/items` and agent impersonation. It buys exactly what `/ws/client` already
exposes and nothing more. `frontend/js/ws.js` sends the frame from inside the
socket's `open` handler, so every reconnect down the backoff ladder
re-authenticates for free; a `4001` close clears the stored token so a wrong
secret asks again instead of retrying itself forever.

**Client → backend** (after the handshake):

```json
{"cmd": "execute", "item_id": 7, "req_id": "1725800000000-k3n9x"}
{"cmd": "execute", "item_id": 7, "req_id": "...", "override_type": "force_stop"}
{"cmd": "set_value", "item_id": 5, "value": 42, "req_id": "..."}
```

`execute` bumps `press_count`/`last_pressed`; `set_value` deliberately does
not — a single slider drag fires it dozens of times, which would make the
counter useless for measuring discrete presses.

Both check `item["target"] in hub.agents` *before* sending. A single
persistent socket per agent means absence is a definitive answer, not a race,
so a missing agent is answered synchronously with `"agent offline"` rather
than left to time out.

**Backend → client** — results and state diffs are broadcast to *every*
connected client verbatim (the backend has no client identity to filter by, so
each client filters on the `req_id`s it actually sent):

```json
{"type": "state", "data": {"windows:mic.muted": true}}
{"type": "result", "req_id": "...", "item_id": 7, "status": "ok"}
{"type": "agent_status", "agent": "windows", "status": "online"}
{"type": "workspace_update"}
{"type": "settings_update", "settings": {"theme": "pastel"}}
```

`workspace_update` is a bare signal — the client refetches and re-renders.
`settings_update` carries its payload instead, because there is one word to
deliver and nothing to re-render.

### Timeout budgets

Every execute resolves within 5 seconds — ok, error, or timeout. A `req_id`
that never gets a matching result is a bug, not an edge case. Five budgets
stack deliberately:

| Where | Budget | What it does |
| --- | --- | --- |
| `ws/client.py` `track(req_id, 5.0)` | 5 s | Broadcasts a synthetic `"timeout"` result. Started only *after* the command actually reached an agent |
| `js/ws.js` `COMMAND_TIMEOUT_MS` | 8 s | Client-side backstop, longer on purpose so the server's real reason wins. Covers the two cases that produce no server reply at all: a command dropped by the `readyState` guard, and a socket down past the 5 s broadcast |
| `api/agents.py` `AGENT_REQUEST_TIMEOUT` | 5 s | HTTP caller parked on a future; exceeding it returns `504` |
| `handlers/audio.py` `_EXPORT_TIMEOUT` | 4 s | One `list_devices` export. Under the backend's 5 s so a wedged `SoundVolumeView.exe` surfaces as the handler's own error rather than a synthetic one |
| `handlers/audio.py` `_SWITCH_STEP_TIMEOUT` | 2 s | Each of the **two** subprocess calls inside one cycling `audio_switch`; 2 + 2 = 4 s, still under 5 |

A late result never crashes anything: `pending.resolve()` and
`agent_requests.resolve_future()` both treat an unknown `req_id` as a silent
no-op, and the client ignores `req_id`s it didn't send.

---

## 6. Tile system

### The two families of state

The seven user-visible states split into two families that work by different
mechanisms, and `button.css` documents the split explicitly.

**Family A — the item's own confirmed device state.** All four write the same
custom property, `--tile-state-color`, and nothing else:

| State | Mechanism | Resolves to |
| --- | --- | --- |
| **default** | `.tile` base rule | `var(--tile-color, var(--tile-default-color))` — i.e. `item.color`, falling back to the theme's colourless-tile token |
| **active** | `.tile.state-active` | `var(--active-color, var(--color-active))` |
| **alert** | `.tile.state-alert` | `var(--alert-color, var(--color-alert))` |
| **false_color** | `render.js` writes `--tile-state-color` **inline** | `item.params.false_color` |

Which class a boolean state toggles is decided per item by
`item.params.active_style` (`alert` → `.state-alert`, anything else →
`.state-active`), read from the DB row rather than guessed from the state
key's name.

The inline `false_color` write beats both class rules regardless of
specificity — which is exactly the ordering the false side needs, and is why
`updateTileState` must `removeProperty()` it again on the true side or a stale
false-state colour would win permanently from then on.

It writes `--tile-state-color` rather than `background-color` because the card
is no longer the only thing a state colour paints: Pastel puts it in a border
and an icon badge, Glossy runs a gradient off it, Liquid Glass mixes it into a
translucent pane. One token, four themes, no hand-kept parallel copy.

**Family B — an external condition acting on the tile.** These are classes,
they carry no colour of their own, and they live **only on pseudo-element
layers**:

| State | Class | Drawn as | Lifetime |
| --- | --- | --- | --- |
| **pending** | `.tile-pending` | `::after` — an 8 px pulsing dot, `background: currentColor` | Appears only after `PENDING_DELAY_MS` = 150 ms, so a 30 ms local toggle never flashes one |
| **ok** | `.tile-ok` | `::before` — a 2 px ring in `--active-color` | `OK_LINGER_MS` = 600 ms, and **only on tiles with no `data-state-key`** — a tile that reports real state confirms itself when the poller's next tick recolours it |
| **error** | `.tile-error` | `::before` — a 2 px ring in `--alert-color` | `ERROR_LINGER_MS` = 4000 ms, outliving the toast so "which tile" is still on screen after "why" has gone |
| **offline** | `.tile-offline` | `filter: grayscale(1)`, `pointer-events: none`, `box-shadow: inset 0 0 0 999px var(--color-scrim)` | Held while the agent is offline; scoped by `data-target`, so one agent going down doesn't grey out another's tiles |

(`.tile-ok` is the eighth state the seven-item list omits; it is included here
because it shares the mechanism and the constraint below.)

**The invariant:** none of the first three may touch `background-color`,
`box-shadow`, `filter` or `outline`. Each of those four is already claimed —
`background-color` by the inline `false_color` write (which no class rule can
outrank), `box-shadow` by `.tile-offline`'s scrim (which co-occurs), `filter`
by `:active` and `.tile-offline`, and `outline` by `:focus-visible` alone.
That last one is a fixed scar: when the ok/error rings used `outline`, a
focused tile that was also erroring rendered only one of the two, and the
keyboard user lost the focus ring precisely when a command had just failed.

`.tile-offline` dims with an inset shadow rather than `opacity` for a measured
reason: group opacity composites the label too, dragging it to 3.34:1 —
illegible rather than de-emphasised. The inset shadow paints above the
background and below the content, so the label keeps full strength.

### Row sizing: what decides a tile's height

`#grid` is `grid-template-rows: repeat(var(--rows), 1fr)` inside a flex column
that gives it all the height under the header — so **the track count is the
tile height**, and `--rows` is the only input.

`renderWorkspace` writes it from the rows the items actually occupy
(`max(item.row + item.height)`, floored at 1), **not** from the workspace's
declared `grid_rows`. Home is 3×5 with items reaching row 3, so the declared
count left one empty track — a strip of dead space along the bottom of the
phone. Adding or deleting an item in Studio now redistributes the height on
the next render with no arithmetic anywhere else. `grid_cols` is unchanged:
columns still come from the workspace, so tile *width* is stable.

Two bounds sit on top of that, both in `grid.css`:

| Bound | Value | Why |
| --- | --- | --- |
| `--row-max` | `240px` | 1fr with few rows is unbounded: a two-tile deck or the workspace selector would hand each tile ~390px of height against 115px of width. `max-height: calc(rows × --row-max + gaps + padding)` clamps the grid and `margin-block: auto` centres the remainder, so the slack is split above and below rather than pooling under the deck. Measured on 390×844: 4 rows land at 188px and never reach the cap, 3 rows land 20px short of filling; only 1–2 row decks and the selector are actually centred |
| no overflow | — | Rows **shrink** rather than scroll (8 rows = 89px each, still well over the 44px touch minimum). `#grid` deliberately has no `overflow` of its own: `html`/`body` are `position: fixed` precisely to keep a scroll chain — and iOS's elastic bounce — off this page |

### `--tile-ink`: derived text colour

A tile's text colour is **computed, not fixed**. `applyTileInk()` in
`render.js`:

1. reads the tile's computed `--tile-state-color`,
2. parses it (`#rgb`, `#rrggbb`, `rgb()`/`rgba()`; anything else returns
   `null` and the ink is left alone rather than guessed),
3. computes the WCAG contrast ratio against each of the two palette inks —
   `INK_LIGHT = #F1F5F9` and `INK_DARK = #1A1F26`,
4. writes the winner as an inline `--tile-ink`.

`button.css` sets `color: var(--tile-ink, var(--color-text))` on `.tile`, not
on `.label` — so the icon (`stroke="currentColor"` in every `ICONS` entry),
the `.subtitle` and `.tile-pending`'s dot all follow one decision.

Two call sites, and missing either is the bug this exists to prevent: after
`appendChild` in `renderWorkspace` (a detached element's computed custom
property is the empty string), and again in `updateTileState`'s boolean branch
after the state colour changes. This is why `#f2c14e` carries dark text while
`#2a2f38` carries light.

Themes whose tile surface is **not** the state colour — Pastel's white card,
Liquid Glass's dark frosted pane — cannot outrank the inline custom property,
so they pin `color` on their own `.tile` rule instead. The inline property
still resolves; the `color` declaration it feeds is what gets overridden.

### Slider tiles

A tile renders as a slider when `kind === "action" && width >= 2 && type ===
"audio_volume_set"`. It stays a `<div>`, not an `<input type="range">`, because
the fill is a plain styled div driven by `--fill-percent` — so every
affordance is written by hand: `role="slider"`, `tabindex="0"`, `aria-label`
(from `item.label`; `role="slider"` does not take its name from its contents),
`aria-valuemin/max/now/text`, and a `keydown` handler (arrows ±5, PageUp/Down
±10, Home/End).

**`setSliderValue()` is the only place a slider's value becomes visible** — it
writes `--fill-percent` and `aria-valuenow`/`aria-valuetext` together. Three
callers drive it: the drag handlers, the keyboard handler, and
`updateTileState` when the agent's poller reports the real volume. That third
one is the easy one to miss; missing it leaves the announced value stuck while
the bar shows the truth. Writing `--fill-percent` directly anywhere else
silently desyncs the two.

Drag updates are throttled to `SLIDER_THROTTLE_MS` = 100 ms, with the drag's
final value always forced through; keyboard steps are always forced, since a
single keypress is not a continuous drag.

### Everything else `render.js` decides per tile

- Pressable tiles are real `<button>`s (focusability, the button role and
  Enter/Space come from the browser). Sliders stay divs. Non-`action` tiles
  are plain divs with **no listener at all**, so they don't put empty stops in
  the tab order.
- The click handler is guarded on `event.detail === 0`: `longpress.js` already
  fires the tap from `pointerup`, and a mouse click fires both — an unguarded
  listener would execute the command twice per press. A keyboard-synthesised
  click carries `detail === 0`, a real mouse click carries `1`.
- A `contextmenu` listener gives the long-press menu a keyboard and
  right-click route (Menu key, Shift+F10).
- A **string** state value (e.g. `speaker.device_name`) lazily creates a
  `.subtitle` child; a **number** drives the fill bar; a **boolean** drives the
  state class.

### How Studio writes colours into `params` — and pins them

Studio's Appearance group has four colour inputs: `Color` (the `color`
column), `Active color`, `Alert color`, `False color`. The last three are not
columns — they live inside `params`.

The submit handler in `studio.js` merges all three in **unconditionally**, on
every save:

```js
paramsObj.active_color = fields.activeColor.value;
paramsObj.alert_color  = fields.alertColor.value;
paramsObj.false_color  = fields.falseColor.value;
```

Their pre-fills are `existingParams.active_color || "#0d9488"`,
`existingParams.alert_color || "#dc2626"`, and
`existingParams.false_color || item.color || "#2a2f38"`.

`render.js` then writes `--active-color`/`--alert-color` inline **only when the
key is present**, and an inline custom property outranks every stylesheet rule
regardless of specificity. The consequence:

> **Any item ever saved through Studio permanently pins its own active/alert
> colours to the hardcoded teal/red, and no theme's fallback can reach it
> again.** Pastel's deliberately darkened `--color-active: #0b7c72` and
> `--color-alert: #b91c1c` — chosen to clear AA against a white card — are
> dead on that item.

`render.js`'s own comment describes exactly this bug in its former shape: it
used to assign the two literals unconditionally at render time, which made
`var(--active-color, var(--color-active))` unable to reach its fallback on any
tile. That was fixed at the render layer; Studio reintroduces it at the data
layer, one item at a time, whenever someone presses Save. See §11.

`false_color` is written the same way, but its pre-fill is `item.color`, so
saving an untouched item writes back the colour that was already rendering and
nothing changes visually. It does mean the two can drift later: change `color`
alone afterwards and the false state keeps the old value.

The device pickers are the deliberate counter-example — they are written
**only** for `audio_switch` and **only** when non-empty, because an empty
select there means "the agent's list didn't load", not "none".

---

## 7. Theme system

The Dashboard's look is **two independent settings**, not one:

| Axis | Setting key | Values | Control |
| --- | --- | --- | --- |
| the *material* | `theme` | `flat`, `pastel`, `glossy`, `liquid-glass` | the header pill on the Dashboard, cycling |
| the *ground* | `mode` | `auto`, `light`, `dark` | Studio's "Deck background" picker |

Four materials × two grounds, and every combination is supported — no theme is
"the light one" any more. They are separate `setting` rows, separate
endpoints, and separate DOM attributes (`data-theme` / `data-mode` on `<html>`)
on purpose: they are orthogonal, so folding them together would mean eight
theme slugs, eight-step cycling, and no way to say "keep my theme, flip the
ground".

### The four themes

| Slug | Look | Where it is defined |
| --- | --- | --- |
| `flat` | The original: the state colour flooded across the card, plus a faint achromatic top sheen | **No block in `themes.css` at all.** It is the base stylesheets rendering untouched, which is what stops the theme drifting away from its own base |
| `pastel` | Light deck: near-white cards on soft grey, the state colour carried by a 1.5 px border and a circular icon badge (plus a 14 % tint behind the icon) | `[data-theme="pastel"]` blocks in `themes.css` |
| `glossy` | The state colour as moulded plastic: two gradient layers (a lit top edge over a body deepening to 30 % black) plus a tight drop shadow. **No blur anywhere** | `[data-theme="glossy"]` blocks |
| `liquid-glass` | Frosted panes: `color-mix` of the state colour at 42 %, a white veil, a specular top edge, over a dark blue-black ground carrying two soft radial pools | `[data-theme="liquid-glass"]` blocks |

Nearly every rule is a **token override**, not a new rule. Because every state
mechanism resolves to `--tile-state-color`, a theme mostly answers "what is a
surface, what is a border, what is text" and lets the existing rules repaint
themselves. Only where a theme changes a tile's *shape* — Pastel's circular
badge, Glossy's gradient, Liquid Glass's frost — does it add rules of its own.

Three collisions the themes had to handle explicitly, each documented in place:

- **`box-shadow`.** Glossy and Liquid Glass both claim it, and `.tile-offline`
  puts its scrim there too. At equal specificity, `themes.css` loading last
  would have won and left offline tiles undimmed — so both restate the pair,
  scrim first.
- **`--tile-default-color`.** Pastel redefines `--color-surface` as the white
  card, so a colourless tile falling back to `--color-surface` would be white
  on white (the workspace selector's tiles are exactly this case, and did
  render as an invisible border). Splitting the two token names is what lets
  each theme answer "what colour is a colourless tile" separately.
- **`--color-scrim`.** On a light deck the 60 % near-black wash reads as *more*
  emphasis, the opposite of what offline means, so Pastel inverts it to a light
  wash.

**Liquid Glass and the visual-design skill.** This is the one theme that uses
`backdrop-filter`, against the "no glass material system" guidance in
`.claude/skills/it-deck-visual-design`. It is bounded deliberately: exactly
**one** blurred layer per tile (`blur(12px) saturate(180%)`), nothing blurred
behind it, and no blur at all on the header pills or the slider fill. There is
also an `@supports not (backdrop-filter)` fallback that raises `--glass-tint`
to 100 %, because the property degrading to *no blur* leaves a 42 %
translucent tile on a dark ground — unreadably faint, not merely plainer.
Don't read this theme as a licence for a second blurred surface.

### The light / dark axis

**`auto` is not a browser or OS preference.** It means "whichever ground this
theme shipped with", and it exists because the setting is new: with only
`light`/`dark`, no default could leave every existing deck looking the way it
did — `dark` would have flipped a Pastel deck, `light` would have flipped the
other three. `auto` is the default, and nobody's deck changes on deploy.

| Theme | `auto` resolves to |
| --- | --- |
| `flat` | dark |
| `pastel` | **light** |
| `glossy` | dark |
| `liquid-glass` | dark |

That table exists in **three** places, all of them load-bearing and all of
them pointing at each other: `NATIVE_MODE` in `frontend/js/theme.js` (the one
that stamps the resolved attribute), the inline boot script in
`index.html`'s `<head>` (the pre-paint copy — a classic script cannot import a
module), and, in selector form, `themes.css`'s
`:not([data-mode="light"])` / `:not([data-mode="dark"])` guards, which are what
render correctly when there is no attribute at all. Adding a theme means
answering "which ground does it ship with" in all three.

**How the CSS is organised.** The light palette is declared **once**, for
every theme, as one rule with two ways in:

```css
[data-mode="light"],                          /* an explicit choice, any theme */
[data-theme="pastel"]:not([data-mode="dark"])  /* Pastel with no choice made  */
```

Dark needs no shared block: `base.css`'s `:root` *is* the dark palette, so
dark is the absence of that rule. The values in it are Pastel's own,
unchanged — so a light deck in any theme inherits ratios that were already
measured rather than a second, unmeasured light palette. Per-theme blocks are
two attributes deep (`[data-theme="x"][data-mode="y"]`), so they outrank it,
and the `:not()` and `[data-mode=…]` arms of a pair can never both match.

What each theme adds on top, and nothing more:

| Theme | Light variant | Dark variant |
| --- | --- | --- |
| `flat` | *nothing* — Flat light is the shared light palette exactly, the same way Flat dark is the base stylesheets untouched | — |
| `pastel` | the hairline `--tile-default-color`, and the inverted `--color-scrim` | a card lifted to `#1f2531` (a base-value card is 1.27:1 on a black page), `--tile-default-color: #7d879b`, and `--pastel-rim` |
| `glossy` | a softer drop shadow (22% black, not 50% — 50% on a pale page reads as a cut-out) | the `#0b0e13` page the shadow needs |
| `liquid-glass` | a **stronger** veil, a re-lit `--glass-edge`, light radial pools, a white-ish header pill, `opacity: 1` on the subtitle, and the inverted `--color-scrim` | everything it already had |

**Liquid Glass's dark variant is the shipped one; the *light* one is the new
work**, and it is not an inversion. Glass scatters what is behind it *toward
white* in either mode, so the veil gets **stronger** in light (42% → 20%,
against dark's 14% → 3%) and the specular top edge stays white and goes
brighter, while only the containing hairline and the drop shadow flip to a
neutral dark — on a pale ground it is shade, not light, that describes a
pane's rim. `--glass-tint` deliberately stays **42% in both**: how much state
colour a pane holds is what the theme says about the *item*, and that should
not change with the room's lights. The result is frosted white glass on a pale
ground rather than a dark theme with its numbers flipped.

**`--pastel-rim`**, the one mechanism the axis added. Pastel paints the state
colour *on* the card as a 1.5px rim and an icon badge. In light mode that is
always legible (every state colour in the project is dark, the card is white);
in dark mode it inverts, and the neutral `#2a2f38` that every "off" toggle and
uncoloured item carries is **1.14:1** on the card — the rim vanishes
(defensible: an unlit key) and *the icon vanishes with it* (not defensible: it
is the tile's content, and it was disappearing on five of the ten seeded
tiles). So dark mode mixes the state colour 60/40 toward the text ink before
drawing it, via one indirection both rules read:
`--pastel-rim: color-mix(in srgb, var(--tile-state-color) 60%, var(--color-text))`.
Light mode declares nothing and uses the `var(--pastel-rim, var(--tile-state-color))`
fallback, so it is byte-for-byte what it was.

**`--color-scrim` is per-theme, not per-mode**, and that is the one place the
axis is *not* a palette swap. `.tile-offline`'s wash sits under text whose
colour was chosen against the item's *undimmed* colour, so which way the wash
runs follows the tile, not the page:

| Theme | Light mode scrim | Why |
| --- | --- | --- |
| `flat`, `glossy` | the **dark** base wash | the tile is the item's colour and the ink is adaptive; the commonest tile on a light deck is still the dark `#2a2f38` carrying light ink, and a light wash over that is **1.82:1** |
| `pastel`, `liquid-glass` | the light wash | the card / pane is pale whatever the item's colour is, and its text is pinned dark; the dark wash measures 1.96:1 on a Liquid Glass pane |

### Contrast, re-verified across the axis

The check collapses from "4 themes × 7 states × 2 modes" once you notice
**which themes' tile text is adaptive**:

- **Flat and Glossy** paint the tile in the state colour and take their ink
  from `applyTileInk`, which reads that colour — neither of which the mode
  touches. Every tile state therefore measures *identically* in light and
  dark, and this was confirmed in the browser rather than argued: all eight
  states (default, active, alert, `false_color`, pending, ok, error, offline)
  return the same ratio in both modes. Only the page chrome changes, and the
  light palette's own values cover it (accent 6.1:1 on the page, 7.1:1 on a
  pill; muted text 5.0:1 / 5.8:1).
- **Pastel** is state-invariant instead: the card never carries the state
  colour, so the label is one number per mode — 15.6:1 light, 14.0:1 dark.
  What varies is the rim, and with `--pastel-rim` the dark side runs 3.8:1
  (neutral) / 6.9:1 (teal) / 5.4:1 (alert red), all clear of the 3:1 floor for
  a non-text UI element.
- **Liquid Glass light** is the only place a pane has to be composited by hand
  (state colour × 42% tint × veil × ground). Worst case is a pure-black item
  colour at the bottom of the veil: **6.0:1** for the label; every real state
  colour lands 7.3:1–14.6:1. The 12px subtitle was the one casualty of
  `button.css`'s `opacity: 0.65` (3.1–4.4:1), fixed the same way Pastel's
  light card fixed it — `opacity: 1`, size and weight already carry the
  hierarchy.

### The storage / sync chain

```
  ┌─ press the theme pill
  │
  ├─ theme.js cycleTheme()
  │     • next = THEMES[(i+1) % len]
  │     • applyTheme(next) IMMEDIATELY  ── optimistic paint, before the network
  │         · normalizeTheme() re-checks the allowlist
  │         · <html data-theme="…">
  │         · localStorage["itdeck:theme"] = next        (cache only)
  │         · rewrite the pill's .theme-name, aria-label and title
  │     • await putTheme(next)
  │         └─ on failure: applyTheme(previous) and rethrow → app.js toasts
  │
  ├─ PUT /api/settings/theme
  │     • 422 unless the slug is in api/settings.py's THEMES
  │     • INSERT … ON CONFLICT(key) DO UPDATE  →  setting('theme', slug)
  │     • hub.broadcast_to_clients({"type":"settings_update","settings":{…}})
  │
  ├─ every other connected panel
  │     ws.js → onSettingsUpdate → app.js → applyTheme(theme)
  │       (applied, not re-fetched — the frame already carries the value,
  │        and applyTheme re-validates before it reaches the DOM)
  │
  └─ next page load
        1. index.html's inline <head> script reads localStorage synchronously
           and stamps data-theme + data-mode before any stylesheet paints
        2. theme.js initTheme() applies the cached values, wires the button,
           then GET /api/settings and applies whatever the server says
```

The mode runs the same chain from the other end — Studio's picker →
`theme.js setMode()` (optimistic apply, roll back on failure) →
`PUT /api/settings/mode` → the same `settings_update` broadcast → every
Dashboard's `applyMode`, cached under `localStorage["itdeck:mode"]`.

**A `settings_update` frame carries only the key that changed**, so every
consumer must test a key's *presence*, not its truthiness. This is a real
trap, not a style note: `applyTheme(undefined)` normalizes to `flat`, so the
pre-existing `onSettingsUpdate(({ theme }) => applyTheme(theme))` would have
flipped every other panel's deck to Flat the first time Studio changed the
mode. `app.js` now checks `!== undefined` on each key. The `GET` is the
opposite case — it always returns both keys, so an absent one there really does
mean "no opinion" and the normalizers' defaults are correct.

Because the mode resolves *through* the theme, **`applyTheme` restamps
`data-mode`**: cycling Pastel → Glossy on an `auto` deck has to move the ground
with it. `theme.js` holds the *preference* (`auto`|`light`|`dark`) in module
state and stamps the *resolved* value (`light`|`dark`) on `<html>`, even for
`auto` — the CSS handles an absent attribute too, but a deck whose JS has run
is easier to reason about when the attribute says what you are looking at.

Two properties this buys:

- **The choice is server-side**, so it is shared by every panel pointed at this
  backend rather than being per-browser. `localStorage` is *only* a cache; the
  server value always wins a moment later, and a stale entry self-corrects.
- **The value is validated at every point it is read**, because it arrives from
  three sources that can each lie: the server (a different process), the
  `localStorage` cache (writable by anything on this origin), and a
  `settings_update` frame (a token-gated channel since `/ws/client` grew its
  `hello` handshake, but still a plain-http LAN one).

The pill's label is **derived from the slug** in `theme.js`'s `themeLabel()`
(kebab-case → Title Case, so `liquid-glass` → "Liquid Glass"), deliberately
not held in a lookup table. It is also half of the button's `aria-label`
("Theme: Flat. Switch to Pastel."), which is why both use the same function:
WCAG 2.5.3 wants the accessible name to contain the visible one.

### Adding a theme: the places that must agree

A new slug has to appear, byte-identical, in **four code locations**:

1. `THEMES` in `backend/app/api/settings.py` — the server-side allowlist
2. `THEMES` in `frontend/js/theme.js` — **order here is the cycle order**
3. the inline boot allowlist in `frontend/index.html`'s `<head>`
4. a `[data-theme="…"]` block in `frontend/css/themes.css`

Plus documentation: this file (§7), and `CLAUDE.md`'s one-line pointer.

> **Note on the count.** `settings.py`'s own comment says "four places" (the
> four code locations); `CLAUDE.md` historically said "five", counting itself
> as the documentation slot. Both describe the same set. Now that the theme
> detail lives here, the documentation slot is *this file*.

Missing #3 raises **no error at all** — it just flashes Flat on every load,
which is exactly what that script exists to prevent.

Since the light/dark axis there is one more question to answer, in **three**
of those places: **which ground does the new theme ship with?**
`NATIVE_MODE` in `theme.js`, the boot script's
`(t === "pastel") ? "light" : "dark"` line, and the theme's own
`:not([data-mode="…"])` guard in `themes.css` all have to agree. Miss the guard
and the theme renders with the wrong ground until someone picks a mode
explicitly; miss the boot copy and it flashes the wrong ground on every load.
Neither errors either.

### The deploy-ordering constraint

`backend/app/api/settings.py` — and therefore the allowlist — ships **inside
the Docker image**. The other three locations are in `frontend/`, which is
**bind-mounted** and never baked in.

Deploying the frontend ahead of the backend therefore produces a silent
revert:

1. The phone loads new `theme.js`, whose `THEMES` includes the new slug.
2. Pressing the pill cycles to it and repaints optimistically.
3. `PUT /api/settings/theme` hits the **old** backend → `422`.
4. `cycleTheme`'s catch calls `applyTheme(previous)` — the deck snaps back.

There is no error banner; the only signs are a toast and a theme that won't
stick. **Deploy the backend first**, or at minimum in the same pull + restart,
then hard-reload the phone.

---

## 8. Windows agent

### What it does

`agent.py` reads `SERVER_IP`, `AGENT_TOKEN`, `AGENT_NAME` (default `windows`)
and `SERVER_PORT` (default `"8000"`) from `.env`, connects to
`ws://<SERVER_IP>:<SERVER_PORT>/ws/agent`, sends the hello, then runs two
coroutines under one `asyncio.gather` for the life of the connection:

- **`_receive_loop`** — for each frame, look up `cmd` in `HANDLERS`, call it,
  send back `{"type":"result","req_id","item_id", **result}`. An unknown `cmd`
  replies `unknown command: <cmd>` rather than silently dropping.
- **`poll_loop`** — every second, build the state snapshot and send it.

If either raises, `gather` propagates to `main()`'s reconnect loop, which tears
down and retries with exponential backoff (1 s, doubling, capped at 30 s; reset
to 1 s whenever a connection succeeded).

`HANDLERS` today: `launch_app`, `audio_mute_toggle`, `audio_volume_set`,
`audio_switch`, `list_devices`, `screenshot`, `process_toggle`, `force_stop`,
`agent_shutdown`.

`force_stop` is the one handler called with two arguments — it receives
`item_type` alongside `params`, because it derives a process name from the
*original* item (a `launch_app`'s `path` basename, or `VPN_PROCESS_NAME` for a
`process_toggle`) rather than requiring a dedicated stored field.

`agent_shutdown` returns `{"status": "ok"}` and does nothing; the actual exit
happens back in `_receive_loop` after the result frame has gone out, via
`os._exit(0)` after a 200 ms pause. `sys.exit()` would unwind into `main()`'s
reconnect loop and immediately reconnect the agent it was just asked to stop.

A `Global\ITDeckAgentSingleton` named mutex stops a second instance starting —
Windows releases it automatically when the owner dies, so there is no stale
lock to clean up. Two agents silently overwriting each other's hub registration
is the failure this exists to prevent.

### Device enumeration and the audio switch

**There is no device cache anywhere and never was.** `list_devices()` shells
out to `tools/SoundVolumeView.exe` on every call:

```
SoundVolumeView.exe /scomma <tmp>/devices.csv /Columns
    "Name,Command-Line Friendly ID,Direction,Default,Type,Device State"
```

Parsing details that are load-bearing:

- **`utf-8-sig`**, because SoundVolumeView writes a UTF-8 BOM — plain `utf-8`
  would leave it on the first header name, making the key `"\ufeffName"`.
  Device names here are routinely non-ASCII.
- **`newline=""`**, the csv module's documented requirement; quoted fields can
  contain embedded newlines.
- **`Type == "Device"`** filters out Subunit rows (per-channel controls like
  "Front"/"Rear") and Application rows. Of ~35 rows on the maintainer's
  machine, only 4 were `Device`.
- **`Device State == "Active"`** is what tells a plugged-in endpoint from one
  Windows merely remembers — every HDMI port on the GPU, last month's headset.
- **`Default`** is not a Yes/No field: it is empty for non-defaults and
  otherwise holds the direction it is default for, so `is_default` is an
  emptiness test. A comment in `audio.py` flags one **unverified** pairing:
  this reads the `Default` column on the understanding that it pairs with the
  trailing `0` role argument the switch commands pass, taken from
  SoundVolumeView's documented role numbering rather than tested. If the cycle
  ever advances to the wrong device, that pairing is the first thing to check.
- The **`Command-Line Friendly ID`** (`DriverName\Device\Name\Direction`) is
  the identifier, never the bare `Name` — `Динамики` is both the real Realtek
  render device *and* a capture-side subunit under an unrelated fifine
  microphone. These strings are passed to subprocess exactly as read: never
  shortened, split, or parsed.

`handle_audio_switch` has **two modes**, decided by the item:

1. **Explicit pair** — if `params` carry *both* `output_device_primary` and
   `output_device_secondary` (Studio's device picker writes them), it is a
   straight A/B toggle via `/SwitchDefault`. Read all-or-nothing: pairing a
   configured primary with a fallback secondary would toggle between a pair
   nobody chose.
2. **Cycle** — otherwise it enumerates the PC's output devices *at press time*,
   filters to `Direction == "Render"` **and** `is_active`, sorts by device ID
   (so the sequence is stable rather than dependent on SoundVolumeView's row
   order), finds the current default, and advances to the next with
   `/SetDefault`. A default not in the list at all (an inactive one, or a
   capture device holding the role) starts the cycle from the top.
3. **Fallback** — if enumeration returns nothing at all,
   `OUTPUT_DEVICE_PRIMARY`/`SECONDARY` from the agent's `.env` are used as an
   A/B pair. These are now the *last resort*, not the restriction they used to
   be.

**The cycle is what makes a newly plugged speaker reachable from the deck**,
precisely because the scan and the switch are the same action — a device
plugged in one second before the press is in the cycle for that press.

That is two subprocess calls inside one execute, so each gets half the budget
(`_SWITCH_STEP_TIMEOUT` = 2 s); the 4 s total stays under the backend's 5 s.
The timeouts are not decorative: the receive loop is single-threaded, so an
unbounded subprocess call would stall the whole agent — poll ticks and every
other message included — if `SoundVolumeView.exe` ever wedges.

### Staleness / refresh behaviour, summarised

| Thing | Refresh behaviour |
| --- | --- |
| Audio device list | Re-scanned on **every** `list_devices` and every cycling `audio_switch`. No cache |
| Default output device | Re-resolved on **every** `get_volume` / `get_muted` / `get_default_output_name` call, i.e. every poll tick — the default can change between polls (unplugged headset, changed in Windows settings) |
| Polled state | Pushed every 1 s unconditionally; the **backend** deduplicates into change-only broadcasts |
| Studio's device dropdown | Loaded on demand when Type is exactly `audio_switch` and a Target is set; guarded by a `deviceRequestSeq` counter so a slow reply for a Target the user has moved away from can't repopulate the wrong agent's devices. A saved ID missing from the list is shown as `… (not in this agent's list)` rather than silently falling back to "none" |
| **The agent process itself** | **Never auto-refreshed.** Nothing in the deploy pipeline touches it — see §9 |

### Other agent-side specifics

- `launch_app` uses `os.startfile` (ShellExecute), not `Popen`: `Popen` cannot
  raise a UAC prompt, so an exe whose manifest requires admin failed silently —
  command received, no crash, app never opened.
- Volume/mute go through pycaw's **raw device enumerator**, not
  `AudioUtilities.GetMicrophone()`/`GetSpeakers()`. Those two are not
  symmetric: `GetMicrophone()` returns a raw `IMMDevice` (which has
  `.Activate()`), `GetSpeakers()` wraps its result in a helper that does not.
  Enumerating directly gives the same type for both roles, so one code path
  works for both.
- `screenshot` puts a **CF_DIB** on the clipboard: a full BMP with its 14-byte
  `BITMAPFILEHEADER` sliced off. Passing the whole file produces a clipboard
  entry that looks fine in memory and pastes as garbage everywhere. The
  clipboard is closed in a `finally`, because an open clipboard blocks every
  other app's copy/paste on that PC.
- `process.py`'s env reads happen **inside** the handlers, not at module level:
  `agent.py` imports handlers before calling `load_dotenv()`.

---

## 9. Deploy pipeline

### Machines

| Machine | Role | How it is reached | Verified? |
| --- | --- | --- | --- |
| Windows dev PC | Where the repo is edited (`C:\projects\controlhub`) and where the agent runs | local | Verified — this is the working directory |
| **Athlon** — Debian prod | Runs the backend container; repo checked out at `~/controlhub` | `ssh athlon` (an SSH config alias) | Verified — `.claude/settings.local.json` allows `Bash(ssh athlon *)`, `.claude/hooks/session_state.py` runs `ssh athlon "cd ~/controlhub && git log -1 --oneline"`, and `.claude/skills/ship/SKILL.md` drives the whole deploy over it |
| Debian test VM | Staging/test host | — | **Unverified.** Nothing in the repo names a test VM. `ansible/inventory.yml` is gitignored, and `inventory.yml.example` carries only a placeholder host (`itdeck-example`, `203.0.113.10`). It may exist; the repo does not attest to it |

### What deploys how

Two halves with completely different mechanics — this is the single most
important thing about the pipeline:

| Half | Mechanism | What updating it takes |
| --- | --- | --- |
| **Backend** (`backend/app/**`) | Baked into the Docker image (`Dockerfile` does `COPY app ./app`), published to `ghcr.io/itegin/it-deck-backend` | `docker compose pull && docker compose up -d` — i.e. a new **image** |
| **Frontend** (`frontend/**`) | **Bind-mounted** (`./frontend:/app/frontend` in `docker-compose.yml`); the Dockerfile deliberately does not `COPY frontend/` | A `git pull` on the host, then **Ctrl+Shift+R** in the browser. No image rebuild ever busts the browser cache |
| **Windows agent** (`agents/windows/**`) | Nothing. It is a manually launched local process | Close the console window, relaunch the "IT-Deck Agent" desktop shortcut, **by hand on the Windows PC** |

### `./deploy.sh` (run on Athlon)

Five steps, `set -euo pipefail`, non-zero exit on any failure. Output is in
Russian; the marker lines are what to read.

1. **Refuse to run with uncommitted edits on the server** — `✗ На сервере есть
   правки` means someone edited files directly on the host.
2. **`git pull --rebase`**, and warn about unpushed commits on the server.
3. **`docker compose pull` then `docker compose up -d`.** Note this **pulls the
   prebuilt GHCR image**; it does not build locally. (`CLAUDE.md`'s older
   wording "rebuilds" and the `ship` skill's `--build` timeout note both
   predate commit `d2a7567`, which switched this to a pull.)
4. **Health check** — polls `http://localhost:8000/health` for 20 s.
5. **md5 the `.py` files on disk against the same files inside the container**
   — `✓ Код в контейнере актуален` is the only line that proves the container
   is not stale.

`./check.sh` is the read-only status readout: git sync, container running,
container-vs-disk md5, `/health`, the tile list off `/api/workspaces`, and
whether the agent is currently connected (parsed out of the backend log).

`./backup.sh` uses `sqlite3 .backup` (the Online Backup API), **not** `cp` —
the live DB is in WAL mode, so a plain file copy can catch it mid-write. Keeps
14. It is not installed automatically; the Ansible playbook registers it as a
3 a.m. root cron job.

### CI/CD — what actually exists

Three separate statements, all true:

- **CI image build: exists, tags only.** `.github/workflows/build.yml` runs on
  `push` of a `v*.*.*` tag — **never** on a push to `main`. It builds
  `./backend` and pushes `ghcr.io/itegin/it-deck-backend:<tag>` and `:latest`.
- **CD: does not exist.** Nothing deploys automatically. A release reaches
  Athlon only when a human runs `./deploy.sh` there over SSH (the `/ship`
  skill automates the sequence, but a person still triggers it).
- **Tests in CI: none.** Nothing is run against the code at any point. The only
  test-shaped file in the repo is `agents/windows/test_mic.py`, a manual probe.
  `check.sh` and `deploy.sh`'s md5 step are the entire verification story.

> **On "Stage 11".** The repo attests to Stage 1 (`ansible/site.yml`'s header:
> the manual base setup the playbook reproduces), Stage 9 (commit `05bbff1` —
> agent autostart, backup script), Stage 10 (commit `dadead9` — the Ansible
> playbook) and Stage 12 (referenced as already past in `ansible/site.yml:57`).
> Stages 2–8 and 11 are **not recorded anywhere in the repo**. Chronologically
> the two `feat(ci)` commits (`9aa3f79`, `d2a7567` — GHCR build, then switching
> `deploy.sh` to pull) sit between Stage 10 and Stage 12, so they are the
> **likely** Stage 11 — an inference from commit order, not something the repo
> states. See §12 for the resulting gap in `CLAUDE.md`'s status table.

### Ansible provisioning (`ansible/site.yml`)

One playbook, one host group, no `roles/`. Provisions a fresh Debian 12 box:
Yandex apt mirrors, base packages, Docker via `get.docker.com` (Debian's
`docker.io` has no compose v2), a `ufw` firewall (22/80/443/8000, default-deny
inbound), nginx as a TLS reverse proxy with a self-signed `itdeck.local` cert,
the repo checkout over **HTTPS** (anonymous, so no git SSH key on the target),
`.env` copied from the example only if absent, `docker compose up -d`, the
daily backup cron job, and a **loopback-only** Prometheus + node_exporter
stack (no firewall rule; reach it with `ssh -L 9090:127.0.0.1:9090`). No
Grafana — the host has a 4 GB RAM ceiling.

Safe to re-run: an existing `.env` is never overwritten and the TLS cert is not
regenerated.

**The playbook does not describe Athlon's layout.** It checks the repo out at
`itdeck_repo_dest: "{{ ansible_env.HOME }}/it-deck"`, whereas Athlon runs from
`~/controlhub`. These are two different provisioning routes — Athlon predates
the playbook and was set up by hand (the Stage 1 steps the playbook
reproduces). Don't assume a path on one from the other.

---

## 10. Platform constraints

These are properties of the environment, not choices that can be revisited by
editing code.

- **The agent is Windows-only, by design.** It depends on `pycaw`/`comtypes`
  (Windows COM audio APIs) for mute and volume, `pywin32` for the clipboard
  DIB and the singleton mutex, and the bundled `SoundVolumeView.exe` for
  device enumeration and switching.
- **Python 3.7–3.12 for the agent.** `comtypes` does not support 3.13/3.14.
  (Range per `README.md`; **unverified** against a current `comtypes` release.)
- **The agent must run in the interactive user session**, never as a
  SYSTEM-context service — session 0 cannot reach a logged-in user's audio
  session (Windows Session 0 Isolation). `install_task.ps1` registers its task
  as the interactive user for this reason.
- **One socket per agent connection.** `ConnectionHub` keeps one `WebSocket`
  per agent name, so "not in `hub.agents`" is a definitive offline signal, not
  a race — `_handle_execute` answers it immediately rather than waiting out a
  timeout.
- **No secure context on the phone.** The Dashboard is reached over plain
  `http://<lan-ip>:8000`, so browser APIs requiring HTTPS (e.g.
  `crypto.randomUUID()`) are unavailable there; `ws.js` generates request IDs
  manually instead.
- **iOS Safari applies `:active` only if a touch listener exists** somewhere on
  the page. `app.js` ends with an empty `touchstart` on `document.body` purely
  to enable that.
- **The frontend is bind-mounted, not baked in.** `backend/Dockerfile`
  deliberately omits `COPY frontend/`, so a frontend change needs no image
  rebuild — and the container is useless without the repo checkout beside it.
- **The port lives in three files** — `.env`'s `SERVER_PORT`,
  `docker-compose.yml`'s mapping, and the Dockerfile's `uvicorn --port`. Change
  all three together.

---

## 11. Known tech debt and open issues

Ordered roughly by how likely each is to bite.

### Security / auth

1. **Auth is shared secrets with no identity, expiry or rate limiting.**
   `POST /api/screenshot`, the four `/api/items` endpoints, the two
   `/api/workspaces` mutations, and `list_devices` all check the same
   `AGENT_TOKEN` the agent's WebSocket uses; `/ws/client` checks a second,
   separate `CLIENT_TOKEN` (§5). Neither carries an identity, an expiry, or a
   rate limit, and a phone holds its token in `localStorage` on a plain-http
   LAN. Documented as sufficient for the single-user local-network scope;
   revisit if that scope changes.
2. **`GET /api/workspaces` is unauthenticated** and returns the full catalog,
   including every item's `params` — which can hold executable paths and device
   identifiers.
3. **`PUT /api/settings/theme` is an unauthenticated write** — deliberate (the
   phone has no token and nowhere safe to keep one over plain http), and
   constrained to four literals, so what it concedes is that anyone on the LAN
   can change how the deck looks.

### Frontend / theming

4. **Studio bypasses the theme tokens.** Every Save writes
   `active_color`/`alert_color`/`false_color` into `params` unconditionally,
   `render.js` turns the first two into inline custom properties, and an inline
   custom property cannot be outranked — so a Studio-saved item can never again
   pick up a theme's `--color-active`/`--color-alert` fallback. Pastel's
   AA-corrected `#0b7c72`/`#b91c1c` are the concrete casualty. Full mechanism
   in §6. *Fix shape: write the three keys only when the user actually changed
   them from the pre-fill, or move theme-aware defaults out of the item row.*
5. **Marginal contrast, knowingly left.** `fixup_toggle_off_colors`'s own
   comment measures the neutral off-state `#2a2f38` against the alert red at
   **2.78:1**, short of WCAG 1.4.11's 3:1 for state-identifying colours. Left
   as a deliberate call because closing it means repainting the shared
   `--color-alert`, which is also every error message and destructive control
   in the app. (The active-teal pair clears at 3.59:1.)
6. **Liquid Glass's *dark* contrast claim is still a comment, not a check.**
   `themes.css` says the light ink holds against every tinted pane, "measured,
   not assumed — see the audit note in `css/themes.css`'s history". That points
   at git history rather than at a number in the file, and it did not hold up
   when the light variant was measured alongside it: composited by hand, a
   near-white item colour puts the *dark* pane's label at **3.4:1** and its
   12px subtitle at 2.3:1. The light variant is now measured in the file
   (§7, worst case 6.0:1) and had its subtitle fixed; the dark side was left
   exactly as it shipped rather than being changed under a task about the
   light/dark axis. *Fix shape: the same `opacity: 1` the light variant now
   uses, plus either a stronger veil floor or an ink that reads the composited
   pane rather than the raw state colour.*
7. **`.tile-offline` dims under an ink chosen before the dimming.**
   `--tile-ink` is computed from the item's *undimmed* colour, then
   `.tile-offline` lays `--color-scrim` over the tile — so a mid-to-light item
   colour, which gets the dark ink, ends up dark-on-dark: the seeded
   `#f2c14e` measures **2.29:1** offline, `#8e5ff5` 1.43:1, `#0d9488` 1.49:1
   (the neutral `#2a2f38`, which most tiles use, is fine at 15.8:1). This is
   pre-existing and mode-independent — `themes.css`'s own comment claims
   "4.67:1 in the worst case of a near-white item.color", which recomputes to
   3.25:1 for white and much worse for saturated mid-tones. Found while
   verifying the light/dark axis and deliberately not fixed there: light mode
   was given the same dark scrim Flat and Glossy already use, so it inherits
   these ratios rather than adding new ones. *Fix shape: re-run
   `applyTileInk` against the scrim-composited colour when `.tile-offline`
   goes on and off.*
8. **The theme allowlist lives in four places** with no test tying them
   together, and missing the `index.html` boot copy raises no error at all —
   it just flashes Flat on every load. The light/dark axis adds a second
   three-place table (which ground each theme ships with), with the same
   silent-failure shape. See §7.
9. **Three placeholder tiles are not wired.** `Lights`, `Spotify`, `Sleep PC`
   still carry the prototype types `toggle`, `launch`, `run`. Pressing one
   returns `unknown command: <type>`.

### Deploy / operations

9. **`agents/windows/` is never deployed by anything.** No CI job, no
    `deploy.sh` step, no Ansible task touches it. A stale agent silently runs
    old code with no error until a handler bug that "should have been fixed"
    surfaces live. The restart is a GUI action on the Windows PC that Claude
    Code cannot perform.
10. **No CD and no tests in CI** — see §9.
11. **The deploy-ordering hazard** (frontend before backend ⇒ a new theme 422s
    and silently reverts) is structural, not a bug to fix: it follows directly
    from the image/bind-mount split. See §7.
12. **Mixed content over the nginx proxy.** `js/ws.js` opens
    `ws://${location.host}/ws/client` unconditionally. Correct on
    `http://<ip>:8000`; through the TLS proxy Ansible installs, a plain `ws://`
    from an `https://` page is blocked as mixed content. Only the direct HTTP
    path works end to end today.
13. **`ansible/site.yml` runs `docker compose up -d --build`,** but
    `docker-compose.yml` declares only `image:` and no `build:` context, so
    there is nothing for `--build` to build — the image always comes from GHCR.
    Harmless, but a stale flag.

### Correctness / consistency

14. **`SERVER_PORT` is read from `.env` by the agent only.**
    `backend/app/config.py` reads it and **nothing imports `config.py`**; the
    container's real port comes from the Dockerfile's hardcoded
    `uvicorn --port 8000` plus `docker-compose.yml`'s hardcoded mapping. That
    sits awkwardly against `CLAUDE.md`'s standing rule "SERVER_PORT must be
    read from .env, never hardcoded" — the rule is honoured on the agent side
    and not on the backend side. Changing the port means editing the three
    files listed in §10 (`.env`, `docker-compose.yml`, `Dockerfile`);
    `config.py` is a fourth place the value is *read*, but since nothing
    imports it, editing it alone changes nothing.
15. **`item.color` and `item.params` shapes are unvalidated.** `color` is a
    bare `str | None`, so `"notacolor"` is storable (`button.css` splits
    `background-color`/`background-image` into two declarations precisely so a
    bad colour can't also kill the sheen). `params` is checked for
    parseability, never for shape — which keys a `type` understands is the
    handler's business and nothing enforces it.
16. **`fixup_vpn_item` hardcodes `workspace_id = 1`,** unlike
    `fixup_day4_items`'s dynamic lookup. Safe today (there has only ever been
    one seeded workspace, and it gets id 1) but it would silently insert
    against the wrong workspace if that ever changed.
17. **`fixup_mic_item`'s second UPDATE is unguarded** and re-applies
    `params`/`icon` on every startup for any row labelled `Mic` — the same
    always-on-reapply pattern that `fixup_volume_item` had to be fixed out of
    because it reverted Studio edits.
18. **`POST /api/screenshot` is retained but unused.** The screenshot handler
    copies to the PC's clipboard now; the endpoint is kept for a possible
    future remote-viewable-screenshot feature. It still accepts uploads from
    any token-bearing caller and writes them to disk.
19. **Single-user, single-process by design.** `ConnectionHub` and `state.py`
    are module-level singletons in one process; a second backend replica would
    split the agent registry and the state snapshot in half.

---

## 12. Documentation map, and one gap

| File | Holds |
| --- | --- |
| `README.md` | What the project is, requirements, setup, Ansible, status |
| `CLAUDE.md` | Only what Claude Code needs loaded every session: core rules, deploy patterns, naming, Stage terminology, stage status |
| **this file** | Everything detailed: schema, API, tile/theme internals, agent internals, deploy pipeline, tech debt |
| `DOCUMENTATION.md` | Superseded by this file; reduced to a pointer |
| `AUDIT_REPORT.md` | Historical — the 2026-07-27 audit that produced the first `CLAUDE.md`. Several findings are long fixed; read it as a record, not as current state |

**The gap: there is no Stage status table anywhere in the repo.** `CLAUDE.md`
now carries one built only from what is attested (Stages 1, 9, 10, 12, plus the
Stage 11 inference in §9); Stages 2–8 are marked unverified. If you have the
real stage list, that table is the place to put it.
