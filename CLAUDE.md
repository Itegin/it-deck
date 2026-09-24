# IT-Deck

Self-hosted control surface: an old iPhone becomes a deck for a Windows PC.
Single-user. Three pieces, one WebSocket each: **backend/** (FastAPI + SQLite),
**frontend/** (vanilla JS/CSS PWA, no build step), **agents/windows/** (Python
agent). **standalone/** bundles all three into one `ITDeck.exe` (the main path).
Legacy: backend in Docker on Athlon, frontend bind-mounted.

Details (schema, API, protocol, tiles, themes, agent, standalone §10, tech debt
§12) are in [`docs/IT-Deck_Tech_Reference.md`](docs/IT-Deck_Tech_Reference.md).
Read it before touching those areas. How-to and extension recipes:
[`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md); decisions:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). History: [`CHANGELOG.md`](CHANGELOG.md).

Version: `ITDECK_VERSION` in `standalone/launcher.py`. Bump it in the same
commit as the tag. A public release also needs its `frontend/whats-new.json`
entry (`scripts/check_release.py` enforces both). Public name is IT-Deck; internal identifiers (`controlhub`
loggers, `controlhub.db`, repo folder) stay `controlhub` on purpose. Phases are
"Stage N", never "Day N".

## Core rules

- iOS applies CSS `:active` only if a touch listener exists: keep the empty
  `touchstart` on `document.body` in `app.js`.
- Every execute resolves within 5 s: ok, error or timeout. A `req_id` with no
  result is a bug.
- `SERVER_PORT` comes from config (`.env` / `config.env`), never hardcoded.

## Things that are easy to break

- **Startup fixups in `db.py` must be guarded on the value they upgrade from.**
  They run on every start, and a bare `WHERE label=...` reverts Studio edits.
  One-shot inserts go through `_already_applied`/`_mark_applied`.
- **Keep in step:** `tile-catalog.js` ↔ agent `HANDLERS` ↔ `WIDGETS`
  (`js/widgets/index.js`) ↔ `ICONS` (`render.js`) + `BRAND_ICONS`
  (`brand-icons.js`, no Russian services) ↔ db.py seeds. The agent's
  `OPEN_URL_SCHEMES` ↔ the catalog's `URL_SCHEMES`; backend `DOCK_MAX` ↔
  `studio-preview.js` `DOCK_MAX`. `ALLOWED_OVERRIDES` (`ws/client.py`) ↔ the
  long-press menu in `app.js`. Theme slugs:
  `settings.py`, `theme.js`, `index.html` boot script, `themes.css`.
- **Widgets** (`kind=widget`): `mount(tile,item)` returns `destroy()`, and
  `render.js`/Studio preview call `destroyWidgets()` before wiping the grid.
  Colours only via `currentColor`.
- **Studio** is always Liquid Glass, doesn't link `grid.css`, and has
  symmetric EN/RU strings in `studio-i18n.js`. State colours are written only
  if the key is present/explicitly chosen (never by comparing values).
- `/api/widgets/weather` is unauthenticated (the phone calls it) and bounded by
  a snapped-coordinate cache. All writes need `X-Agent-Token`.
- Standalone: tokens default to `admin`, port `49732`, `config.env` is
  load-if-exists (never regenerate). The exe is `--windowed`: no console, the
  info window is the only UI, so nothing may destroy it. `build.ps1` and
  `.github/workflows/release.yml` duplicate the PyInstaller call (absolute
  paths) -- change both together.
- Backend and agent live in the launcher's kill-on-close job; a new child must
  join it, and its breakaway flags are what keep agent launches alive after
  IT-Deck closes (`_spawn_detached`, run off the receive loop -- "ok" =
  started; `kill_process()` spares IT-Deck itself).
- The VPN tile is `launch_app` + `state_key vpn.running`. The launcher passes
  `VPN_PROCESS_NAME` to the agent at spawn, so a new path lights the indicator
  only after a restart.

## Deploy

- **Standalone:** changes in `backend/`, `frontend/`, `agents/windows/` need
  `standalone/build.ps1`. A `v*.*.*` tag only *builds* the exe (a CI artifact);
  publishing is a manual workflow run -- see CONTRIBUTING.md "Releasing".
- **Legacy (Athlon):** `ssh athlon ./deploy.sh` (backend, local build);
  `./check.sh` to verify. Frontend is a `git pull`, then Ctrl+Shift+R on the
  phone. Backend before frontend for themes; frontend first for the
  `/ws/client` handshake.
- A legacy agent is restarted by hand from its shortcut, so ask the user.
