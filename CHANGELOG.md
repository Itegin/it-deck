# Changelog

Newest first. Versions are git tags; `main` between tags is unreleased.

Detail lives in [`docs/IT-Deck_Tech_Reference.md`](docs/IT-Deck_Tech_Reference.md) —
standalone mode is §10, tech debt is §12.

---

## v0.3.1 — 2026-09-15

First real-use pass over standalone mode. Everything here came out of running
`ITDeck.exe` on an actual PC with an actual phone, not from reading the code.

### Fixed

- **The agent no longer dies on a backend restart.** Its reconnect loop caught
  only `(ConnectionClosed, OSError)`, but websockets' `InvalidHandshake` family
  derives from `WebSocketException`, **not** `OSError`. Any moment the backend
  answered an upgrade with something that wasn't a WebSocket — a restart, or
  the window before uvicorn mounts its routes, which standalone hits on *every*
  launch — killed the agent process outright instead of reconnecting.
- **The launcher now supervises the agent and respawns it** (2 s → 30 s
  backoff, reset after 60 s healthy), printing every restart to its console.
  Deliberate exits are left alone: `0` (the `agent_shutdown` tile) and `3`
  (`EXIT_ALREADY_RUNNING`, the singleton mutex). Code `3` rather than `0`
  because `start_agent.bat` pauses on a non-zero exit — that pause is how the
  legacy shortcut keeps its "already running" message readable.
- **The VPN tile could kill the VPN it was meant to report on.**
  `process_toggle` is a toggle, and the agent only learned its process name
  when a press arrived, so a freshly started agent reported `vpn.running =
  false` while the VPN was up. The tile rendered "off", a tap meaning "on"
  killed it — and every agent start re-armed the trap, which is why it
  presented as "the VPN closes when the agent restarts". The launcher now seeds
  the name from the `item` table into the agent's environment, on first spawn
  and every respawn.
- **The VPN tile is configurable at all on a standalone install.** It read
  `VPN_PROCESS_NAME`/`VPN_PATH` from the agent's `.env`, which standalone never
  generates — a `KeyError` on every press. Now item params come first, env
  second; `handle_force_stop` had the identical bug and shares the fix.
- **The agent's singleton mutex actually holds.** Its `PyHANDLE` was discarded
  and garbage-collected, which destroys the mutex — the guard was only as
  durable as refcounting made it.
- **Studio stops asking for the agent token on every page load.** Persisted to
  `localStorage`, and dropped on a `401` so a wrong value isn't re-sent forever.
- **Two candidate causes removed for a stray white rectangle on the desktop:**
  the Mica backdrop on the tkinter info window, and the `MoveWindow(...,
  bRepaint=TRUE)` immediately before the console minimizes. Neither was
  reproduced under observation — see tech debt §12.22.

### Changed

- **A fresh install now picks port `49732`, not `8000`** (IANA dynamic range).
  `8000` is heavily contested on a developer machine. Existing installs keep
  their port — `config.env` is load-if-exists by design. To move one, delete
  `config.env` and relaunch; the phone then re-prompts for its token once,
  because the origin changed.
- `SERVER_PORT` in `config.env` is documented as hand-editable — that is the
  "choose your own port" answer; there is no picker UI.

### Known open

- The printed "primary" LAN address can be a virtual adapter (Hyper-V, VPN);
  the real one is on the "this PC also has" line. §12.20.
- `process_toggle` still has no confirmation on a genuine mis-tap. §12.21.

---

## v0.3.0 — 2026-09-15

**Standalone mode.** One `ITDeck.exe` runs the backend, the agent and the
frontend on the PC being controlled — no Docker, no second machine. The
pre-0.3.0 Docker-on-a-server layout still works and is now the "legacy" path.

- `standalone/launcher.py` as the single entry point, re-invoking itself with
  `--role backend` / `--role agent` to get two processes out of one frozen exe.
- Config generated once into `%LOCALAPPDATA%\IT-Deck\config.env`, never
  regenerated. `CLIENT_TOKEN` and `AGENT_TOKEN` default to `admin`.
- Desktop shortcut on first launch; a GUI info window with the Dashboard and
  Studio links and a copy button; backend/agent logs to files so the
  connection URL stays on screen.
- `deploy.sh` restored to its local-build design (a GHCR-pull version had gone
  stale against plain `main` commits).

## v0.2.1 — 2026-09-07

Reverted the flat-card visual style from v0.2.0.

## v0.2.0 — 2026-08-29

CI builds the backend image and pushes it to GHCR on version tags.

## v0.1.1 — 2026-07-30

Fixed iOS rubber-band scrolling on the Dashboard; removed leftover header
spacing.

## v0.1.0 — 2026-07-27

Stage 9: agent autostart, backup script, an honest README.
