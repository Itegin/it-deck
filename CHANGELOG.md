# Changelog

Newest first. Versions are git tags; `main` between tags is unreleased.

Detail lives in [`docs/IT-Deck_Tech_Reference.md`](docs/IT-Deck_Tech_Reference.md) —
standalone mode is §10, tech debt is §12.

---

## Unreleased

Batched on purpose: these land on `main` without a tag, and go out together in
the next release.

### Added

- **The info window is a three-step setup guide, with a QR code.** It used to
  show two bare URLs, a raw 32-character token and a log path, which told a
  brand-new user nothing — including the thing this project gets asked about
  most, that the VPN tile does nothing until it is given a path in Studio.
  Step one now carries a QR code of the dashboard URL, so the phone needs no
  typing at all; step two names the VPN tile explicitly and hides the agent
  token behind a button that says what it is for; step three explains what
  Minimize and Quit actually do. Verified by reading the rendered pixels back:
  the code is module-for-module identical to the matrix for the URL.
- **An update notice.** One request to the GitHub releases page at startup, on
  a background thread; if a newer version exists the window shows it with a
  Download button. Silent on every failure, and `UPDATE_CHECK=0` in
  `config.env` turns it off. Only the exe can be stale — the phone loads the
  deck from whatever build is running.
- The window lists this PC's other addresses under the link, which only the
  console used to print.

### Fixed

- **The address in the link (now the QR code) could be one no phone can
  reach.** It came from "which address would the OS route an external packet
  from" — that is the default route, and a running VPN owns the default route.
  IT-Deck ships a VPN tile, so this was aimed squarely at its own users:
  measured here, it offered the tunnel's 172.16.0.1/30 while the phone needed
  192.168.0.15. Addresses are now ranked (private range, subnet width, then a
  name hint) with the routed answer kept only as a tie-break.
- **The window no longer sits on top of everything.** It set `-topmost` at
  startup and never cleared it, so it floated above browsers and full-screen
  games for its whole life. It now releases the flag after four seconds.
- **"Hide this window" destroyed the only interface IT-Deck has** — see
  v0.3.7; the button now minimizes, and both the update notice and the
  revealed token re-fit the window instead of pushing the Quit button off the
  bottom edge.
- **Logs were minutes stale.** Both child processes write to a file, and
  Python block-buffers a non-tty stdout. They line-buffer now, which matters
  more than usual since the window points users at those files by name.
- **The Mic tile's params and icon were reapplied on every startup**, silently
  reverting anything set on that tile in Studio. Same always-on-reapply bug
  already fixed for the Volume and Terminal tiles; the Mic row was missed.
- The Close Agent tile rendered without an icon — it is seeded as `power` and
  the icon set had no such key, exactly the omission `shield` had.
- The dashboard auto-selects the only deck instead of asking you to pick one
  of one, which is the first screen after scanning the QR on a fresh install.

### Performance

- The agent stopped doing ~100 COM calls a second to read one string: the
  default output device's name is cached against the endpoint id, so an
  unchanged device costs one cheap call. Verified identical output against the
  old implementation on real hardware — 23.8ms → 1.2ms per poll.
- `is_process_running("")` returns immediately instead of walking every
  process on the machine once a second, which is what a standalone install did
  until the VPN tile was configured.
- Smaller, all behaviour-identical: tile ink no longer re-parses two constant
  colours per tile per render, the deck appends every tile before reading any
  computed style, and the unused `onResult` fan-out, the unused pydantic
  models, `gen_icon.py` and an unreferenced `logo.svg` are gone.

---

## v0.3.7 — 2026-09-15

### Fixed

- **The VPN tile's silence finally has a diagnosis: UAC.** `CreateProcessW`
  on the maintainer's `v2RayTun.exe` fails with winerror 740
  (`ERROR_ELEVATION_REQUIRED`) every single time — the exe has no embedded
  manifest, but carries the per-user `RUNASADMIN` compatibility flag, and its
  running instance's token confirms it is elevated. Every press therefore fell
  through to the `os.startfile()` fallback, which puts a **UAC consent dialog
  on the PC** and waits for someone to answer it — which is also why a VPN
  press always came back at exactly the 2.0s launch budget. The press was
  being answered by a dialog nobody was standing in front of. The tile now
  says so, with the two ways out (clear the exe's "Run as administrator"
  flag, or run IT-Deck itself as administrator), instead of reporting a
  success no toast would ever show.
- **"Hide this window" destroyed the only interface IT-Deck has.** Since
  v0.3.6 the exe is built `--windowed`, so there is no console behind that
  window: destroying it left IT-Deck running with no way to see the
  connection URL again and no way to stop it short of Task Manager. The
  button now minimizes (and is labelled that way), and the title bar's X is
  wired to the same confirmed Quit as the button.

---

## v0.3.6 — 2026-09-15

### Fixed

- **IT-Deck no longer has a console at all, and Force Stop can no longer take
  it down.** Force Stop on the Terminal tile kept killing the launcher, the
  backend and the agent along with its target. A first attempt guarded the
  console host by walking its ancestors; it did not work, because
  `OpenConsole.exe`'s parent is `svchost.exe`, not the Windows Terminal it
  belongs to — there is no ancestry link to walk. So the coupling is removed
  instead of guarded: the exe is built `--windowed`, giving it no console and
  therefore no host process that anything can be asked to kill. Verified end
  to end — launch a terminal from the tile, Force Stop it, and IT-Deck and the
  VPN both keep running.
- Launcher output goes to `logs\launcher.log`, since a windowed build has no
  stdout. Only the launcher redirects — wiring it at module level first sent
  every uvicorn request line into `launcher.log` instead of `backend.log`.
