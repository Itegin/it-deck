# IT-Deck

Self-hosted control surface: an old iPhone becomes a deck for a Windows PC.
Single-user. Three pieces, one WebSocket each: **backend/** (FastAPI + SQLite),
**frontend/** (vanilla JS/CSS PWA, no build step), **agents/windows/** (Python
agent). **standalone/** bundles all three into one `ITDeck.exe` (the main path).
Legacy: backend in Docker on Athlon, frontend bind-mounted.

Details (schema, API, protocol, tiles, themes, agent, standalone §10, tech debt
§12) are in [`docs/IT-Deck_Tech_Reference.md`](docs/IT-Deck_Tech_Reference.md).
Read it before touching those areas. History: [`CHANGELOG.md`](CHANGELOG.md).

Version: `ITDECK_VERSION` in `standalone/launcher.py`. Bump it in the same
commit as the tag. Public name is IT-Deck; internal identifiers (`controlhub`
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
- **Keep in step:** `tile-catalog.js` ↔ agent `HANDLERS` ↔ `WIDGETS`
  (`js/widgets/index.js`) ↔ `ICONS` (`render.js`) ↔ db.py seeds. Theme slugs:
  `settings.py`, `theme.js`, `index.html` boot script, `themes.css`.
- **Widgets** (`kind=widget`): `mount(tile,item)` returns `destroy()`, and
  `render.js`/Studio preview call `destroyWidgets()` before wiping the grid.
  Colours only via `currentColor`.
- **Studio** is always Liquid Glass, doesn't link `grid.css`, and has
  symmetric EN/RU strings in `studio-i18n.js`. State colours are written only
  if the key is present/explicitly chosen (never by comparing values).
- `/api/widgets/weather` is unauthenticated (the phone calls it) and bounded by
  a snapped-coordinate cache. Everything that writes needs `X-Agent-Token`.
- Standalone: tokens default to `admin`, port `49732`, and `config.env` is
  load-if-exists (never regenerate). The exe is `--windowed`: no console, and
  the info window is the only UI, so nothing may destroy it. `build.ps1` and
  `.github/workflows/release.yml` duplicate the PyInstaller call (absolute
  paths), so change both together.
- Everything the agent launches must survive IT-Deck closing
  (`_spawn_detached`). Launches run off the receive loop, and "ok" means
  started. Elevation-required targets are reported, not swallowed.
- The VPN tile is `launch_app` + `state_key vpn.running`. The launcher passes
  `VPN_PROCESS_NAME` to the agent at spawn, so a new path lights the indicator
  only after a restart.
- `kill_process()` never kills IT-Deck's own processes (`protected_pids()`).

## Deploy

- **Standalone:** any change in `backend/`, `frontend/` or `agents/windows/`
  needs `standalone\build.ps1`. Releases: a `v*.*.*` tag makes CI attach
  `ITDeck.exe`.
- **Legacy (Athlon):** `ssh athlon ./deploy.sh` (backend, local build);
  `./check.sh` to verify. Frontend is a `git pull`, then Ctrl+Shift+R on the
  phone. Backend before frontend for themes; frontend first for the
  `/ws/client` handshake.
- The Windows agent is restarted by hand (desktop shortcut). Claude can't do
  it, so ask the user.
- No tests and no CD in CI.
