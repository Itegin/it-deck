# IT-Deck

Self-hosted universal control surface that turns an old iPhone into a deck
for your PC. Single-user, single-process app — no multi-tenancy, and no auth
beyond two shared secrets: `AGENT_TOKEN` (every write endpoint and `/ws/agent`)
and `CLIENT_TOKEN` (the Dashboard's `/ws/client`).

Three pieces, one persistent WebSocket each: **backend/** (FastAPI + SQLite),
**frontend/** (vanilla JS/CSS PWA, no build step), **agents/windows/**
(Python agent on the controlled PC). As of v0.3.0, **standalone/** bundles
all three into one `ITDeck.exe` that runs directly on the controlled PC —
see "Standalone mode" below. The pre-v0.3.0 layout (backend as a Docker
container on a separate server, frontend bind-mounted) still works and is
documented under "Deploy" as the legacy path.

> **Anything detailed lives in
> [`docs/IT-Deck_Tech_Reference.md`](docs/IT-Deck_Tech_Reference.md)** —
> DB schema, the full API surface, message shapes, the tile state /
> `--tile-state-color` / `--tile-ink` mechanism, theme internals, Windows
> agent internals, the deploy pipeline, and current tech debt. Read it before
> changing any of those; don't re-derive them from the code.

## Naming

Public name: **IT-Deck**. Internal identifiers — the repo folder, logging
namespaces (`controlhub`, `controlhub.api`, `controlhub.ws`), and the SQLite
filename (`controlhub.db`) — intentionally remain `controlhub`. This is a
deliberate decision from the rebrand, not an incomplete rename.

Development phases are called **Stage N**, never "Day N" (renamed in commit
`1b64a7e`). Use "Stage N" in commits, comments and docs.

## Core rules

These three are non-negotiable and get checked on every relevant change:

- **iOS Safari applies `:active` only if a touch listener exists somewhere on
  the page** (an empty `touchstart` on `document.body`). Verify it is still
  present in `app.js` before assuming CSS `:active` rules will work on iPhone.
- **Every execute command must resolve within 5s: ok, error, or timeout.** A
  `req_id` that never gets a matching result is a bug, not an edge case.
- **`SERVER_PORT` must be read from `.env`, never hardcoded.** (Honoured by
  the agent and by standalone mode, which wires `backend/app/config.py`'s
  previously-dead `SERVER_PORT` into real use. The legacy Docker path still
  hardcodes it in three places — see the reference doc's tech-debt section.)

## Standalone mode (v0.3.0+)

- **`standalone/launcher.py`** is the single entry point, for both
  `python standalone/launcher.py` (dev) and the frozen `ITDeck.exe`
  (`standalone/build.ps1`, PyInstaller `--onefile`). It generates
  `AGENT_TOKEN`/`CLIENT_TOKEN`/`SERVER_PORT` once into
  `%LOCALAPPDATA%\IT-Deck\config.env` (load-if-exists, generate-if-missing —
  never regenerate on restart, or the phone's stored token stops working),
  then re-invokes itself via `sys.executable` with `--role backend` /
  `--role agent` to run both as separate OS processes from one exe (there's
  no bundled `python.exe` to spawn otherwise).
- **Backend and agent stay separate processes on purpose** — closing/
  restarting one doesn't take down the other, matching the existing
  "close the console window, relaunch" agent recovery story. The agent
  connects to `ws://127.0.0.1:<port>/ws/agent`, unmodified from how it talks
  to a remote backend.
- **`ITDECK_DATA_DIR`/`ITDECK_FRONTEND_DIR` env vars are additive.**
  `backend/app/db.py`'s `DB_PATH` and `backend/app/main.py`'s `StaticFiles`
  directory both default to their original Docker-only values (`/app/data`,
  cwd-relative `"frontend"`) when unset, so the legacy Docker/Ansible path
  is unaffected — only the standalone launcher sets them, to real absolute
  paths, since a desktop shortcut/frozen exe has no fixed cwd.
- **Rebuilding `ITDeck.exe` after any `backend/`, `frontend/`, or
  `agents/windows/` change is required** — none of it is bind-mounted like
  the Docker path. Run `standalone\build.ps1`.

## Deploy (legacy: Docker on a separate server)

- **`./deploy.sh` (on Athlon, via `ssh athlon`) updates the backend only.** It
  pulls git, pulls the prebuilt GHCR image, restarts the container, health-checks
  it, and md5s the container's code against disk. `✓ Код в контейнере актуален`
  is the only line that proves the container isn't stale.
- **The frontend is bind-mounted, not baked into the image.** A `git pull` on
  the host is the whole deploy; no rebuild busts the browser cache, so finish
  with **Ctrl+Shift+R** on the phone.
- **⚠ Deploy the backend before the frontend.** The theme allowlist ships
  *inside* the image while `theme.js` / `index.html` / `themes.css` are
  bind-mounted — frontend-first means a new theme `422`s and silently reverts
  on the phone, with no error banner.
- **`CLIENT_TOKEN` must be set in `.env` before deploying the backend.**
  `/ws/client` fails closed: with it unset, every Dashboard connection is
  refused with close code `4001` and the deck shows no tiles. Hand it to the
  phone once via `http://<server>:8000/?token=<value>` (stored in
  `localStorage`, stripped from the URL) or the prompt the Dashboard shows.
- **⚠ For the `/ws/client` handshake specifically, deploy the frontend *first* —
  the reverse of the theme rule above.** The two orderings are opposites because
  the compatibility runs opposite ways: a new `ws.js` against an old backend is
  harmless (the `hello` frame carries no `cmd`, so the old message loop ignores
  it), but a new backend against a *cached* old `ws.js` sends no `hello` at all
  and every connection dies at `4008` after 5 s. The phone's cache is the real
  hazard here, so **Ctrl+Shift+R before restarting the backend**, not after.
- **`./check.sh`** is the read-only status readout (git sync, container
  freshness, `/health`, agent connected).
- **The Windows agent is started manually**, from the "IT-Deck Agent" desktop
  shortcut (`agents/windows/start_agent.bat`). The Scheduled Task from
  `install_task.ps1` is **disabled on both PCs**; ignore it. A window that
  stays open means the agent crashed and the text in it is the error.
- **Restarting the agent after any `agents/windows/` change is a GUI action on
  the Windows PC that Claude Code cannot perform.** Nothing in the deploy
  pipeline touches the agent — a stale one silently runs old code until a
  handler bug surfaces live. Ask the user to close the console window and
  relaunch the shortcut, then verify via `check.sh`.

## Stage status

Only what the repo actually attests to. Stages 2–8 are not recorded anywhere
in the codebase or git history — fill them in if you know them, don't guess.

| Stage | What | Status | Evidence |
| --- | --- | --- | --- |
| 1 | Manual server base setup (packages, Docker, firewall) | done | `ansible/site.yml` header |
| 2–8 | — | **unverified** | not recorded in repo |
| 9 | Agent autostart, backup script, honest README | done | commit `05bbff1` |
| 10 | Ansible playbook for server provisioning | done | commit `dadead9` |
| 11 | CI image build → GHCR; `deploy.sh` switched to pull | done (**inferred** — the commits sit here chronologically, the stage number is not stated) | commits `9aa3f79`, `d2a7567` |
| 12 | Ansible hardening (Yandex mirror, nginx TLS, Prometheus) | done | `ansible/site.yml:57` refers to it as past |

**CI/CD today:** the GHCR image build exists but runs on `v*.*.*` tags only,
never on `main`. There is **no CD** (a human runs `deploy.sh` over SSH) and
**no tests in CI**.