- Child processes and the first-launch shortcut helper are spawned with
  `CREATE_NO_WINDOW`, so nothing flashes a console onto the desktop now that
  the parent has none to inherit.

This also retires the console-hiding workaround and the stray rectangle it
caused: there is nothing left to hide.

---

## v0.3.5 — 2026-09-15

Reported from a second machine, plus parity with the author's own deck.

### Fixed

- **Force Stop took IT-Deck down with its target.** `kill_process` matches by
  name and kills every match — and on Windows 11 the default console host is
  Windows Terminal, so a Force Stop on the Terminal tile killed the process
  hosting IT-Deck's own console and the launcher, backend and agent went with
  it. `kill_process` now refuses to touch a protected set: everything attached
  to our console, our own process and its ancestors, and the console host
  **and its ancestors** (under Windows Terminal the host is `OpenConsole.exe`
  whose parent is the `WindowsTerminal.exe` that would be matched by name).
  This makes Force Stop safe for IT-Deck, not safe in general — it still ends
  every other process with that name.
- **Tokens generated by a pre-v0.3.0 install are migrated to `admin`.** A
  fresh download on a new PC kept showing 32-hex tokens, because `config.env`
  was never regenerated. Only values matching the old generator's exact shape
  are rewritten, so a hand-picked secret survives. `SERVER_PORT` is
  deliberately left alone — moving it orphans the phone's stored token on top
  of the token change.

### Changed

- **The VPN tile is a `launch_app`, matching the reference deck.** It was a
  `process_toggle`, which needed params nothing seeded and stopped the VPN on
  its second press. An unconfigured tile now answers with what to set instead
  of failing or toggling. A `process_toggle` someone gave real params to is
  left exactly as it is.
- **Added the missing "Close Agent" tile** (`agent_shutdown`), which the
  reference deck has and standalone never seeded. Its cell is picked at run
  time from the first free one at or below the existing tiles — a fixed cell
  collided, because a fresh database and a migrated one lay the same tiles out
  in different rows. **After pressing it the agent stays stopped by design;
  quit IT-Deck from its window and relaunch to get it back.**

---

## v0.3.4 — 2026-09-15

### Fixed

- **Force Stop on the Terminal tile matched nothing.** It derives a process
  name from the launch path, which gives `wt.exe` — but `wt.exe` is a launcher
  alias and the process it starts is `WindowsTerminal.exe`, so a long-press →
  Force Stop killed nothing and still reported "ok". The tile now carries an
  explicit `process_name`. Introduced by v0.3.2's move to Windows Terminal.
- The Terminal fixup now upgrades installs sitting on *any* previous default,
  not just the original Notepad one, while still leaving a tile customised in
  Studio alone.

---

## v0.3.3 — 2026-09-15

**The first release that actually carries a downloadable `ITDeck.exe`.**
v0.3.2 added the workflow meant to build it and the workflow failed, so that
tag has no artifact. No application code changed between the two.

### Fixed

- **The release workflow now builds.** PyInstaller resolves a relative
  `--add-data` *source* against `--specpath`, not against the working
  directory — so with `--specpath standalone` it went looking for
  `standalone/frontend` and died with "Unable to find ... frontend".
  `build.ps1` never hit this because it has always passed absolute paths.
  The workflow does now too. Reproduced locally in a clean venv with the
  exact CI command, and confirmed fixed the same way before pushing.

---

## v0.3.2 — 2026-09-15

Second real-use pass. Also the release that added CI for a downloadable
`ITDeck.exe` — though that workflow failed on its first run, so this tag has
no artifact; see v0.3.3.

### Fixed

- **Launched apps are now fully detached from IT-Deck.** Closing or killing
  IT-Deck must never take down anything it started — the VPN client above
  all. Every launch goes through `CreateProcess` with `DETACHED_PROCESS |
  CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`: no inherited console,
  its own process group, outside any job object. Verified by launching an app
  from a tile and then hard-killing all four IT-Deck processes — the app and
  the VPN both survived.
- **The VPN tile no longer reports `timeout` while the app starts anyway.**
  `os.startfile()` can take many seconds to return and it ran inline in the
  agent's receive loop, breaching the 5 s command budget. Launches now run on
  a worker thread with a 2 s budget; a fast failure still reports its real
  error, and a slow launch returns "ok" meaning *the launch was started*.
- **The stray rectangle on the desktop is gone.** It was the stub Windows
  draws for a minimized window with no taskbar button. The console is now
  hidden outright (`SW_HIDE`) rather than minimized.

### Changed

- **The Terminal tile opens Windows Terminal, not Notepad.** A tile named
  Terminal that opened a text editor was not useful. Existing installs are
  migrated — but only if the tile was still on the old Notepad default, so a
  tile repointed in Studio is left alone. `wt.exe` with `powershell.exe` as a
  fallback for machines without Windows Terminal.
- **The info window has a Quit button.** With the console hidden there is no
  taskbar button to restore and no way to press Ctrl+C, so the window is now
  the stop control. "Hide this window" and "Quit IT-Deck" are separate.
- **The version is shown in the info window and the console banner**, and now
  exists as a constant in code (`ITDECK_VERSION`) rather than only in git tags.

### Added

- **`.github/workflows/release.yml`** — builds `ITDeck.exe` on a `windows-latest`
  runner for every `v*.*.*` tag and attaches it to the GitHub release.
  Hand-runnable for tags that were already pushed without one.

### Known open

- One `access violation` from `comtypes` was seen in `agent.log`, on the last
  line, during process exit only. Noted, not investigated.
- The VPN "closes when IT-Deck closes" report was never reproduced. The
  isolation above is implemented because it is required, not because a
  mechanism was found — the one VPN death actually captured in a log was a
  toggle press doing what a toggle does.

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
