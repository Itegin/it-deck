# IT-Deck — Technical Reference

Full technical reference for IT-Deck, written by reading the code. Current as
of **v0.3.2**.

- **What the project is and how to install it** → [`README.md`](../README.md)
- **What changed in each release** → [`CHANGELOG.md`](../CHANGELOG.md)
- **What Claude Code needs every session** (core rules, deploy patterns,
  naming) → [`CLAUDE.md`](../CLAUDE.md)
- **This file** — schema, API surface, tile/theme internals, agent internals,
  **standalone mode (§10)**, deploy pipeline, tech debt.

Start at §10 if you are running the standalone `ITDeck.exe`, which is the
primary path since v0.3.0; §9 covers the legacy Docker-on-a-server layout.

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
| `config.py` | `SERVER_PORT` read from the environment. Imported only by `standalone/launcher.py` (`run_backend`); Docker's port is hardcoded in the Dockerfile — see §12. |
| `auth.py` | The two shared-secret checks: `token_ok(env_var, presented)` (constant-time, fails closed on an unset variable) and `check_agent_token()` for routes. Every route and both WebSocket handshakes use it. |
| `db.py` | `DB_PATH = /app/data/controlhub.db`, connection factory (`PRAGMA foreign_keys = ON` per connection), schema for `workspace` / `item` / `setting`, `seed_if_empty()`, and **seven** idempotent `fixup_*` migrations. |
| `models.py` | `Item` / `Workspace` pydantic models and the query helpers `get_workspaces_with_items()`, `get_item()`, `bump_press_count()`. |
| `state.py` | In-memory current-state snapshot plus diffing. `update_state()` returns only keys whose values actually changed; `get_state()` returns a copy. Not persisted — rebuilt from the agent's next poll tick. |
| `pending.py` | Per-`req_id` timeout timers. `track(req_id, seconds, on_timeout)` schedules a synthetic failure; `resolve(req_id)` cancels it. Broadcast-only — it never hands a value back to one caller. |
| `agent_requests.py` | The request/response half, deliberately separate from `pending.py`: an HTTP handler parks on an `asyncio.Future` keyed by `req_id` and gets the agent's actual reply dict back. |
| `config_file.py` | `config_path()` (from `ITDECK_CONFIG_FILE`, standalone only) and `update_config()`, the line-preserving atomic writer for `config.env`. |
| `api/access.py` | `GET/PUT /api/access`: read the phone token, change either token at runtime (see `docs/ARCHITECTURE.md` ADR-15). |
| `ws/protocol.py` | What both sockets share: `receive_object()` (a frame → dict or `None`, never fatal), `accept_hello()` (5 s timeout, token check, close codes `4001`/`4008`). |
| `ws/hub.py` | `ConnectionHub` module-level singleton: a `set` of client sockets, a `dict` of one socket per agent name, `broadcast_to_clients()`, `send_to_agent()`. |
| `ws/agent.py` | `/ws/agent` — hello/token check *before* hub registration, result fan-out (cancel timer → resolve future → broadcast), state ingestion with agent-name namespacing. |
| `ws/client.py` | `/ws/client` — initial full-state push, then `execute` / `set_value` dispatch, each with a 5-second timeout started only after the command actually reached an agent. |
| `api/items.py` | Token-gated item CRUD. Validates `params` parses as JSON (`validate_params_json`) **and** validates grid placement (`validate_placement`). Every mutation broadcasts `workspace_update`. |
| `api/workspaces.py` | Token-gated `POST /api/workspaces` and `POST /api/workspaces/{id}/compact`. `_pack_items()` is a pure placement pass, kept DB-free so it can be exercised directly. |
| `api/screenshot.py` | Token-gated `POST /api/screenshot` — PNG ≤ 10 MB written beside the DB under `data/screenshots/`, named from the server clock. **Retained but called by nothing** (see §12). |
| `api/agents.py` | Token-gated request/response proxies to a connected agent, one shared `_ask_agent()`: `list_devices` (audio devices), `list_apps` (Start Menu programs) and `fetch_icon` (a site's icon). Replies verbatim, 5-second budget. **There is no agent-status endpoint** (agent status travels over the WebSocket only). |
| `api/settings.py` | `GET /api/settings`, `PUT /api/settings/theme`. Holds the canonical `THEMES` allowlist. The one write endpoint with no token. |

### `agents/windows/`

| File | Responsibility |
| --- | --- |
| `agent.py` | Connects, sends `hello`, runs the receive loop and the poll loop together (whichever ends first cancels the other), reconnects with exponential backoff (1 s → 30 s cap, reset after a connection that held 10 s). Owns `HANDLERS` and a `Global\ITDeckAgentSingleton` named mutex that stops two agents registering under one name. |
| `dispatch.py` | Frame → handler → result frame. Pure (no Windows imports), so it is tested on any OS. A bad frame, bad params or a raising handler becomes an error result, never a dropped connection. |
| `poller.py` | Polls local state every **1 s** and pushes the snapshot unconditionally; the backend deduplicates. Each key is read on its own (`READERS`), so one failing source drops only its own key, and its error is printed once rather than every tick. |
| `handlers/audio.py` | `audio_mute_toggle`, `audio_volume_set`, `audio_switch`, `list_devices`, plus the read functions the poller uses. Mute/volume go through pycaw's raw device enumerator; enumeration and switching shell out to the bundled `tools/SoundVolumeView.exe`. |
| `handlers/process.py` | `launch_app` (detached CreateProcess first, `os.startfile` fallback so a UAC-manifested exe can actually elevate; expands `%VAR%` in paths, optional raw `args`), `open_url` (scheme allowlist, handed to `rundll32 url.dll,FileProtocolHandler` through the same detached spawn), `process_toggle` (start/stop the configured VPN), `force_stop` (derives a process name from the *original* item's type and params). |
| `handlers/apps.py` | Studio queries, not tile actions: `list_apps` (Start Menu `.lnk` → exe path + args, plus Store apps as `%SystemRoot%\explorer.exe shell:AppsFolder\<AppID>`, read by a PowerShell child and cached 60 s) and `fetch_icon` (a site's icon → 64 px PNG data URI; HTTP/HTTPS-only opener, size caps, Google s2 fallback). Both run on the receive-loop thread -- a worker thread let garbage collection release pycaw's COM pointers off-thread and crashed the frozen agent (0xC0000005). DNS goes through a 2 s bounded lookup with GC paused. |
| `handlers/input.py` | `send_keys` (a chord such as `ctrl+shift+m`) and `media_key`; `parse_keys()` is pure and tested. |
| `handlers/power.py` | `power`: lock / sleep / restart / shutdown, scheduled 0.5 s after the reply. |
| `handlers/clipboard.py` | `clipboard_set`: text from `set_value` onto the PC clipboard, capped at 100k characters. |
| `handlers/system.py` | PC-load readers for the widget: `pc.cpu`, `pc.ram`, `pc.net_down`, `pc.net_up`. |
| `config_file.py` | `current_token()`: the agent's token re-read from `config.env` on every connect. |
| `handlers/screenshot.py` | `screenshot` — grabs the primary monitor with `mss`, converts to a CF_DIB (a BMP with its 14-byte file header sliced off) and puts it on the **PC's own clipboard**. |
| `start_agent.bat` | The launcher. Creates the desktop shortcut and icon on first run; `pause`s only on a non-zero exit, so a window left open means the agent crashed and the text in it is the error. |
| `install_task.ps1` / `uninstall_task.ps1` | Register/remove an "IT-Deck Agent" logon Scheduled Task, always as the interactive user. Per `CLAUDE.md` the task is **disabled on both PCs** and is not what runs the agent. |
| `tools/` | `SoundVolumeView.exe`, `make_icon.py`. |
| `test_mic.py` | A manual probe, not a test suite. |

### `frontend/`

| File | Responsibility |
| --- | --- |
| `index.html` | Dashboard shell. Carries the **only inline script in the project**: it reads the cached theme from `localStorage` synchronously so the first paint isn't a flash of the wrong theme. |
| `studio.html` | Desktop editor, always Liquid Glass (pinned `data-theme`, mode follows the shared setting). Links `base/button/widgets/themes/toast/studio.css` -- **not** `grid.css`, which pins `html/body` for the phone and would stop the page scrolling. |
| `js/app.js` | Boot: fetch workspaces, resolve which deck to show (`localStorage` → `?workspace=` → selector), render, wire WebSocket callbacks, map tagged failures to actionable messages. Ends with the empty `touchstart` listener iOS needs for `:active`. |
| `js/api.js` | REST fetches plus per-item `params` JSON parsing. Tags each failure kind (`unreachable`, `status`, `badReply`, `badParams`). |
| `js/ws.js` | Client WebSocket: connect/reconnect with backoff (1 s → 30 s), `sendExecute`, `sendSetValue`, manual `req_id` generation, and the in-flight bookkeeping that turns raw result frames into per-tile `pending`/`ok`/`error` phases. |
| `js/render.js` | Every DOM write: grid, quick-launch bar and tiles, the icon table and `tileIconFor()` (glyph / brand logo / text / site-icon image), the WCAG ink calculation, the last reported state (`knownState`, re-applied after every render — the backend only sends changes), the volume slider's pointer + keyboard handling, live state → colour/fill/subtitle, command-feedback classes, the workspace selector, the error state. |
| `js/studio.js` | Studio controller: loading, the agent token dialog + `api()` helper (drops the token on any 401, and before sending one that isn't printable ASCII -- typed in another keyboard layout, it would make `fetch()` throw and read as "can't reach IT-Deck"), workspace/mode pickers, compaction, the VPN setup card. |
| `js/token-check.js` | `headerSafeToken()`, the check above, apart from the DOM so node tests can reach it. |
| `js/studio-inspector.js` | Studio's editor panel, generated from the catalog (type cards, setup fields, appearance, size, "More settings" JSON for unmanaged keys). |
| `js/studio-preview.js` | Studio's deck preview: real `.tile` markup + live widgets, `+` free cells, drag-to-move. |
| `js/studio-i18n.js` | Studio EN/RU strings by `navigator.language`; keys must exist in both. |
| `js/studio-guide.js` | Studio's built-in guide dialog (7 sections, pictures in `img/guide/`, EN/RU). Opens itself once (`itdeck:studio-guide-seen`). |
| `js/brand-icons.js` | One-colour brand logos (Simple Icons, CC0) for `icon = "brand:<key>"`. No Russian services, by the author's decision. |
| `js/onboarding.js`, `css/onboarding.css` | The phone's first-run tour, once per device (`itdeck:onboarded`), its own small EN/RU table. |
| `img/guide/` | Guide pictures: shared ones at the top, Studio screenshots per language in `en/` and `ru/`. Bundled into the exe with the rest of `frontend/`. |
| `js/tile-catalog.js` | What tile types exist and what each needs configured. Must agree with agent `HANDLERS`, `WIDGETS`, `ICONS` and db.py seeds. |
| `js/widgets/` | Widget tiles: `index.js` registry (`mount(tile,item,ctx) -> destroy`, `ctx.onState` for live agent state), `clock-weather.js`, `pc-stats.js`. |
| `js/swipe.js` | Horizontal swipe on the deck → next/previous deck (touch and pen only). |
| `js/dom.js` | `el()`, the element builder Studio's dialogs share. |
| `js/studio-access.js`, `js/studio-whats-new.js`, `js/studio-decks.js` | Studio's Access (tokens), What's new and Decks (export/import/templates) dialogs. |
| `whats-new.json`, `templates/*.json` | The "What's new" notes (one entry per public version) and the deck templates. |
| `css/widgets.css`, `css/studio.css` | Widget layout (currentColor only, container queries); Studio's glass panels and forms. |
| `js/theme.js` | Fetch/PUT the theme, cycle it, apply it to `<html data-theme>`; derives the display label from the slug; re-validates every value against the allowlist before it reaches the DOM. |
| `js/longpress.js` | 500 ms stationary hold → long press; movement past 10 px cancels. |
| `js/contextmenu.js` | The long-press dialog (Force Stop / Cancel), `role="dialog" aria-modal="true"`, focus restored on dismiss. |
| `js/toast.js` | One shared `role="status"` element, 3500 ms, newest message wins. |
| `css/base.css` | The palette tokens (`--color-*`, `--tile-default-color`, `--color-scrim`), page/body rules, reduced-motion block. Linked by **both** pages. |
| `css/grid.css` | Dashboard only. Pins `html, body` (kills iOS rubber-band), makes `body` a flex column so `#deck` → `#grid` has real height, sizes explicit **and implicit** tracks, and lays out the quick-launch bar (`#dock`: a row under the grid in portrait, a column on the left in landscape). |
| `css/button.css` | The tile: the `--tile-state-color` resolution, `--tile-ink`, the sheen, the slider fill, and the four external-condition modifiers. |
| `css/themes.css` | The three themes that need blocks — Flat is the base stylesheets untouched and has none. Linked from `index.html` only. |
| `css/contextmenu.css`, `css/toast.css` | The two overlays. |
| `manifest.webmanifest`, `icons/` | PWA metadata for add-to-home-screen. |

### Repo root

`deploy.sh`, `check.sh`, `backup.sh`, `docker-compose.yml`, `ansible/`,
`.github/workflows/build.yml`, `scripts/token_fingerprint.py`, plus the docs
(`README.md`, `CLAUDE.md`, `CHANGELOG.md`, `DOCUMENTATION.md`,
`AUDIT_REPORT.md`) and `docs/screenshots/` (the images the README embeds).
(`gen_icon.py` used to sit here too; it wrote an icon nothing referenced and
still said "CH" from the ControlHub days, and was deleted.)

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
| `grid_cols` | INTEGER NOT NULL DEFAULT 3 | Enforced by `validate_placement` |
| `grid_rows` | INTEGER NOT NULL DEFAULT 5 | Enforced by `validate_placement` |

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
| `dock` | INTEGER NOT NULL DEFAULT 0 | 1 = in the quick-launch bar, not the grid. Then `col` is the place in the bar (0–6, `DOCK_MAX` = 7), `row` is 0 and the tile is a 1×1 action. Added by `init_db()` with `ALTER TABLE` when `PRAGMA table_info` lacks it — a schema step keyed on the column's absence, not a value fixup |

**Icon keys registered in `render.js`:** `lightbulb`, `music`, `moon`,
`terminal`, `mic`, `speaker`, `headphones`, `audio-switch`, `camera`,
`shield`, `power`, `globe`. Plus three more kinds (v0.5.0), all through
`tileIconFor()`: `brand:<key>` (a logo from `js/brand-icons.js`), `text`
(draws `params.icon_text`, up to 3 characters, via `textContent`) and `image`
(draws `params.icon_img`, a `data:image/png;base64,` URI ≤ 24 000 chars, via
`<img src>`). Icon markup is module-authored SVG only — `item.icon` is used as
a lookup key and never interpolated into `innerHTML`.

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
| `args` | `handle_launch_app` | Raw command-line arguments appended after the quoted path (Discord's `--processStart Discord.exe`) |
| `url` | `handle_open_url` | The address an `open_url` tile opens; http/https or an allowlisted app scheme |
| `icon_text`, `icon_img` | `render.js` `tileIconFor()` | The text or site icon for `icon = "text"` / `"image"`; Studio writes only the one the chosen icon kind uses |
| `city`, `lat`, `lon`, `show_seconds` | `widgets/clock-weather.js` | Clock & weather widget: display name, coordinates (no weather without both), seconds tick |

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

### `schema_migration`

| Column | Type | Notes |
| --- | --- | --- |
| `name` | TEXT PRIMARY KEY | e.g. `day4_items`, `vpn_item`, `close_agent_item`, `audio_switch_state_key` |
| `applied_at` | TEXT NOT NULL | `datetime('now')` when recorded |

Added by the post-v0.5.1 audit. It records which **one-shot** fixups a
database has already had, so a seeded tile the user deletes or renames stays
that way (tech debt #24, now fixed). It is additive: older builds ignore it,
and a database without it gets it from `init_db()`. `_already_applied()` and
`_mark_applied()` in `db.py` are the whole interface. The mark is committed
in the same transaction as the fixup's own writes.

### Seed and fixups

`seed_if_empty()` inserts one workspace (`Home`, 3×5) and three placeholder
items (`Terminal`, `Camera`, `Volume`) — but only on a genuinely fresh DB.
Everything after it is an **idempotent fixup that runs on every startup**,
because older installs still need backfilling. Order in `main.py` is
load-bearing:

| # | Function | What it does | Idempotency guard |
|---|---|---|---|
| 1 | `fixup_remove_placeholder_tiles` | Deletes `Lights`, `Spotify`, `Sleep PC` — prototype types no handler implements | Label **and** the dead type (`toggle` / `launch` / `run`). It used to be label-only, which deleted any user tile named "Spotify" |
| 2 | `fixup_legacy_seed` | `Terminal` → `launch_app` + Windows Terminal params | `AND kind = 'action' AND type = 'launch'` for the type; params guarded on every default it has ever shipped |
| 3 | `fixup_mic_item` | `Camera` → `Mic` / `audio_mute_toggle`, then backfills `params`/`icon` on the label it settles into | `type = 'toggle'` on the flip (a user tile named "Camera" used to be converted), then guards on the exact old values |
| 4 | `fixup_volume_item` | `Volume` → `audio_volume_set`, width 2, moved to (2,0) | `AND kind = 'action' AND type = 'run'` |
| 5 | `fixup_day4_items` | Inserts `Headphones`, `Audio Switch`, `Screenshot` | **One-shot** (`schema_migration`), with insert-if-label-missing on that one run. **Must run after #4** — Headphones takes the cell Volume's move vacates |
| 6 | `fixup_vpn_item` | Inserts `VPN` at (3,1) | **One-shot**, with `WHERE NOT EXISTS`; `workspace_id` hardcoded to `1` (unlike #5's dynamic lookup) |
| 7 | `fixup_vpn_tile_type` | An unconfigured `process_toggle` VPN tile → `launch_app` | `AND type = 'process_toggle'` and no real params — a configured toggle is a deliberate setup |
| 8 | `fixup_close_agent_item` | Inserts `Close Agent` in the first free cell below the occupied rows | **One-shot** once the tile exists; a full grid is not recorded and retries next start. The cell is computed, never hardcoded |
| 9 | `fixup_audio_switch_state_key` | Sets `Audio Switch`'s `state_key = speaker.device_name` | **One-shot**, `AND state_key IS NULL` (so a deliberately cleared key stays cleared) |
| 10 | `fixup_toggle_off_colors` | Repaints `Mic` and `VPN` off-state colour to the neutral `#2a2f38` | Guarded on the exact colour being replaced. **Runs after the inserts** — it must see the rows they create |
| 11 | `fixup_widget_types` | Repairs widget rows whose `type` #2 overwrote | `kind = 'widget' AND type = 'launch_app'`, a combination only that bug could produce. **Runs last** |

**A guard has to name the value it upgrades *from*.** `AND type <> 'the
target'` looks like one and is not: it matches every row that is not already
what the fixup wants, which includes every deliberate change. Both #2 and #4
were written that way, and the bill came due when a user replaced the
`Terminal` tile with the clock widget in Studio and left its label alone. One
restart later #2 had rewritten `type` to `launch_app` on a `kind = 'widget'`
row; `mountWidget()` then finds no widget registered for that type, declines,
and the tile falls back to its icon and label — so the Terminal tile
reappeared, on the deck and after every reload, with nothing in any log.
Reproduced against a scratch DB, which is where the table's new guards were
verified: the row survives two restarts unchanged, and #11 repairs the ones
already damaged.

`AND kind = 'action'` is on every label-matched UPDATE for the same reason
one layer up. A fixup exists to migrate seeded *action* tiles; a row that is
no longer an action is a row somebody deliberately turned into something
else, whatever its other columns say.

The fixups write to SQLite directly and therefore **bypass**
`validate_placement`; their placements are hand-verified in their own
comments. The inserts (#5, #6, #8) used to decide by label on every start,
so a seeded tile that was renamed or deleted came back; they are one-shot now
(see `schema_migration` above).

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
| `POST /api/items` | `X-Agent-Token` | `ItemCreate` (`workspace_id`, `row`, `col`, `width`=1, `height`=1, `label`, `icon?`, `color`=`#2a2f38`, `kind`, `type`, `target`=`windows`, `params`=`"{}"`, `state_key?`, `dock`=false) | The created row. `400` on bad params JSON, bad placement, or FK failure. Broadcasts `workspace_update` |
| `PUT /api/items/{id}` | `X-Agent-Token` | `ItemUpdate` — every field optional, `exclude_unset` so *omitted* ≠ *explicit null* | The updated row. Placement is re-checked against the **merged** rectangle, not just the submitted fields. `400` if nothing to update. Broadcasts `workspace_update` |
| `DELETE /api/items/{id}` | `X-Agent-Token` | — | `{"status": "ok"}`, or `404`. Broadcasts `workspace_update` |
| `POST /api/workspaces` | `X-Agent-Token` | `{"name", "grid_cols"=3, "grid_rows"=5}` | The created workspace; `position` is assigned server-side. Broadcasts `workspace_update` |
| `POST /api/workspaces/{id}/compact` | `X-Agent-Token` | — | The full re-packed item list. `404` if the workspace is missing. Broadcasts `workspace_update` |
| `POST /api/screenshot` | `X-Agent-Token` | `multipart/form-data`, `file` — must declare `image/png`, ≤ 10 MB | `{"status":"ok","filename":"YYYYmmdd_HHMMSS.png"}`; `400` wrong type, `413` too large. Filename comes from the **server clock**, never from `file.filename` |
| `POST /api/agents/{agent_name}/list_devices` | `X-Agent-Token` | — | The agent's reply **verbatim**: `{"status":"ok","devices":[{name,id,direction,is_default,is_active}]}`. `404` agent offline, `504` no reply in 5 s |
| `POST /api/agents/{agent_name}/list_apps` | `X-Agent-Token` | — | Verbatim: `{"status":"ok","apps":[{name,path,args}]}` — Start Menu shortcuts that point at an existing `.exe`. Same `404`/`504` |
| `POST /api/agents/{agent_name}/fetch_icon` | `X-Agent-Token` | `{"url": "<http(s) address>"}` (≤ 2048) | Verbatim: `{"status":"ok","icon":"data:image/png;base64,…"}` (64 px). The **agent** makes the outbound requests, never the backend. Same `404`/`504` |
| `GET /api/widgets/weather?lat&lon` | **none** | — | `{temp, code, is_day, min, max, updated_at, stale}` normalized from Open-Meteo. Coordinates snapped to 0.01° *before* the upstream URL is built; 10 min fresh cache, stale served ≤6 h on failure, else `502`; ≤64 cache entries. Unauthenticated because the phone calls it (it holds only `CLIENT_TOKEN`) |
| `GET /api/widgets/geocode?q&lang` | `X-Agent-Token` | — | Up to 8 `{name, admin1, country, lat, lon}`; `502` if the geocoder is unreachable (Studio then offers manual coordinates) |
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
- **`validate_placement`** rejects `width`/`height` < 1, negative `row`/`col`,
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

First frame must be a hello within 5 s (`4008` otherwise), or the socket
closes with code `4001`. Both sockets share this handshake and frame reader
(`app/ws/protocol.py`). A later frame that is not a JSON object is logged and
skipped, never fatal:

```json
{"type": "hello", "agent": "windows", "version": "0.1.0", "token": "<AGENT_TOKEN>"}
```

The token is checked **before** hub registration, so a bad token never reaches
the hub even for an instant. On success the backend broadcasts `agent_status`
and registers the socket under `hello.agent` (default `"windows"`). One socket
per agent name: a second registration replaces the first (the old socket is
left for the server's keepalive to reap, so two PCs on one name can't evict
each other in a loop).
Unregistering checks socket identity, so the replaced socket's late cleanup
can't remove the new one, and `offline` is only broadcast when the current
socket leaves. Before this, an agent restart told every phone the agent was
offline while it was connected.

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

**Backend → agent**, whether anyone is watching the deck:

```json
{"type": "watchers", "active": false}
```

Sent to each agent as it connects, and to all agents when the first
`/ws/client` socket arrives or the last one leaves (`ConnectionHub.sync_watchers`).
The agent reads state only while `active` is true. Commands are unaffected.
An older agent ignores the frame, and an agent that never receives it keeps
polling.

**Agent → backend**, polled state, sent every tick (while watched) regardless of change:

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

`override_type` is limited to `ALLOWED_OVERRIDES = {"force_stop"}` in
`ws/client.py`. Anything else is answered `"command not allowed"`; before,
a phone could send any agent command against any tile. Any other `cmd` is
answered, to the asking socket only, with
`{"type":"result","req_id":…,"status":"error","message":"unknown command: <cmd>"}`
(it used to be dropped; tech debt #22). A non-integer `item_id` is
`"item not found"`, and a row whose params are not a JSON object is
`"tile settings are invalid"`.

`execute` bumps `press_count`/`last_pressed`; `set_value` deliberately does
not — a single slider drag fires it dozens of times, which would make the
counter useless for measuring discrete presses.

Both check `item["target"] in hub.agents` *before* sending. A single
persistent socket per agent means absence is a definitive answer, not a race,
so a missing agent is answered synchronously with `"agent offline"` rather
than left to time out. A send that fails gets the same answer at once, where
it used to wait out the 5 s timeout.

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

A newly accepted socket is sent the full `state` first, and then one
`agent_status` frame per agent any tile references — `online` or `offline`,
computed against `hub.agents`. Without that, a client that connected while an
agent was already down would only ever have learnt about it from the *next*
connect or drop, which might never come (see §6, the clock takeover).

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

### Widget tiles

`kind = "widget"` tiles are drawn by `js/widgets/`: `mountWidget(tile, item)`
runs after the tile is appended (container queries need a sized tile) and
returns a `destroy()`. **`render.js` calls `destroyWidgets()` before each of its
three `grid.innerHTML = ""` wipes** -- the wipe removes elements, not timers, and
every `workspace_update` re-renders. Widgets use `target = "backend"`, so
`setAgentOffline()` (which selects `data-kind="action"`) never greys them, and
they never send `execute`. Unknown widget types keep the label-only fallback.
Adding one: module in `js/widgets/`, register in `WIDGETS`, add a
`tile-catalog.js` entry, and a backend provider under `api/widgets.py` if it
needs data or secrets. (A phone-side recorder won't work over plain http --
`getUserMedia` needs a secure context.)

### When the PC end is gone: the clock takes the deck

A deck whose agent has been closed is a screen full of buttons that cannot do
anything. The one tile that still works in that state is the clock widget,
because it runs entirely on the phone — which is why "the time is still there
when IT-Deck is off on the PC" was an observation before it was a feature.

`updateClockTakeover()` in `render.js` turns that into the deck's fallback
state. When it fires, `#grid` gets `.clock-takeover`, one clock widget gets
`.clock-takeover-tile`, every other tile is `display: none`, and the clock
spans `1 / -1` in both axes with its type scaled up to match (`widgets.css`).
A one-line note at the foot of the tile says why. Nothing is stored and
nothing has to be undone: the tiles come back the moment the deck can reach
the PC again.

**What counts as gone**, in order:

1. `connectionDown` — the socket to the backend is closed (`onConnectionChange`
   in `ws.js`, fired on `open`/`close`, de-duplicated so a backoff ladder does
   not re-render once a second). IT-Deck itself is not there.
2. Every agent named by an action tile on the *current* deck is offline
   (`offlineAgents`, fed by `agent_status`). IT-Deck is running; the agent is
   not.

The socket outranks the agents deliberately: with it down, what the agents are
doing is unknown, so claiming "agent offline" would be a guess. The note says
which of the two it is.

A deck with no clock widget never takes over — there would be nothing to show
— and neither does one with no action tiles, since a deck of pure widgets does
not depend on an agent in the first place.

**The one protocol change this needed.** `agent_status` had only ever been
sent when an agent connected or dropped, so a client that loaded *while* an
agent was already down was never told: it drew live-looking tiles for an agent
that could not answer, and the takeover would have waited for an event that
was not coming. `/ws/client` now sends one `agent_status` frame per
**referenced** agent (`get_referenced_agents()` — `SELECT DISTINCT target FROM
item`, because "offline" is a statement about an agent that is *not* in
`hub.agents`) immediately after the initial `state` push. No new frame type,
so an older client ignores nothing and sees nothing new.

**The face (v0.5.0).** In the takeover the clock tile drops its card in every
theme (`#grid.clock-takeover .tile.clock-takeover-tile` — the id outranks each
theme's `[data-theme] .tile` surface) and the text time is swapped for a
seven-segment SVG face `clock-weather.js` builds from the formatted time
(unlit segments at 7 % opacity, a colon that blinks unless motion is reduced;
AM/PM stays text). The text time stays in the DOM as the accessible one. The
quick-launch bar is hidden for as long as the takeover lasts
(`body.deck-takeover`), and its tiles count toward "which agents does this
deck need".

**Sizing (text face, still used on an ordinary tile).** The time is the widest thing on the screen and `white-space:
nowrap` means an overshoot clips rather than wraps, so the full-screen size is
split by character count: `mountClockWeather()` publishes `.wc-seconds` on the
tile, and `23:04:31` (eight characters) is sized smaller than `23:04` (five).
Measured on a 375px-wide viewport: 67px against a 351px tile, 256px of text.

### How Studio writes colours into `params`

`active_color`/`alert_color`/`false_color` are written **only when explicitly
chosen**, and "explicit" is decided by **key presence**: a key present in
`params` opens as a chosen colour and is written back; an absent key opens as
"Theme colour" and stays absent. Never by comparing with the teal/red defaults
-- every tile saved by the pre-v0.5 Studio carries all three keys, and an
equality test would silently delete a colour someone picked. Those older tiles
stay pinned until someone ticks "Theme colour".

Other params rules in `studio-inspector.js`: keys the catalog entry doesn't
manage are preserved (shown as "More settings" JSON); changing a tile's type
drops the previous type's managed keys; device IDs are removed only when the
device list *loaded* and "none" was picked (a failed load means "unknown").
Paths are cleaned of the quotes Explorer's "Copy as path" adds.

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
tasks for the life of the connection:

- **`dispatch.receive_loop`** — for each frame, look up `cmd` in `HANDLERS`,
  call it, send back `{"type":"result","req_id","item_id", **result}`. An
  unknown `cmd`, non-object params, a non-JSON frame or a handler that raises
  all become an error result (or a skipped frame), never an exception.
- **`poll_loop`** — every second, read each state key and send the snapshot.

Whichever ends first cancels the other, and the failure (if any) propagates
to `main()`'s reconnect loop, which retries with exponential backoff (1 s,
doubling, capped at 30 s). The backoff resets to 1 s after a connection that
held for 10 s, so a backend restart is followed by a prompt retry, but a
rejected token, which closes at once, keeps backing off. Before this it only
reset on a clean close, and a backend restart could cost 30 s.

`HANDLERS` today: `launch_app`, `open_url`, `audio_mute_toggle`,
`audio_volume_set`, `audio_switch`, `list_devices`, `list_apps`, `fetch_icon`,
`screenshot`, `process_toggle`, `force_stop`, `agent_shutdown`.

`open_url` refuses any scheme outside `OPEN_URL_SCHEMES` (http, https and the
app links the Studio presets use: discord, tg, steam, spotify, zoommtg, slack,
ms-settings) with a message, so `file:`, `ms-msdt:` and friends never reach
ShellExecute. It launches through `_spawn_detached(rundll32, "url.dll,
FileProtocolHandler <url>")` rather than `os.startfile` so the breakaway flags
of §10.4a apply: a browser opened from the deck outlives IT-Deck.

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
| **Backend** (`backend/app/**`) | Baked into the Docker image (`Dockerfile` does `COPY app ./app`); `docker-compose.yml` declares both `build: ./backend` and `image: ghcr.io/itegin/it-deck-backend:latest` | `docker compose up -d --build` — rebuilds locally from the checked-out code, tags it with the same name CI publishes to GHCR |
| **Frontend** (`frontend/**`) | **Bind-mounted** (`./frontend:/app/frontend` in `docker-compose.yml`); the Dockerfile deliberately does not `COPY frontend/` | A `git pull` on the host, then **Ctrl+Shift+R** in the browser. No image rebuild ever busts the browser cache |
| **Windows agent** (`agents/windows/**`) | Nothing. It is a manually launched local process | Close the console window, relaunch the "IT-Deck Agent" desktop shortcut, **by hand on the Windows PC** |

### `./deploy.sh` (run on Athlon)

Five steps, `set -euo pipefail`, non-zero exit on any failure. Output is in
Russian; the marker lines are what to read.

1. **Refuse to run with uncommitted edits on the server** — `✗ На сервере есть
   правки` means someone edited files directly on the host.
2. **`git pull --rebase`**, and warn about unpushed commits on the server.
3. **`docker compose up -d --build`.** Rebuilds locally from `./backend` —
   deliberately not `docker compose pull`. Commit `d2a7567` switched this to
   a GHCR pull; a pulled image only exists for tagged releases and lags
   behind plain `main` commits, which broke step 5's freshness guarantee and
   got the whole script deleted (commit `87ff3bc`) rather than fixed. v0.3.0
   restored both `deploy.sh` and `docker-compose.yml`'s `build:` context to
   the original local-build design — see `CLAUDE.md`'s Deploy section.
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
  skill automates the sequence, but a person still triggers it). This is
  deliberately decoupled from the GHCR/tag pipeline above — `deploy.sh`
  builds locally rather than waiting on or pulling a CI-built image (see
  §9's deploy.sh section).
- **Tests in CI: since v0.5.0.** `.github/workflows/ci.yml` runs on every push
  to `main` and every pull request, on `windows-latest` (the agent's modules
  import pywin32/pycaw): `pytest tests` -- the backend API through
  `TestClient` (placement and the quick-launch bar, auth on the agent queries,
  cache and MIME headers) and the agent's pure checks (`validate_url`, the LAN
  guard) -- then `node --check` on every frontend module and
  `node --test tests/frontend.test.mjs` (URL cleaning, presets vs logos, EN/RU
  key symmetry). `agents/windows/test_mic.py` is still a manual probe.

> **On "Stage 11".** The repo attests to Stage 1 (`ansible/site.yml`'s header:
> the manual base setup the playbook reproduces), Stage 9 (commit `05bbff1` —
> agent autostart, backup script), Stage 10 (commit `dadead9` — the Ansible
> playbook) and Stage 12 (referenced as already past in `ansible/site.yml:57`).
> Stages 2–8 and 11 are **not recorded anywhere in the repo**. Chronologically
> the two `feat(ci)` commits (`9aa3f79`, `d2a7567` — GHCR build, then switching
> `deploy.sh` to pull) sit between Stage 10 and Stage 12, so they are the
> **likely** Stage 11 — an inference from commit order, not something the repo
> states. See §13 for the resulting gap in `CLAUDE.md`'s status table.

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

## 10. Standalone mode

The primary way to run IT-Deck since v0.3.0: one `ITDeck.exe` on the PC being
controlled, no Docker, no second machine. §9 covers the legacy path, which
still works and is still how Athlon runs.

### 10.1 Process model

`standalone/launcher.py` is the single entry point for both a dev run
(`python standalone/launcher.py`) and the frozen exe. PyInstaller
(`--onefile`) bundles that one script, so there is no `python.exe` to spawn
children with — instead the launcher **re-invokes itself** via
`sys.executable` with a `--role` flag.

```
ITDeck.exe                       (role: launcher — supervises, prints links)
├── ITDeck.exe --role backend    (uvicorn + FastAPI + SQLite, 0.0.0.0:<port>)
└── ITDeck.exe --role agent      (agents/windows/agent.py, unmodified)
                                  connects to ws://127.0.0.1:<port>/ws/agent
```

Backend and agent stay **separate OS processes on purpose**. Each side keeps
its own reconnect/dispatch logic untouched, and restarting one does not take
down the other — the same recovery story the legacy path has.

`self_invocation()` is what papers over the dev/frozen difference: frozen,
`sys.executable` *is* the script, so no path argument; in dev it is
`python.exe`, so the script path has to be passed explicitly.

### 10.2 Configuration

Everything lives in `%LOCALAPPDATA%\IT-Deck\`:

| Path | Holds |
| --- | --- |
| `config.env` | `AGENT_TOKEN`, `CLIENT_TOKEN`, `SERVER_PORT`, plus optional hand-edited agent keys |
| `controlhub.db` | The SQLite database (same schema as §3) |
| `logs\backend.log` | uvicorn's stdout/stderr |
| `logs\agent.log` | The agent's stdout/stderr |

`load_or_create_config()` is **load-if-exists, generate-if-missing, never
regenerate**. That is load-bearing, not incidental: the phone stores its token
in `localStorage` keyed to the origin, so regenerating on restart would log
the phone out on every launch.

| Key | Default on a fresh install | Notes |
| --- | --- | --- |
| `SERVER_PORT` | `49732` (`DEFAULT_PORT`) | IANA dynamic range (49152–65535), no registered service. `find_free_port()` walks upward if taken. Hand-editable — this is the "let the user choose a port" answer; there is no picker UI |
| `CLIENT_TOKEN` | `admin` | Deliberately not random: it is the one thing a person may have to type on a phone, and on a self-hosted LAN a random secret buys little (see the auth item in §12) |
| `AGENT_TOKEN` | `admin` | Same reasoning; this one gets typed into Studio's own token prompt |
| `OUTPUT_DEVICE_PRIMARY`/`SECONDARY`, `VPN_PROCESS_NAME`, `VPN_PATH` | *(absent)* | Never auto-generated — no safe default exists. Preserved verbatim across restarts if you add them |

**`8000` was the old default and existing installs keep it.** A port change
moves the origin, which orphans the phone's `localStorage`, so the phone
re-prompts for its token exactly once afterwards. That is expected. To move an
existing install onto the new default, delete `config.env` and relaunch —
which also resets both tokens to `admin`.

`ITDECK_DATA_DIR` and `ITDECK_FRONTEND_DIR` are **additive** env vars.
`backend/app/db.py`'s `DB_PATH` and `backend/app/main.py`'s `StaticFiles`
directory both keep their original Docker-only defaults (`/app/data`,
cwd-relative `"frontend"`) when unset, so the legacy path is untouched — only
the launcher sets them, to absolute paths, because a desktop shortcut and a
frozen exe have no fixed cwd.

### 10.3 Agent supervision

The legacy path gets its stability from a human: the agent runs in a console
window, and "a window that stays open means the agent crashed" (§9). Standalone
minimizes that console two seconds after startup, so nobody sees it — the deck
just goes half-dead. The launcher therefore supervises the agent itself.

- Respawns on an **unexpected** exit, backoff `AGENT_RESTART_MIN_DELAY` (2 s)
  doubling to `AGENT_RESTART_MAX_DELAY` (30 s), reset once an agent survives
  `AGENT_HEALTHY_AFTER` (60 s).
- `AGENT_DELIBERATE_EXIT_CODES = (0, 3)` is never respawned.
- Every restart is printed to the launcher console, so a crash loop is visible
  rather than silent.
- The **backend** is not respawned: if it exits, everything stops, as before.

| Agent exit code | Means | Supervisor | `start_agent.bat` (legacy) |
| --- | --- | --- | --- |
| `0` | `agent_shutdown` tile pressed (`os._exit(0)`) | leave it stopped — until **Start the agent** in the window (v0.5.0) sets `agent_start_requested`, which the loop turns into an immediate spawn with a reset backoff and mutex budget | closes the window |
| `3` | `EXIT_ALREADY_RUNNING` — another agent holds the singleton mutex | leave it stopped | **pauses**, keeping the message readable |
| anything else | crash | respawn with backoff | pauses |

Code `3` exists precisely because those two readers want opposite things from
it. `0` would make the legacy shortcut close its window instantly on a
double-launch; `1` would make the supervisor fight a process that is correctly
refusing to run. Keep `EXIT_ALREADY_RUNNING` in `agents/windows/agent.py` and
`AGENT_DELIBERATE_EXIT_CODES` in `standalone/launcher.py` in step.

Two agent-side fixes belong to the same story:

- **`main()` catches `Exception`, not `(ConnectionClosed, OSError)`.**
  websockets' `InvalidHandshake` family (`InvalidStatus`, `InvalidMessage`)
  derives from `WebSocketException`, **not** `OSError` — verified against the
  pinned `websockets==13.1`. Before this, any moment the backend answered an
  upgrade with something that wasn't a WebSocket (a backend restart, or the
  window before uvicorn mounts its routes — which standalone hits on *every*
  launch) killed the agent process outright instead of reconnecting. `main()`
  *is* the recovery path; nothing above it can recover. Don't narrow it back.
- **The singleton mutex handle is held in a module global.** A `PyHANDLE`
  nobody holds is garbage-collected, and closing the last handle destroys the
  mutex — so the guard was only ever as durable as refcounting made it.

### 10.4 The VPN tile, and why its state matters

`process_toggle` is a **toggle**, and that makes a wrong state reading
destructive rather than cosmetic.

The handler resolves its target through `resolve_toggle_target()` in
`agents/windows/handlers/process.py`: **item params first**
(`process_name`, `path`), environment second (`VPN_PROCESS_NAME`, `VPN_PATH`).
Params-first is what makes the tile configurable on a standalone install at
all — those env vars only ever existed in the agent's `.env`, which the
launcher does not generate, so the seeded tile used to raise `KeyError` on
every press. `handle_force_stop`'s `process_toggle` branch goes through the
same helper; it had the identical bug.

Configure it from Studio's params field — note that JSON requires doubled
backslashes in a Windows path:

```json
{"active_style": "normal",
 "process_name": "v2RayTun.exe",
 "path": "C:\\Program Files (x86)\\v2RayTun\\v2RayTun.exe"}
```

`poll_loop` reports `vpn.running` from `get_watched_process_name()`. If that
name is unknown, the poller reports `is_process_running("")` → `false` **while
the VPN is actually up** — and then a tile that renders "off" kills the VPN on
the next tap, because the handler correctly sees the process running. Every
agent start re-armed that trap, which is why it presented as "the VPN closes
when the agent restarts".

So `watched_process_name()` in the launcher reads the process name straight
out of the `item` table and passes it in the agent's environment, on first
spawn and on every respawn (a name changed in Studio takes effect on the next
agent restart). Read **after** `wait_for_health()`, because the `item` table
does not exist until the backend's startup hook has run. `config.env` still
wins — setdefault semantics — and the legacy path is untouched because it sets
`VPN_PROCESS_NAME` itself.

> The toggle is still destructive on a genuine mis-tap, and there is no
> confirmation step. Correct state removes the trap, not the sharp edge.

### 10.4a Launch isolation and the command budget

Two guarantees every launch has to satisfy, both learned from real use.

**Nothing IT-Deck starts may die when IT-Deck does.** This is a stated product
requirement — the VPN client above all. `_spawn_detached()` in
`agents/windows/handlers/process.py` uses `CreateProcess` with
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`: no
inherited console, so no console-close event can reach it; its own process
group, so Ctrl+C/Ctrl+Break don't; outside any job object, so a job-wide kill
doesn't either. `CREATE_BREAKAWAY_FROM_JOB` is attempted separately, because
CreateProcess rejects it outright when the current job forbids breakaway and
losing the whole detached launch over an optional flag would be the wrong
trade.

Verified by launching an app from a tile and then hard-killing all four
IT-Deck processes: the app and the VPN both survived. Be precise about what
that is, though — **no measurement in this project has ever reproduced
IT-Deck killing a launched process.** These flags are a guarantee written into
the code so the question stops being empirical, not the result of a diagnosis.
They are not new either: commit `341426b` added them for this exact reason and
`0176612` dropped them again without comment.

**A launch must not blow the 5 s command budget** (a core rule — see the top
of this document). `handle_process_toggle` and `handle_launch_app` run
synchronously inside the agent's single `_receive_loop`, and `os.startfile()`
— ShellExecute — can take many seconds to return. Confirmed: a VPN press
returned `{"status": "error", "message": "timeout"}` while the app appeared a
few seconds later. `start_process()` therefore runs the launch on a worker
thread and waits only `_LAUNCH_BUDGET_SECONDS` (2.0) for it. A launch that
finishes inside the budget still reports its real error, which is the common
case (bad path, missing exe); one that doesn't returns success, so **"ok" here
means "the launch was started", not "the app is on screen"**.

`os.startfile()` remains as the fallback, and only as the fallback — it is the
one path that can open a `.lnk`, a document, a URL, or an exe whose manifest
demands elevation. It is also the *less* isolated path, since the shell
creates the process rather than our flags.

The seeded **Terminal** tile launches `wt.exe`, with `fallback_path` set to
`powershell.exe` (`TERMINAL_PARAMS` in `backend/app/db.py`). A bare name, not
an absolute path: Windows Terminal lives behind a per-user Store execution
alias under `%LOCALAPPDATA%\Microsoft\WindowsApps`, so a seeded absolute path
would be wrong for every other user, while `CreateProcess` searches `PATH`,
which already contains that directory (measured: launches in 0.06 s).
`fallback_path` is an optional item param `handle_launch_app` tries only when
the first target fails, covering a Windows 10 machine with no Windows Terminal
installed.

The tile also carries an explicit `process_name` of `WindowsTerminal.exe`.
Without it, `handle_force_stop` derives the name from the path and gets
`wt.exe` — and no running process is ever called that, because `wt.exe` is a
launcher alias for a process named `WindowsTerminal.exe`. Long-press → Force
Stop would match nothing and still report `ok`.

`fixup_legacy_seed` migrates existing installs, and its params UPDATE is
**guarded on the set of values this tile has actually shipped with**
(`_DEFAULT_TERMINAL_PARAMS`), so a tile someone repointed in Studio is left
alone. Append to that tuple rather than replacing it when the default changes,
or the next change silently stops upgrading installs that took the previous
one.

### 10.5 Console, info window, and the LAN address

Backend and agent stdout go to log files, never the console: uvicorn logs every
request and the agent logs roughly one line a second, which scrolled the
connection URL off screen within seconds on a real install. **The launcher's
console only ever prints what `run_launcher()` itself writes.**

`show_info_window()` is the whole desktop-side UI of a `--windowed` build.
It runs tkinter on a daemon thread (the main thread's poll loop is what keeps
the process alive and answers Ctrl+C) and is laid out as **three numbered
steps**, not a list of facts:

1. **Open the deck on your phone** -- a QR code of the dashboard URL, the URL
   itself in a read-only field, a copy button, and the runner-up addresses.
2. **Set up your tiles (optional)** -- names the VPN tile as the one thing
   that needs configuring, opens Studio, and keeps the agent token behind a
   reveal button with a sentence saying what it is for.
3. **When you're done here** -- what Minimize and Quit actually do, and where
   the logs are.

Since v0.5.6 the window is **dressed as Studio** (ARCHITECTURE ADR-17):
Studio's dark Liquid Glass tokens in `_STUDIO_TOKENS`, composited by
`_studio_palette()` and checked against the CSS by a test. The header is a
panel like Studio's top bar. The steps are rounded panels with the accent
number badge. The update notice is Studio's setup card (accent ring). Buttons
are `.btn` / `.btn-primary` / `.btn-danger` with the accent focus ring. The
rounded shapes are images made by `_rounded_png()` (Pillow, 4x supersampled)
and stretched by ttk image elements. ttk *tiles* an element's middle and
edges, and on Windows every tile of a translucent image is a separate slow
blend, so the sources are drawn about as big as the widgets
(`_PANEL_IMAGE`, `_CONTROL_IMAGE`, `_WELL_IMAGE`): a panel is one tile each
way instead of hundreds. The element's `width`/`height` keep the size each
widget asks for small. The cost is ~0.2 s more before the window appears. Every button is made by
`glass_button(parent, kind, ...)`, which derives its style from `parent` so the
style background (what shows around the rounded corners) matches what the
button sits on. Without Pillow the window falls back to flat colours.

The **tour and What's new overlays** (`open_overlay()`) sit on Studio's page
background, not on flat black. `_ground_png()` paints `--color-bg` with the
two pools from `themes.css` (`_STUDIO_POOLS`, verbatim, tested) and bakes the
card into the same image. A ttk card would fill its corners with a flat
colour, which shows as squares on a gradient. The card itself is nine-sliced
from a small antialiased tile (`_nine_slice`); a full draw is about 24 ms and
happens when the overlay opens, then again only after a window resize
(debounced by 120 ms).

The geometry holds still while the overlay is open:
- the content is measured once against every page and fixed at the tallest;
- Back is always packed, disabled on page 1, and sits to the left of the
  main button;
- the main button's width is the longer of its two labels.

A page turn only swaps label text. Before this, each click resized the card
and repacked buttons, and on Windows the relayout showed as a stutter.

**Closing an overlay** goes through `_swap_behind_curtain()`. Destroying it and
repainting the main screen is ~150 ms of work, and Windows shows every step:
the screen assembled itself in strips. Freezing redraw (`WM_SETREDRAW`)
doesn't help, because Tk paints after `WM_PAINT`, through its event queue. So
a borderless top-level holding a snapshot of the window (`_window_snapshot()`,
`PrintWindow` from the window's own DC, not a screen grab: the launcher isn't
DPI-aware) covers it, invisible until painted and with DWM's fade turned off.
The overlay goes and the screen repaints underneath (a separate top-level
doesn't clip this one's painting), then the cover is dropped: one frame.
Without Pillow or `PrintWindow` the swap still happens, just uncovered; the
cover always comes off, even if the swap fails (both tested).

Since v0.5.0 the window also has a **first-run tour**: four pages laid over
the finished window with `place()` (no second window — nothing in it can
destroy the info window), shown by itself when `config.env` did not exist at
launch, and from the header's **Tutorial** button afterwards. The
"agent is not running" line carries a **Start the agent** button, so the Close
Agent tile is no longer a one-way door.

The shape is the fix for a real report: two bare URLs, a 32-character token
and a path told a new user on a second PC nothing, least of all that the VPN
tile does nothing until it has a path.

Styled with the Dashboard's Liquid Glass *colors* (`frontend/css/themes.css`);
tkinter cannot do that theme's backdrop blur, and it has no rounded corners,
shadows or gradients either -- the hierarchy is a lighter card fill, the
numbered accent badge, one `PAD` constant and font sizes. `_detect_ui_lang()`
localizes EN/RU from the Windows UI language, and `_STRINGS` must stay
symmetric across both.

Four rules learned the hard way, all about drawing:

- **`_apply_windows11_chrome()` must run after every widget is packed.**
  Resolving the real HWND needs `update_idletasks()`, which also forces Tk to
  commit to whatever size the window has at that moment -- call it early and the
  window renders correctly styled but cropped mid-text.
- **Call `fit_window()` after adding anything post-build.** Tk auto-sizes only
  until it is given an explicit geometry; afterwards new content is clipped.
  Both the update notice and the revealed token pushed the Quit button off the
  bottom edge before this existed -- found by screenshot, not by reasoning.
- **Do not re-add the Mica backdrop (`DWMWA_SYSTEMBACKDROP_TYPE`).** It was
  there and was removed. Mica composites a material *behind* the client area;
  Tk paints that area opaque and knows nothing about it. Doing it properly
  needs `DwmExtendFrameIntoClientArea` plus a transparent client brush. Only
  the dark title bar (`DWMWA_USE_IMMERSIVE_DARK_MODE`) remains.
- **The QR code is black on white on purpose.** It is decoded by a camera, and
  scanners want the contrast and the quiet zone they were designed for.
  `qrcode` is pure Python here -- `QRCode.get_matrix()` straight onto a
  `tk.Canvas`, no image backend -- and is imported behind a `try/except` so a
  checkout without it shows the link and no code rather than no window.

**The window is topmost for `TOPMOST_RELEASE_MS` and then stops.** It raises
itself once so it is not born behind whatever launched it; it used to set the
flag and never clear it, floating above full-screen browsers and games.

`hide_console()` hides the console outright (`SW_HIDE`) and is vestigial on a
`--windowed` build. It used to minimize instead, which left a grey-white stub
rectangle above the taskbar -- reported with a screenshot, and exactly what
Windows draws for a minimized window with no taskbar button to shrink into.

**The info window owns stopping IT-Deck**: "Quit IT-Deck" beside "Minimize",
which iconifies. Quit sets a `threading.Event` and nothing more --
`run_launcher()`'s supervisor loop still owns tearing the processes down,
because doing it from the tkinter thread would race that loop and leave
orphans. The title bar's X is bound to the same confirmed Quit.

#### The update check

A daemon thread calls `newer_version_available()` once at startup: one request
to the GitHub releases API, 10s timeout, and **every** failure returns `None`
silently -- offline, DNS, the unauthenticated rate limit, a JSON schema
change. The answer travels back through a `queue.Queue` that the Tk thread
polls with `root.after`, because tkinter may only be touched from the thread
running its mainloop; `_update_check_worker` always queues exactly one item so
the poll terminates. `UPDATE_CHECK=0` in `config.env` opts out, and a
`config.env` written before the key existed reads as enabled.

The 10s timeout is measured: on the maintainer's machine the TLS handshake to
`api.github.com` intermittently exceeds 5s with a VPN up, while a successful
request completes in ~0.7s. A short timeout here does not fail loudly, it
silently disables the feature on exactly the networks it exists for.

**Only the exe can be out of date.** The phone is not an installed client --
it loads the frontend from the running build -- so there is no stale client on
that side and a dashboard banner would duplicate this one.

#### Picking the LAN address

`detect_primary_and_other_ips()` **ranks** this PC's addresses; it does not
ask the routing table. It used to: the UDP-connect-to-8.8.8.8 trick returns
the source address of the default route -- and **a running VPN owns the
default route**, which for an app shipping a VPN tile is aimed squarely at its
own users. Measured with v2RayTun up, it offered the tunnel's `172.16.0.1/30`
while the phone could only reach `192.168.0.15`. That was survivable as a line
of text with alternatives printed beneath it; it is not survivable as the
address baked into a QR code.

`_rank_address()` sorts on address facts first and names last, because a name
blocklist can never be complete and the user's own VPN client is not on it:

| Signal | Why |
| --- | --- |
| RFC1918 or not | A home LAN is private. Rejects Radmin VPN's `26.x.x.x` -- public IANA space borrowed by a virtual-LAN product |
| Subnet width | A LAN is /24 or wider; a point-to-point tunnel is a /30 or /32 |
| Which private range | `192.168/16` is what consumer routers hand out, then `10/8`, then `172.16/12` -- the range Hyper-V and tunnels squat in |
| Adapter name hint | Last, and only as a tie-break |

The routed address is kept as a tie-break between two equally plausible LAN
adapters (a laptop on Wi-Fi and Ethernet at once). Verified against six
setups, including Hyper-V, WSL, WireGuard and this machine's real one. The
window lists the runner-ups under the link, which only the console used to
print. `check_reachable()` remains a soft firewall hint and does not select
the address.

### 10.6 Building

```powershell
standalone\build.ps1
```

Auto-installs a compatible Python (3.12 via winget) if nothing suitable is on
PATH, generates `agents/windows/icon.ico` (gitignored, so a fresh clone has
none), then runs PyInstaller `--onefile`.

**Rebuilding after any `backend/`, `frontend/` or `agents/windows/` change is
required** — none of it is bind-mounted the way the Docker path's frontend is.
Close a running `ITDeck.exe` first, or the build cannot overwrite it.

The built exe gives itself a desktop shortcut (`IT-Deck.lnk`) on first launch —
`ensure_desktop_shortcut()`, the same `WScript.Shell`/`CreateShortcut`
technique and idempotency check `start_agent.bat` uses, only fired from Python
and only when frozen.

### 10.8 Windows shutdown

Shutting the PC down with IT-Deck running used to leave it switched on. The PC
stopped at Windows' "this app is preventing you from shutting down" screen and
waited for a click nobody was there to give — twice, overnight, which is what
this section exists to prevent happening again.

**It is not the info window.** That was the first suspect, since its close box
opens a modal confirmation dialog and a modal dialog during shutdown is a
classic stall. Tk clears itself: sending a real `WM_QUERYENDSESSION` to a real
Tk `HWND` shows Tk answering `TRUE` immediately and mapping the message onto
the `WM_SAVE_YOURSELF` protocol — *not* onto `WM_DELETE_WINDOW`. The Quit
dialog never opens on this path.

**It is PyInstaller's `--onefile` bootloader**, the part of `ITDeck.exe` that
is not ours. One-file mode runs the program as a *child* process and keeps the
parent alive to delete the unpacked `_MEIxxxx` temp directory after it. So the
parent can survive long enough to do that, it creates its own hidden top-level
window — class `PyInstallerOnefileHiddenWindow` — and on `WM_QUERYENDSESSION`
it calls `ShutdownBlockReasonCreate()` and then waits in its `WM_ENDSESSION`
handler for the child to exit. (All of this is legible in the bootloader
binary's own log strings: `LOADER: creating hidden window to capture system
shutdown events...`, `LOADER: handling session shutdown - giving the child %d
ms to exit...`.) The child never exited: `run_launcher()` is a supervisor loop
that only stops on Quit or on the backend dying. The parent waited, timed out,
and Windows named `ITDeck.exe` as the reason the shutdown had stalled.

**The fix lives in the child** — `install_session_end_handler()` — and is
simply "exit when asked":

- It registers a hidden window of its own, class `ITDeckSessionEndWatcher`,
  with a message loop on its own thread. It must be a **real top-level
  window**: a message-only (`HWND_MESSAGE`) window is never sent session-end
  messages at all, which is the trap in doing this the obvious way.
- `WM_QUERYENDSESSION` is the **primary** trigger, not `WM_ENDSESSION`. The
  bootloader registers its block reason during the *query* phase, so by the
  time `WM_ENDSESSION` is dispatched the blocking screen may already be up.
  Acting a phase early means a shutdown somebody cancels at that screen also
  stops IT-Deck — a relaunch, against a PC that stays on all night.
- Every route (the watcher's two messages, Tk's `WM_SAVE_YOURSELF`) sets one
  `threading.Event`, so the teardown is idempotent and the process exits
  exactly once, in one place.
- **The teardown runs inside the window procedure, before it answers.** Not
  after, and not handed to another thread — see §10.8a, which is the entire
  reason v0.4.2 did not work. Only the process's own exit is deferred, by
  `SESSION_END_EXIT_DELAY` (50 ms), so the procedure gets to return `TRUE`
  rather than vanishing mid-message; by the time that pause starts there is
  nothing left that matters.
- `DefWindowProcW` and the `WNDPROC` prototype declare a pointer-sized
  `LRESULT`. Left at the ctypes default the return value truncates to a C
  `int`, and a truncated answer to `WM_QUERYENDSESSION` reads as `FALSE` —
  which would *add* a shutdown blocker rather than remove one.
- The teardown is `terminate()` on the agent and the backend, then a **wait
  for each of them to actually be gone** (`SESSION_END_CHILD_GRACE`, one
  second each, not one second shared), then `os._exit(0)` — not `sys.exit`,
  which would run interpreter shutdown with a tkinter mainloop on another
  thread, i.e. one more place to hang.

Sleep and hibernate are unaffected: those are `WM_POWERBROADCAST` and never
send `WM_QUERYENDSESSION`.

#### 10.8a The second blocker, and why v0.4.2 did not fix it

v0.4.2 answered Windows correctly and still left the PC on all night. It set a
`threading.Event` and let a waiter thread do the actual work 150 ms later, so
that the window procedure could return `TRUE` first. **Windows does not wait
150 ms.** It terminates a process as soon as its windows have answered, so
that teardown never ran at all: the backend and the agent were left running.

Both of them are re-invocations of this same exe sharing its unpacked
`_MEIxxxx` directory — each has `python312.dll` mapped out of it — so
PyInstaller's parent could not delete the directory, and when it cannot it
puts up a modal `MessageBoxW`: **"Failed to remove temporary directory"**. A
modal dialog during shutdown is a shutdown that never finishes. Same PC, same
night, different blocker.

The tell was in `launcher.log`: the `Windows is ending the session` line was
missing from the run that failed, which is the proof the handler's work never
reached it. That line's *presence* is now the signal the path ran.

The lesson generalises past this bug: **anything that must happen at session
end has to be finished before the window procedure returns.** There is no
"later" — later is after the process has been killed.

This also explains the `_MEIxxxx` drift. Each of those directories is ~90 MB;
this machine had accumulated 35 of them, 1.26 GB, every one the residue of a
parent killed before it could tidy up. `sweep_stale_unpack_dirs()` now clears
them at startup, skipping the directory the running copy is using and treating
any failure to delete as "in use, leave it alone".

**Measured, before and after.** Windows' real sequence is
`WM_QUERYENDSESSION` to every top-level window, then `WM_ENDSESSION` to every
top-level window; both builds were driven through exactly that against a
running exe, with `SendMessageTimeout` timing each reply.

The first two columns below are the polite test: send the messages, then wait
for the app to tidy up. That test is what let v0.4.2 look fixed. The third is
the honest one — it terminates the launcher child the instant it has answered,
which is what Windows actually does, and is the only column that separates
v0.4.2 from v0.4.3.

| | v0.4.1 (polite test) | v0.4.2 (polite test) | v0.4.2 (honest test) | v0.4.3 (honest test) |
| --- | --- | --- | --- | --- |
| Block reason registered by the parent | `Needs to remove its temporary files.` | same | same | same |
| **Parent's reply to `WM_ENDSESSION`** | **never returned (120 s)** | 330 ms | **never returned (90 s)** | **120 ms** |
| Backend / agent afterwards | both alive | both gone | **both alive** | both gone |
| Modal "Failed to remove temporary directory" | — | no | **yes** | no |
| The run's `_MEIxxxx` directory | left behind | deleted | **left behind** | deleted |
| `ITDeck.exe` workers left | all | none | 2 | none |

`WaitToKillAppTimeout` is five seconds by default, so a handler that never
returns is a handler Windows gives up on — and the string it then puts on the
"preventing you from shutting down" screen is the block reason in the first
row, which is why that screen named `ITDeck.exe` and offered a button nobody
was awake to press.

One note for anyone re-running this. A synthetic `WM_ENDSESSION` strands the
parent: it returns from its handler and then waits to be killed by a shutdown
that is not actually happening. That one surviving process is an artifact of
the test, not a leak — it has already deleted the temp directory and released
its block reason by then. Kill it afterwards.

### 10.8b Ending the task, and the second copy

§10.8 and §10.8a are about a shutdown IT-Deck is *told* about. This one is
about the deaths it is told nothing about, which is the same modal dialog
reached from the other side.

**Reported:** the agent was closed with Task Manager's **End task** and
Windows put up **"Failed to remove temporary directory: ...\_MEI000038302"**.

**Read off the machine afterwards**, before anything was touched: `ITDeck.exe
--role backend` and `--role agent` still running, both children of a launcher
pid that no longer existed; `launcher.log` missing both the `Quit requested`
line and the `Windows is ending the session` line for that run; and
`_MEI000038302` holding 26.5 MB of its ~90, with `PIL\` and `win32\` stamped
minutes after the rest. That is PyInstaller's parent getting partway through
its cleanup and stopping at `python312.dll`, which the two orphans still had
mapped.

**Why no handler could have caught it.** End task is `TerminateProcess`. It
sends no `WM_QUERYENDSESSION`, unwinds no stack, runs no `finally:` and fires
no `atexit`. Every teardown route IT-Deck had was code, and code is exactly
what does not run. This is not a regression of §10.8a — it is the case that
section could never have covered.

**The fix is a job object** (`create_child_job()`), because its enforcement is
the kernel's rather than ours: `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` terminates
every process in the job when the last handle to it closes, and the kernel
closes this process's handles however this process died. The backend and the
agent are assigned to it as they are spawned — `assign_to_child_job()`, on
*every* agent spawn, respawns included.

**Both breakaway flags are load-bearing.** Job membership is inherited by
child processes, and CLAUDE.md's standing rule is that anything the agent
launches must survive IT-Deck closing — so a naive job would take the VPN
client down with the deck, which is a worse bug than the one being fixed.
`JOB_OBJECT_LIMIT_BREAKAWAY_OK` is what makes `_spawn_detached`'s
`CREATE_BREAKAWAY_FROM_JOB` succeed instead of falling through to its
no-flags branch (§10.4a); `JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK` covers the
launches that cannot ask for themselves, namely `start_process()`'s
`os.startfile()` fallback for `.lnk` files, documents and anything demanding
elevation, which takes no `creationflags` at all.

Verified end to end against the frozen exe, not just at the ctypes level: a
temporary `launch_app` tile pointing at Notepad, pressed over `/ws/client`,
then the launcher force-killed. All four `ITDeck.exe` processes gone, the
run's `_MEIxxxx` directory deleted, no modal dialog — and Notepad still
running.

| `Stop-Process -Force` on the launcher | v0.4.3 | v0.4.4 |
| --- | --- | --- |
| Backend / agent afterwards | both alive | both gone |
| Modal "Failed to remove temporary directory" | yes | no |
| The run's `_MEIxxxx` directory | left behind, half-deleted | deleted |
| An app launched from a tile | survives | survives |

#### The sweep was eating live directories

`sweep_stale_unpack_dirs()`, added in v0.4.3, reasoned that `shutil.rmtree`
"cannot delete a file another process has open, so a live copy's directory
fails the attempt rather than being half-deleted". Half true, and therefore
wrong: rmtree deletes everything it *can* before it reaches the locked file
and raises. Measured on this machine — start IT-Deck, wait out the 60-second
cutoff, launch a second copy — the second copy's sweep took **32 files** out
of the first copy's directory, which then holds whatever it had not imported
yet.

Every deletion is now claimed by `os.rename` first. Windows refuses to rename
a directory that has a file open anywhere underneath it (`Access to the path
... is denied`, verified against a running instance), and the rename either
moves the whole tree or moves nothing — so a live directory is never touched,
and only a directory proven unused is deleted, under its `.itdeck-stale`
name. A claim left behind by a delete that failed part-way is recognised and
retried on the next start. The same test is what makes it safe to be looking
at `_MEI*` at all: that prefix is PyInstaller's, so some of those directories
belong to other applications, and a running one of those is protected by
exactly the same refusal.

#### A second copy now says so

Starting IT-Deck twice used to fail in a way nobody could read. The second
copy's backend loses the bind and exits, but `wait_for_health()` gets its
`200` from the *first* copy's backend — the two are byte-for-byte identical —
so the launcher carried on, the agent lost the singleton mutex, and a second
later the supervisor noticed `backend_proc` was gone and stopped. IT-Deck
vanished a few seconds after launch, with the explanation in a log file
nobody has a reason to open.

`port_already_serving()` asks the question *before* the backend is spawned,
which is the only time it can be answered, and `wait_for_health()` now also
watches the process so a backend that is never coming is noticed in about a
second instead of twenty. The answer is shown in a native MessageBox
(`report_startup_failure()`) rather than printed: the exe is `--windowed`, so
there is no console, and the info window is precisely what these failures
happen instead of. Startup only — a modal dialog during *shutdown* is the bug
§10.8a exists to fix.

### 10.9 Removing IT-Deck

IT-Deck has no installer, so it has no uninstaller either, and "just delete
the exe" was wrong in four ways -- the data directory, the Desktop shortcut,
the firewall rules and the unpacked temp directory all outlive it. Step 3 of
the info window carries a quiet **Remove IT-Deck from this PC** button that
does the whole job.

**What it removes**, and this is the complete inventory -- IT-Deck writes
nothing to the registry, installs no service and registers no scheduled task:

| Thing | Where | Removed by |
| --- | --- | --- |
| config, tokens, tile database, logs | `%LOCALAPPDATA%\IT-Deck\` | inline, after the children are stopped |
| Desktop shortcut | `%USERPROFILE%\Desktop\IT-Deck.lnk` | inline |
| Windows Firewall rules naming the exe | firewall store | one elevated PowerShell, only if rules exist |
| `ITDeck.exe` | wherever it was run from | the helper below |
| the unpacked `_MEIxxxx` directory | `%TEMP%` | the helper below |

**Two locks against a stray click**, and deliberately not three: a modal
dialog that lists what will go, and Cancel holding the focus with `Return`
bound to *closing* rather than to the red button. The reflex that dismisses
every other dialog dismisses this one too, so the only route to the deletion
is to aim at the red button and click it. (An earlier version also demanded
that the word DELETE be typed; that is the kind of ceremony people learn to
perform without reading, and it was dropped.)

**Why there is a helper process at all.** A running exe cannot delete itself:
Windows holds the image file for as long as anything is mapped to it, and in
onefile mode that is two processes, not one. So `spawn_uninstall_helper()`
writes a small PowerShell script to `%TEMP%` that retries each target until
it succeeds or `UNINSTALL_RETRY_SECONDS` is up, then deletes itself. Retrying
rather than waiting on a process name, because "the file is no longer locked"
is the actual condition, and one deadline *per target* rather than one shared
across the loop -- the same mistake the session-end teardown had to be fixed
out of.

**The creation flags on that helper cost two rounds of testing**, and neither
failure said anything out loud:

1. `DETACHED_PROCESS | CREATE_NO_WINDOW` -- mutually exclusive per the
   CreateProcess docs. It fails with `ERROR_INVALID_PARAMETER`, so the helper
   was never launched and the uninstall quietly left the exe in place.
2. `DETACHED_PROCESS` alone -- `powershell.exe` starts, finds it has no
   console, and exits `0` without running a line of the script. Measured
   across all four combinations; only the `CREATE_NO_WINDOW` ones ran.

So it is `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, plus
`CREATE_BREAKAWAY_FROM_JOB` with the same fallback shape `_spawn_detached()`
uses -- without the breakaway, §10.8b's kill-on-close job would take the
helper down at the moment it is needed.

**Elevation.** Firewall changes need admin rights, so this is the one step
that can raise a UAC prompt. It is asked for *before* the teardown, while the
window the user just clicked in is still on screen -- a consent dialog
appearing after the app has vanished is how malware behaves -- and only after
an unelevated query has confirmed there are rules to remove. Declining it
costs that step and nothing else.

**The elevated command goes through a file, and that is the second bug this
section paid for.** It was first written as a command string nested inside
another command string -- `Start-Process powershell -Verb RunAs -ArgumentList
'-Command',"<filter>"` -- and the outer shell expands `$_` in that inner text
before the elevated child is ever started, so the filter the whole thing is
built on arrives as `Where-Object { .Program -eq '...' }`. It removed nothing
and reported nothing; v0.4.5 shipped with it. Found only by testing the
elevated branch on a disposable copy carrying two firewall rules of its own:
**2 rules before, 2 after**, with every other part of the uninstall correct.
It now writes the one-line filter to a `.ps1` in `%TEMP%` and elevates
`-File`, which has no second round of parsing; the script deletes itself, and
a declined prompt is cleaned up by the caller. Re-measured the same way:
**2 rules before, 0 after.**

**Never from a source checkout.** `sys.executable` is the frozen exe only
when frozen; in a dev run it is `python.exe`. `perform_uninstall()` refuses
when `is_frozen()` is false, and the window does not draw the button there,
which is two guards for one mistake that would otherwise delete an
interpreter.

### 10.7 Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Phone can't reach the printed URL | The "primary" address is a virtual adapter (§10.5) | Use an address from the "this PC also has" line |
| Phone can't reach any address | Windows Firewall — a cancelled prompt leaves **Block** rules for `itdeck.exe` on the Public profile | Delete those inbound rules, or make the network Private |
| Phone asks for a token it shouldn't | `config.env` was regenerated, or the port changed (new origin, empty `localStorage`) | Open the freshly printed `?token=` link once |
| Token rejected with close code `4001` | Stale token on the phone vs. the one in `config.env` | The token in `config.env` is authoritative; re-open the printed link |
| VPN tile errors "not configured yet" | No `process_name`/`path` in its params and no env fallback | Set them in Studio (§10.4) |
| Tiles that need the PC stop responding | Agent died | The launcher respawns it; check the console for restart lines and `logs\agent.log` for why |
| Backend didn't come up in time | Port conflict, or a startup exception | `logs\backend.log` |
| Windows won't shut down / stops on "preventing you from shutting down" | A build older than v0.4.3 — PyInstaller's one-file parent holds the shutdown (§10.8) | Update to v0.4.3 or newer |
| Modal "Failed to remove temporary directory" at shutdown | v0.4.2 only: the backend and agent outlived the teardown and kept the unpack directory open (§10.8a) | Update to v0.4.3 |
| `%TEMP%` filling with `_MEIxxxx` directories | Residue of the above; ~90 MB each | v0.4.3 sweeps them at startup |

---

## 11. Platform constraints

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

## 12. Known tech debt and open issues

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

4. **Tiles saved by the old Studio still pin active/alert colours.** The new
   Studio no longer writes them (§6), but existing rows keep the keys until
   "Theme colour" is ticked per tile. No migration, on purpose: a key can't be
   told apart from a deliberate choice.
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
8. **The theme allowlist lives in four places.** It is now checked:
   `tests/frontend.test.mjs` fails when `settings.py`, `theme.js`, the
   `index.html` boot script and `themes.css` disagree on the slugs, or when the
   two `MODES` lists do. The light/dark *native ground* table (§7) is still
   unchecked.
9. ~~**Three placeholder tiles are not wired.**~~ They are removed on
   startup by `fixup_remove_placeholder_tiles` (§3), which now matches their
   dead types as well as their labels.

### Deploy / operations

9. **`agents/windows/` is never deployed by anything.** No CI job, no
    `deploy.sh` step, no Ansible task touches it. A stale agent silently runs
    old code with no error until a handler bug that "should have been fixed"
    surfaces live. The restart is a GUI action on the Windows PC that Claude
    Code cannot perform.
10. **No CD** — see §9. Tests do run in CI since v0.5.0, but only the pure and
    API layers: nothing exercises audio, launching, or the Tk window.
11. **The deploy-ordering hazard** (frontend before backend ⇒ a new theme 422s
    and silently reverts) is structural, not a bug to fix: it follows directly
    from the image/bind-mount split. See §7.
12. ~~**Mixed content over the nginx proxy.** `js/ws.js` opened
    `ws://${location.host}/ws/client` unconditionally.~~ **Fixed:** it uses
    `wss:` on an https page.
13. ~~**`ansible/site.yml` runs `docker compose up -d --build`,** but
    `docker-compose.yml` declares only `image:` and no `build:` context, so
    there is nothing for `--build` to build — the image always comes from
    GHCR.~~ **Fixed in v0.3.0.** This was not harmless: it's the same gap
    that made `deploy.sh`'s own `--build` a silent no-op, discovered live
    during a v0.3.0 deploy (container stayed stale after a successful `git
    pull` + `docker compose up -d --build`). `docker-compose.yml` now
    declares `build: ./backend` alongside `image:`, so both `deploy.sh` and
    `ansible/site.yml`'s `--build` actually rebuild.

### Correctness / consistency

14. **`SERVER_PORT` is read from `.env` by the agent only.**
    `backend/app/config.py` reads it and only the standalone launcher imports
    it; the
    container's real port comes from the Dockerfile's hardcoded
    `uvicorn --port 8000` plus `docker-compose.yml`'s hardcoded mapping. That
    sits awkwardly against `CLAUDE.md`'s standing rule "SERVER_PORT must be
    read from .env, never hardcoded" — the rule is honoured on the agent side
    and not on the backend side. Changing the port means editing the three
    files listed in §11 (`.env`, `docker-compose.yml`, `Dockerfile`);
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
17. ~~**`fixup_mic_item`'s second UPDATE is unguarded**~~ — **fixed.** It
    re-applied `params`/`icon` on every startup for any row labelled `Mic`,
    the same always-on-reapply pattern `fixup_volume_item` had to be fixed out
    of, and it silently reverted Studio edits to that tile. Both statements are
    now guarded on the values they upgrade from, and the three cases (fresh,
    legacy, user-edited) are covered by a test run.
18. **`POST /api/screenshot` is retained but unused.** The screenshot handler
    copies to the PC's clipboard now; the endpoint is kept for a possible
    future remote-viewable-screenshot feature. It still accepts uploads from
    any token-bearing caller and writes them to disk.
19. **Single-user, single-process by design.** `ConnectionHub` and `state.py`
    are module-level singletons in one process; a second backend replica would
    split the agent registry and the state snapshot in half.
20. ~~**The printed "primary" LAN address can be a virtual adapter.**~~ —
    **fixed**, and it had to be: the address now goes into a QR code, where a
    wrong guess is not a dead link on a line of text but a code that simply
    does not work. Addresses are ranked rather than taken from the default
    route (§10.5, `_rank_address`). Root cause, confirmed live, was not
    Hyper-V but *this app's own VPN tile*: a running VPN owns the default
    route, so the UDP trick returned the tunnel's `172.16.0.1/30`.
21. ~~**`process_toggle` has no confirmation.**~~ **Fixed:** "Ask before
    running" (`params.confirm`) shows Run / Cancel on the phone first, and is
    on by default for new Program on/off, Power and Close agent tiles.
    Existing tiles are unchanged until the option is ticked in Studio.
22. ~~**Unknown `cmd` values on `/ws/client` are silently dropped.**~~
    **Fixed:** answered with an error result to the asking socket (§5).
23. **A stray white rectangle was reported on the desktop during real use.**
    Two plausible causes were removed in v0.3.1 without either being
    reproduced under observation: the Mica backdrop on the tkinter info window,
    and `MoveWindow(..., bRepaint=TRUE)` immediately before the console is
    minimized (§10.5). If it recurs, both hypotheses are wrong and the
    diagnosis starts over.

24. ~~**Renaming or deleting a seeded tile brings it back.**~~ **Fixed:** the
    insert fixups are one-shot via the `schema_migration` table (§3). The same
    audit fixed two worse relatives: `fixup_remove_placeholder_tiles` deleted
    any tile labelled "Spotify" (or Lights / Sleep PC) on every start, and
    `fixup_mic_item` converted any action tile labelled "Camera" into Mic.
    Both are now guarded on the placeholder's dead type.
25. **No application heartbeat on the phone socket.** A socket that iOS left
    silently dead is only replaced when the page becomes visible again, the
    network comes back, or the OS closes it. See `docs/ARCHITECTURE.md`
    ADR-12 for why a ping/pong was not simply added (deploy ordering).
26. **The weather endpoint is unauthenticated and not rate limited.** The
    snapped cache bounds memory, not upstream traffic: a LAN caller sweeping
    coordinates can keep threadpool workers busy on Open-Meteo fetches.
    Accepted for a home LAN.

---

## 13. Documentation map, and one gap

| File | Holds |
| --- | --- |
| `README.md` | What the project is, standalone setup, the tiles, Studio, troubleshooting |
| `docs/DEVELOPMENT.md` | The practical guide: quick start, architecture diagrams, the WebSocket protocol with examples, config, Docker, testing, debugging, and step-by-step extension recipes |
| `docs/ARCHITECTURE.md` | Architecture decision records: why each structural choice was made, including the ones kept only for compatibility |
| `README.ru.md` | The same page in Russian; the two are kept in step |
| `docs/legacy-server.md` | Setup for the pre-v0.3.0 Docker-on-a-server deployment, and the Ansible playbook (moved out of the README) |
| `CHANGELOG.md` | What changed in each tagged release, newest first |
| `CLAUDE.md` | Only what Claude Code needs loaded every session: core rules, deploy patterns, naming, Stage terminology, stage status |
| **this file** | Everything detailed: schema, API, tile/theme internals, agent internals, deploy pipeline, tech debt |
| `DOCUMENTATION.md` | Superseded by this file; reduced to a pointer |
| `AUDIT_REPORT.md` | Historical — the 2026-07-27 audit that produced the first `CLAUDE.md`. Several findings are long fixed; read it as a record, not as current state |

**The gap: there is no Stage status table anywhere in the repo.** `CLAUDE.md`
now carries one built only from what is attested (Stages 1, 9, 10, 12, plus the
Stage 11 inference in §9); Stages 2–8 are marked unverified. If you have the
real stage list, that table is the place to put it.
