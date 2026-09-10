# IT-Deck

Self-hosted universal control surface that turns an old iPhone into a deck
for your PC. Single-user, single-process app — no multi-tenancy, no auth
beyond the agent token.

Three pieces, one persistent WebSocket each: **backend/** (FastAPI + SQLite,
one Docker container), **frontend/** (vanilla JS/CSS PWA, no build step,
bind-mounted), **agents/windows/** (Python agent on the controlled PC).

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
  the agent; the backend still hardcodes it in three places — see the
  reference doc's tech-debt section.)

## Deploy

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
