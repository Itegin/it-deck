# IT-Deck

Self-hosted control surface: a phone (the everyday one or a spare) becomes a
deck for a Windows PC, in its browser. Audience: gamers, streamers, remote
workers, students, enthusiasts -- never pitch it as "for old phones". Single-user. Three pieces, one WebSocket each: **backend/** (FastAPI + SQLite),
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
  `settings.py`, `theme.js`, `index.html` boot script, `themes.css`. The PC
  window's `_STUDIO_TOKENS` (launcher.py) copy Studio's CSS (tested).
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

## Conventions

- **Performance is a feature** (it runs next to games): state is read only
  while a Dashboard is connected (`watchers` frame); backend and agent run
  below normal priority; no per-second process walks. The Tk window has no
  animation and no per-frame redraw: an overlay page turn changes text,
  never geometry.
- **PC window = Studio's look.** `_STUDIO_TOKENS`/`_STUDIO_POOLS` are CSS
  copied verbatim (tested). Rounded shapes are Pillow PNGs, nine-sliced by
  ttk. Buttons come only from `glass_button(parent, kind, ...)`: ttk paints
  the style background behind rounded corners, so it must match the parent.
  Everything must still work flat, without Pillow.
- **Logos** are Simple Icons paths only, one colour via `currentColor`.
  Never hand-drawn, never a wordmark (unreadable at tile size).
- **Licence** is PolyForm Noncommercial. A new dependency or bundled file
  goes into `THIRD-PARTY-NOTICES.md`. SoundVolumeView and Open-Meteo's free
  tier rule out anything paid; the notices list what to replace first.
- Checks before a push: `ruff check .`, `pytest -q`,
  `node --test tests/frontend.test.mjs`, `node --check` on changed JS.

## Releases

A `v*.*.*` tag only builds the exe (artifact). Publishing is a manual
**Run workflow** and happens only when the user says so; never trigger it
yourself. Internal milestone tags share one `whats-new.json` entry (≤5
bullets, EN+RU, named for the public version); `scripts/check_release.py
vX.Y.Z` must pass before tagging. Pushing tags from a cloud session is
blocked (HTTP 403): the user pushes them.

## Deploy

- **Standalone:** changes in `backend/`, `frontend/`, `agents/windows/` need
  `standalone/build.ps1` (or a tag, see Releases; steps in CONTRIBUTING.md).
- **Legacy (Athlon):** `ssh athlon ./deploy.sh` (backend, local build);
  `./check.sh` to verify. Frontend is a `git pull`, then Ctrl+Shift+R on the
  phone. Backend before frontend for themes; frontend first for the
  `/ws/client` handshake.
- A legacy agent is restarted by hand from its shortcut, so ask the user.
