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
> agent internals, **standalone mode (§10)**, the deploy pipeline, and current
> tech debt. Read it before changing any of those; don't re-derive them from
> the code. Release-by-release history is in
> [`CHANGELOG.md`](CHANGELOG.md).

Current version: **v0.3.7** (`ITDECK_VERSION` in `standalone/launcher.py` --
bump it in the same commit as the tag). The bullets below are the constraints that are
easy to break; the reference doc explains the same mechanisms at length.

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

## Standalone mode (v0.3.0+, current as of v0.3.7)

- **`standalone/launcher.py`** is the single entry point, for both
  `python standalone/launcher.py` (dev) and the frozen `ITDeck.exe`
  (`standalone/build.ps1`, PyInstaller `--onefile`). It generates
  `AGENT_TOKEN`/`CLIENT_TOKEN`/`SERVER_PORT` once into
  `%LOCALAPPDATA%\IT-Deck\config.env` (load-if-exists, generate-if-missing —
  never regenerate on restart, or the phone's stored token stops working),
  then re-invokes itself via `sys.executable` with `--role backend` /
  `--role agent` to run both as separate OS processes from one exe (there's
  no bundled `python.exe` to spawn otherwise).
- **`SERVER_PORT` defaults to `49732` on a fresh install, not `8000`**
  (`DEFAULT_PORT` in `launcher.py`; `find_free_port()` still walks upward if
  taken). `8000` is heavily contested on a developer machine and losing that
  race is a baffling failure for a desktop app. Existing installs keep the
  port already in their `config.env` — `load_or_create_config()` is
  load-if-exists by design — so a test machine won't move until that file is
  deleted. **A port change changes the origin, which orphans the phone's
  `localStorage`: the phone re-prompts for its client token exactly once
  after the change. That is expected, not a regression.** Users who want a
  specific port edit `SERVER_PORT` in `config.env`; no separate picker UI.
- **Both `CLIENT_TOKEN` and `AGENT_TOKEN` default to the literal `"admin"`,
  not a random value.** Deliberate: on a self-hosted LAN a token buys
  little real security anyway (see the auth tech-debt item in the
  reference doc), and both are things a person can end up typing —
  `CLIENT_TOKEN` on a phone, `AGENT_TOKEN` into Studio's own token prompt.
  Only affects new config generation — hand-edit `config.env` for a real
  secret on either.
- **The built exe gives itself a desktop shortcut** (`IT-Deck.lnk`) on
  first launch — `ensure_desktop_shortcut()` in `launcher.py`, same
  `WScript.Shell`/`CreateShortcut` technique and idempotency check
  `agents/windows/start_agent.bat` already uses for its own shortcut, just
  invoked from Python via `subprocess.run` instead of from a `.bat`. Only
  fires when frozen (`is_frozen()`); a dev run has nothing sensible to
  shortcut. `build.ps1` now also generates `agents/windows/icon.ico` before
  building (it's gitignored, so a fresh clone has none yet) so both the
  exe and this shortcut get IT-Deck's actual icon, not PyInstaller's
  generic default.
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
- **Releases attach `ITDeck.exe` via CI** (`.github/workflows/release.yml`,
  `windows-latest`, on `v*.*.*` tags). Before v0.3.2 no tag carried an exe at
  all, so installing meant cloning and building on every PC. The workflow is
  also `workflow_dispatch`-able with a tag name, to give an already-pushed tag
  an exe without re-tagging. Its PyInstaller invocation is a deliberate copy of
  `build.ps1`'s rather than a call to it — `build.ps1` exists to make a *developer
  machine* buildable (finding or winget-installing a Python, making a venv), all
  of which is wrong on a runner with a pinned interpreter. **Keep the two
  invocations in step.** In particular **pass absolute paths to PyInstaller**:
  it resolves a relative `--add-data` source against `--specpath`, not the
  working directory, which is how this workflow failed on its very first
  run (it looked for `standalone/frontend`). `build.ps1` was always immune
  because it builds every path with `Join-Path $repoRoot`.
- **Rebuilding `ITDeck.exe` after any `backend/`, `frontend/`, or
  `agents/windows/` change is required** — none of it is bind-mounted like
  the Docker path. Run `standalone\build.ps1`, which also auto-installs a
  compatible Python (3.12 via winget) if nothing suitable is on PATH.
- **The launcher's console only ever prints what `run_launcher()` itself
  writes.** Backend/agent stdout goes to
  `%LOCALAPPDATA%\IT-Deck\logs\{backend,agent}.log` instead — uvicorn logs
  every request and the agent logs a state line roughly once a second, and
  on a real install that scrolled the connection URL off the screen within
  seconds. A GUI window (`show_info_window()`, tkinter in a daemon thread —
  spiked frozen under `--onefile` before relying on it) shows the same
  Dashboard/Studio links, plus a copy button, so the console isn't the only
  place to find them. Styled to match the Dashboard's own "Liquid Glass"
  theme colors (`frontend/css/themes.css`'s dark palette — tkinter can't do
  that theme's actual backdrop blur, so only the colors carry over), with a
  best-effort native Windows 11 dark title bar via `ctypes`/`dwmapi`
  (`_apply_windows11_chrome()` — silently no-ops on Windows 10; must run
  *after* every widget is packed, not before, or Tk locks in its premature
  un-sized geometry — confirmed the hard way). Text is localized EN/RU via
  `_detect_ui_lang()` (Windows UI language, falling back to Python's own
  locale).
- **Don't re-add the Mica backdrop (`DWMWA_SYSTEMBACKDROP_TYPE`) to that
  window.** It was there and was removed: Mica composites a material
  *behind* the client area, Tk paints that area opaque and knows nothing
  about it, and the disagreement is the leading suspect for a stray white
  rectangle reported on the desktop during real use. Doing it properly needs
  `DwmExtendFrameIntoClientArea` plus a transparent client brush — a real
  project, not a two-line `ctypes` call — and tkinter can't do the theme's
  actual blur regardless, which is why only its colors were ever borrowed.
- **Everything the agent launches must survive IT-Deck being closed.** This
  is a hard product requirement, stated by the user in exactly those terms
  about the VPN client. `_spawn_detached()` in
  `agents/windows/handlers/process.py` is the guarantee: `CreateProcess` with
  `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`
  (no inherited console, own process group, outside any job object).
  `os.startfile()` survives only as the fallback for what `CreateProcess`
  cannot launch — `.lnk`, documents, URLs, elevation — and that path is the
  *less* isolated one. Verified by launching an app from a tile, then
  hard-killing all four IT-Deck processes: the app and the VPN both lived.
  **Note this is a guarantee, not a diagnosis** — no measurement here ever
  reproduced IT-Deck killing a launched process. The flags are also not new:
  commit `341426b` added them for this reason and `0176612` silently dropped
  them.
- **A launch target that needs elevation is now reported, not swallowed**
  (`ElevationRequired` in `agents/windows/handlers/process.py`). Measured on
  the maintainer's PC: `CreateProcessW` on `v2RayTun.exe` fails with
  **winerror 740 (ERROR_ELEVATION_REQUIRED)** every time -- the exe has no
  embedded manifest at all, it carries the per-user `RUNASADMIN` AppCompat
  layer in HKCU, and the running instance's token confirms it is elevated.
  So every VPN press fell through to the `os.startfile()` fallback, which
  raises a **UAC consent dialog on the PC** and blocks until someone answers
  it -- which is why a VPN press always came back at exactly the 2.0s budget,
  and is the long-missing diagnosis for "the VPN button doesn't work": the
  press was being answered by a dialog nobody was standing in front of.
  Reported as `status: error` with an actionable message rather than an "ok"
  carrying a note, because `frontend/js/app.js` shows **no toast on success** --
  a note there would be invisible. `handle_launch_app` must not fall through
  to `fallback_path` on this exception: the target was found, so launching
  PowerShell instead of the VPN would be worse than saying so.
- **Launches run off the receive loop** (`start_process()`, worker thread,
  `_LAUNCH_BUDGET_SECONDS = 2.0`). `handle_process_toggle` runs synchronously
  inside the agent's single `_receive_loop`, so a slow `os.startfile()` broke
  the core 5s rule — confirmed: a VPN press returned
  `{"status": "error", "message": "timeout"}` while the app appeared seconds
  later. A launch that finishes inside the budget still reports its real
  error; one that doesn't returns "ok", which therefore means **"the launch
  was started", not "the app is on screen"**.
- **The Terminal tile launches `wt.exe`, not Notepad** (`TERMINAL_PARAMS` in
  `backend/app/db.py`, applied by `fixup_legacy_seed`). Bare name, not an
  absolute path: Windows Terminal lives behind a per-user Store execution
  alias under `%LOCALAPPDATA%\Microsoft\WindowsApps`, so baking one user's
  path into a seeded row breaks for everyone else; `CreateProcess` searches
  `PATH`, which already contains that directory. `fallback_path`
  (`powershell.exe`) covers a machine with no Windows Terminal — it is an
  optional param `handle_launch_app` tries only if the first target fails.
  It also carries an explicit `process_name` (`WindowsTerminal.exe`), because
  `handle_force_stop` would otherwise derive `wt.exe` from the path and match
  no running process at all — `wt.exe` is an alias, not the process it starts.
  **That fixup's params UPDATE is now guarded on the old Notepad value**, so
  it upgrades untouched installs without reverting a tile someone repointed
  in Studio (the always-on-reapply bug `fixup_volume_item` already had).
- **The frozen exe has NO console — built `--windowed`, and it must stay that
  way.** This is the fix for Force Stop repeatedly killing IT-Deck along with
  its target. A console gives IT-Deck a *host process* (`conhost.exe`, or
  `WindowsTerminal.exe` when that is the system default), and that host is a
  process the deck itself can be asked to kill. Guarding it did not work:
  `protected_pids()` walks the host's ancestors, but `OpenConsole.exe`'s
  parent is `svchost.exe`, not the Windows Terminal it belongs to, so the link
  isn't there to walk. No console means no host and no coupling. Verified end
  to end: launch a terminal from the tile, Force Stop it, IT-Deck and the VPN
  both survive.
  - Consequences to keep in step: `sys.stdout`/`sys.stderr` are `None` under
    `--windowed`, so `_redirect_output_to_log()` points them at
    `logs\launcher.log` — **for the launcher role only**, since wiring it at
    module level funnelled every uvicorn line into `launcher.log` instead of
    `backend.log`. Child processes and the shortcut helper are spawned with
    `CREATE_NO_WINDOW`, or they allocate consoles the parent no longer has to
    inherit. `build.ps1` and `release.yml` both pass `--windowed`.
  - `protected_pids()` is kept anyway: it still shields a dev run, and costs
    nothing. It is a backstop, not the fix.
- **`hide_console()` is now vestigial** (it was: `SW_HIDE`, not minimized).
  A minimized console left a grey-white stub rectangle above the taskbar —
  reported with a screenshot; that is what Windows draws for a minimized
  window with no taskbar button, and no amount of repaint tidying fixes it.
  Hiding removes Ctrl+C as the stop path, so **the info window now owns
  stopping IT-Deck**. Quit only sets a `threading.Event`; `run_launcher()`'s
  supervisor loop still owns the actual teardown, because tearing down from
  the tkinter thread would race it and leave orphans. Don't reintroduce
  `SW_MINIMIZE`.
- **The info window is a three-step setup guide, and the steps are the
  point.** It is the only desktop UI a `--windowed` build has, and before this
  it showed two bare URLs, a raw token and a log path — which a real new user
  on a second PC could not act on. Step 1 (open the deck on the phone) is the
  only required one and carries the QR code; step 2 names the VPN tile as the
  one thing needing setup and keeps the agent token behind a reveal button;
  step 3 says what Minimize and Quit do. Keep that shape: anything added here
  belongs inside a step, not loose at the bottom.
  - **`_STRINGS` must stay symmetric EN/RU** — every new key needs both, and
    the Russian is written as Russian, not translated word for word.
  - **tkinter has no rounded corners, no shadow, no blur and no gradient.**
    The hierarchy is carried by a lighter card fill (`surface`), the numbered
    accent badge, one `PAD` constant and the font sizes. Don't plan a design
    that needs anything else — and don't re-add Mica to get it (see below).
  - **Call `fit_window()` after adding any widget post-build.** Tk auto-sizes
    a window only until it is given an explicit geometry; after that, new
    content is clipped, not accommodated. Both the update notice and the
    revealed token pushed the Quit button off the bottom edge before this
    existed — caught by screenshot, not by reasoning.
  - **The QR is black on white deliberately.** It is read by a camera, and
    scanners want the contrast and the quiet zone (`QR_QUIET_MODULES`) they
    were designed for; theming it would look tidier and scan worse. `qrcode`
    is imported behind a `try/except` so a checkout without it shows the link
    and no code rather than no window.
- **The window releases `-topmost` after `TOPMOST_RELEASE_MS`.** It raises
  itself once so it isn't born behind the browser it was launched from, then
  stops floating. It used to set the flag and never clear it, which put it
  above full-screen browsers and games for its entire life.
- **The update check runs on a thread and answers through a `queue.Queue`
  the Tk thread polls with `root.after`.** tkinter may only be touched from
  the thread running its mainloop, so the worker must not call `root.after`
  itself. Every failure returns `None` silently (offline, DNS, GitHub's
  unauthenticated rate limit, a schema change) and `_update_check_worker`
  always queues exactly one item so the poll terminates. The 10s timeout is
  measured, not guessed: on the maintainer's machine the TLS handshake to
  `api.github.com` intermittently needs more than 5s with a VPN up, and a
  short timeout doesn't fail loudly — it silently disables the feature on
  exactly the networks it exists for. **Only the exe can be out of date**; the
  phone loads the frontend from the running build, so there is no "old client"
  on that side and a dashboard banner would be a duplicate.
- **`detect_primary_and_other_ips()` ranks addresses; it does not ask the
  routing table.** The UDP-connect-to-8.8.8.8 trick returns the default
  route's source address — and **a running VPN owns the default route**, which
  for an app that ships a VPN tile is aimed at its own users. Measured with
  v2RayTun up: it returned the tunnel's `172.16.0.1/30` while the phone could
  only reach `192.168.0.15`. `_rank_address()` sorts by RFC1918-or-not (this
  is what rejects Radmin VPN's `26.x.x.x`, public space borrowed by a
  virtual-LAN product), then subnet width (a LAN is /24 or wider, a tunnel is
  a /30), then which private range, and only then an adapter-name hint —
  address facts first, names last, because a name list can never be complete
  and the user's own VPN client is not on it. The routed address is kept only
  as a tie-break between two equally plausible LAN adapters.
- **Both child roles line-buffer stdout (`_line_buffer_stdio()`).** They write
  to files and Python block-buffers a non-tty stream, so `agent.log`'s tail
  ran minutes behind reality — useless for the one job that file has, and the
  window now points users at it by name.
- **Nothing may destroy the info window -- it is the only interface IT-Deck
  has.** With `--windowed` there is no console to fall back to, no Ctrl+C and
  nothing to restore from the taskbar. The left button therefore calls
  `root.iconify()` and is labelled "Minimize"/"Свернуть"; it used to say
  "Hide this window" and call `root.destroy()`, which left IT-Deck running
  with no way to see the URL again and no way to stop it short of Task
  Manager. The title bar's X is bound via `WM_DELETE_WINDOW` to the same
  confirmed Quit the button uses, rather than Tk's default destroy, so the
  close box means what it means in every other desktop app.
- **The console window used to minimize itself a couple seconds after startup**
  (`shrink_and_minimize_console()`, via `GetConsoleWindow()` +
  `ShowWindow`) — the info window duplicates everything it prints, so
  there's no reason for a full-size terminal to sit on the desktop. Only
  fires when frozen. Kept here only as history: the `MoveWindow(...,
  bRepaint=TRUE)` that used to shrink it, and then the minimize itself, were
  both removed while chasing the stray rectangle. The minimize was the cause.
- **Exit code 3 from the agent is retried, not treated as deliberate.**
  It used to sit in `AGENT_DELIBERATE_EXIT_CODES` alongside 0, and that was
  wrong in the case that matters most -- an **upgrade**. The outgoing install's
  agent can still hold the singleton mutex when the new launcher spawns its
  own, so the first spawn exits 3, the supervisor reads "stopped on purpose"
  and never tries again: a backend, a window, three processes and no agent,
  with nothing on screen saying so. Reproduced deliberately (hold the mutex
  20s, kill the agent) and confirmed fixed the same way. `AGENT_MUTEX_RETRIES`
  bounds it, because if a genuinely separate IT-Deck is running no respawn
  will ever win that mutex. **Keep the value 3 non-zero** --
  `agents/windows/start_agent.bat` pauses on a non-zero exit, which is how
  the legacy shortcut keeps its "already running" message readable.
- **The header dot reports the real agent, not decoration.** The launcher
  passes `agent_alive` into the window and the Tk thread polls it; a dead
  agent turns the dot amber and adds a line saying what stops working. A dot
  that is always green is worse than no dot: it is the one affordance a user
  reads as health, and this window is the only place a stopped agent could
  ever be visible now that there is no console.
- **The launcher supervises the agent and restarts it; it does not restart
  the backend.** `run_launcher()`'s loop respawns the agent on an
  *unexpected* exit with 2s→30s backoff, resetting once one survives 60s.
  `AGENT_DELIBERATE_EXIT_CODES = (0, 3)` is never restarted: `0` is
  `agent_shutdown`'s `os._exit(0)` (a tile the user pressed), `3` is
  `agent.py`'s `EXIT_ALREADY_RUNNING` (the singleton mutex). **That one is
  `3` rather than `0` because `agents/windows/start_agent.bat` pauses on any
  non-zero exit** — that pause is how the legacy shortcut keeps its "already
  running" message readable, and exiting `0` would close the window
  instantly. Keep the constant in step across the two files. A backend exit
  still stops everything, as before. This exists because the legacy path's stability came from a human
  seeing a console window stay open on a crash — standalone minimizes that
  console, so nobody sees it and the deck just goes half-dead.
- **`agent.py`'s reconnect loop catches `Exception`, not
  `(ConnectionClosed, OSError)` — don't narrow it back.** websockets'
  `InvalidHandshake` family (`InvalidStatus`, `InvalidMessage`) derives from
  `WebSocketException`, **not** `OSError` (verified against the pinned
  `websockets==13.1`), so before this the agent process died outright any
  time the backend answered an upgrade with something that wasn't a
  WebSocket — a backend restart, or the window before uvicorn mounts its
  routes, which standalone hits on every launch. `main()` *is* the recovery
  path; nothing above it can recover. Its singleton mutex handle is also
  held in a module global now — a discarded `PyHANDLE` is GC'd, which
  destroys the mutex and made the guard only as durable as refcounting.
- **The VPN tile reads `process_name`/`path` from the item's own params
  first, env (`VPN_PROCESS_NAME`/`VPN_PATH`) second** —
  `resolve_toggle_target()` in `agents/windows/handlers/process.py`, used by
  both `handle_process_toggle` and `handle_force_stop`'s `process_toggle`
  branch, which had the identical bare `os.environ[...]` lookup. Those env
  vars only ever existed in the agent's `.env`, which standalone never
  generates, so the seeded VPN tile (`params` = `{"active_style":"normal"}`)
  raised `KeyError` on every press. Configure it from Studio's params field;
  the env fallback keeps existing `.env` installs identical. `poll_loop`
  reads the same name via `get_watched_process_name()`, which the handler
  also updates at runtime.
- **The launcher seeds `VPN_PROCESS_NAME` into the agent's environment by
  reading the item table** (`watched_process_name()`, called from
  `spawn_agent()` after `wait_for_health()` and again on every respawn).
  This is not a nicety — without it the tile is actively destructive.
  `process_toggle` is a *toggle*, and the agent used to learn the process
  name only when a press arrived, so a freshly started agent reported
  `vpn.running = false` while the VPN was actually up. The tile rendered
  "off", the user tapped expecting "on", and the handler — seeing the
  process genuinely running — **killed the VPN**. Every agent start re-armed
  that trap, which is exactly why it looked like "the VPN closes when the
  agent restarts". Confirmed from a real `agent.log` timeline, not theory.
  An env var rather than a new WebSocket config frame: the agent's config
  already travels that way and neither the agent nor the backend needed a
  single change. `config.env` still wins (setdefault semantics), and the
  legacy Docker path is untouched because it sets the variable itself.
- **`kill_process()` refuses to kill IT-Deck's own processes or its console
  host** (`protected_pids()`). Force Stop matches by *name* and kills every
  match, and on Windows 11 the default console host is Windows Terminal — so
  a Force Stop on the Terminal tile (`WindowsTerminal.exe`) killed the process
  hosting IT-Deck's own console and took the launcher, backend and agent with
  it. Reported from a real install. The protected set is three sources
  because no one of them suffices: `GetConsoleProcessList` (our sibling
  processes), our own PID plus ancestors (backstop — `GetConsoleWindow()`
  returns 0 under a ConPTY), and the console host **plus its ancestors**
  (under Windows Terminal the host is `OpenConsole.exe` whose *parent* is the
  `WindowsTerminal.exe` that gets matched by name). Verified in a real
  console that the HWND survives `SW_HIDE`, which this depends on. It makes
  Force Stop safe for IT-Deck, **not** safe in general — every other process
  with that name still dies.
- **The seeded VPN tile is a `launch_app`, not a `process_toggle`** — that is
  what the author's own reference deck on Athlon uses. `process_toggle` was
  the wrong shape: it needs params nothing seeds, and being a toggle its
  second press stops the VPN. `fixup_vpn_tile_type()` converts only
  *unconfigured* toggles, so a deliberate toggle setup survives.
  `handle_launch_app` answers a pathless tile with what to set.
  `watched_process_name()` in the launcher now keys on
  `state_key = 'vpn.running'` rather than on type, and derives the process
  name from `path` when no explicit `process_name` is given — selecting by
  type missed every launch_app tile.
- **`fixup_close_agent_item()` seeds the "Close Agent" tile** the reference
  deck has and standalone lacked. **Its cell is computed, not hardcoded** —
  a fresh database and an already-migrated one lay the same tiles out in
  different rows (verified), so any fixed cell collides on one of them. It
  scans from the last occupied row so the tile lands beside VPN rather than
  in the top-left corner, which is a bad place for a stop button. Pressing it
  exits the agent with code 0, which the supervisor leaves stopped by design
  — and with the console hidden there is no way to restart just the agent, so
  the recovery is "quit IT-Deck from its window and relaunch".
- **The printed "primary" LAN address comes from the UDP-connect-to-8.8.8.8
  trick, not from ranking candidates by local reachability.** A self-connect
  from this same machine succeeds against *any* of its own bound interfaces
  — including a Hyper-V vSwitch or VPN adapter a phone can never reach — so
  it cannot tell a real LAN IP from a virtual one; confirmed the hard way
  when an earlier version of this logic promoted a `172.16.x.x` Hyper-V
  address over the real one. `check_reachable()` is now only a soft
  "check the firewall" hint, not what selects the address.
- **Every startup fixup in `db.py` must be guarded on the value it upgrades
  *from*.** This has now bitten three separate rows — `fixup_volume_item`,
  `fixup_legacy_seed`'s Terminal params, and most recently `fixup_mic_item`,
  which reapplied `params` and `icon` on every single launch and so silently
  reverted any Studio edit to the Mic tile. A bare `WHERE label = '...'`
  matches forever, and these functions run on every backend start, so
  "idempotent" is not the same as "harmless": the row is a user-editable
  record, and rewriting it is data loss. Guard on the old value (or on
  `IS NULL`), the way each of the three now does.
- **The tile icon set (`ICONS` in `frontend/js/render.js`) and what `db.py`
  seeds must stay in step.** A seeded `icon` with no matching key renders a
  label-only tile next to eight tiles that all carry a glyph — it has happened
  twice, `shield` for VPN and `power` for Close Agent. Adding a tile to a
  fixup means checking its icon exists here.
- **The dashboard auto-selects a lone workspace** (`init()` in
  `frontend/js/app.js`) rather than showing a picker with one entry. That
  screen is what a new user lands on straight after scanning the QR code.
  The selector is still reachable via "Switch deck", and the moment a second
  deck exists the behaviour is exactly as before.
- **Random tokens from a pre-v0.3.0 `config.env` ARE migrated to `admin`**
  (`_looks_auto_generated()` + the loop at the top of
  `load_or_create_config()`). This reverses the earlier "deliberately not
  migrated" rule, which was wrong in practice: an old install kept its random
  pair forever, and a fresh download on a new PC showing 32-hex tokens in its
  window reads as a bug every time. The requirement is "admin everywhere".
  The match is deliberately narrow — exactly 32 lowercase hex characters,
  the old `secrets.token_hex(16)` shape — so a hand-picked secret in
  `config.env` survives. **`SERVER_PORT` is not migrated alongside it**:
  moving the port orphans the phone's `localStorage` on top of the token
  change, which is two breakages where one was asked for.
- **Studio's agent token is persisted in `localStorage`
  (`itdeck.agent_token`), not held in memory for one page load.** Entered
  once, then forgotten about — which is the actual requirement; a prompt on
  every reload is one people stop reading and start dismissing. Dropped
  automatically on a `401` from any token-bearing call, mirroring what
  `ws.js` does with `4001` on the Dashboard side, so a wrong value saved
  once isn't re-sent forever. The old comment claiming a project convention
  against browser storage was already contradicted by `ws.js`'s own
  `TOKEN_STORAGE_KEY`; the two keys stay separate because they are two
  different secrets and Studio is desktop-only.

## Deploy (legacy: Docker on a separate server)

- **`./deploy.sh` (on Athlon, via `ssh athlon`) updates the backend only.** It
  pulls git, **rebuilds the image locally** (`docker compose up -d --build`),
  restarts the container, health-checks it, and md5s the container's code
  against disk. `✓ Код в контейнере актуален` is the only line that proves
  the container isn't stale. Local build, not a GHCR pull: a pulled image
  only exists for tagged releases and lags behind plain `main` commits,
  which is exactly what broke this script once already (commit `87ff3bc`
  deleted it after a GHCR-pull version went stale; it was restored to the
  local-build design in v0.3.0). CI still publishes the same image tag to
  GHCR on version tags — that's for anyone who wants to `docker compose
  pull` elsewhere, not part of Athlon's own deploy path.
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
