# Changelog

Newest first. Versions are git tags; `main` between tags is unreleased.

Detail lives in [`docs/IT-Deck_Tech_Reference.md`](docs/IT-Deck_Tech_Reference.md) —
standalone mode is §10, tech debt is §12.

---

## Unreleased

### Fixed

- Studio could not save anything once its saved agent token had non-Latin
  letters in it (typed in the Russian layout): every save said "can't reach
  IT-Deck". It now drops that token, says why, and asks again.
- Closing the tutorial or What's new in the PC window revealed the main
  screen in visible strips for a fraction of a second. The screen is now
  rebuilt under a snapshot and appears in one frame; the rounded panels
  and buttons also draw several times faster.

---

## v0.5.7 — 2026-09-24

**The public release.** It includes the internal milestones v0.5.2 to
v0.5.6 below; an install on v0.5.1 updates straight to it and keeps its
tiles, decks and tokens.

### Fixed

- The tutorial and What's new overlays showed an empty black window and
  stuttered on every page turn. They now sit on Studio's own background,
  with its purple and teal glows. The card keeps one size across pages,
  Back stays in place (greyed on page 1, left of the main button), and a
  page turn changes only text.

### Changed

- README (EN/RU) covers everything since v0.5.1: the new tiles, several
  decks, tokens, being easy on games and settings surviving updates.
- CLAUDE.md gains the commands, the release rules and the conventions for
  logos, performance and the PC window.
- **NirSoft's `readme.txt` ships with SoundVolumeView.** It is committed next
  to the exe in `agents/windows/tools/` and bundled into `ITDeck.exe` by both
  `build.ps1` and the release workflow, so the NirSoft package goes out
  complete.

---

## v0.5.6 — 2026-09-24

Internal milestone; published together with the rest as v0.5.7.

### Added

- **The PC window looks like Studio.** Studio's own dark Liquid Glass tokens
  (a test reads them back from the CSS), with rounded panels, buttons and
  fields drawn as antialiased images, Studio's accent badge, setup-card
  update notice, `.btn` styles and focus ring. Without Pillow it falls back
  to flat colours.
- **Game and launcher logos:** Valorant, League of Legends, Riot Games,
  Counter-Strike, Dota 2, PUBG, Fortnite, Roblox, Rockstar Games, FACEIT,
  Epic Games, Battle.net, EA, Ubisoft, GOG and PlayStation. Installed games
  pick them up by name.
- **`LICENSE`** (PolyForm Noncommercial 1.0.0) and
  **`THIRD-PARTY-NOTICES.md`**, which lists what is bundled or used and on
  what terms, plus what would have to change before charging money. Both
  are attached to a published release.
- The weather widget's setup credits Open-Meteo (CC BY 4.0), as its data
  licence requires.

### Changed

- The window follows `config.env` by its modification time: one `stat()`
  every 2 s instead of a read and parse.
- `docs/DEVELOPMENT.md` §13 has measured costs: an agent state tick is about
  0.1 ms once a second while a phone is open.

---

## v0.5.5 — 2026-09-24

Internal milestone; published together with the rest as v0.5.7.

### Added

- **PC load widget.** CPU and memory as bars, network speed as text, live
  on the phone. It is read only while a phone is looking, like all the
  deck's state.
- **Decks as files.** Studio → **Decks** can:
  - download the deck on screen as a file;
  - add a deck from a file;
  - start one from a template (Streamer, Work).

  An import always creates a new deck. It is checked by the same rules as a
  tile saved in Studio, and a bad file adds nothing.
- **Swipe between decks** on the phone, with dots under the header that
  show where you are and switch decks with a tap.

### Changed

- A finger that moves more than a few pixels on a tile no longer presses
  it. It is treated as a drag, which is also what lets a swipe start on a
  tile.
- Widgets can subscribe to the agent's live state (`ctx.onState`). The clock
  is unaffected.

---

## v0.5.4 — 2026-09-24

Internal milestone; published together with later work as v0.5.7.

### Added

- **Hotkey tile.** It presses a key combination on the PC, such as
  `ctrl+shift+m` for Discord's mute, `alt+tab` or `win+d`. Some games and
  anti-cheat systems ignore synthetic keys by design.
- **Media key tile:** play/pause, next, previous, stop, volume up/down, for
  whatever is playing.
- **Power tile:** lock, sleep, restart or shut down. It asks "Run?" on the
  phone first.
- **Send text tile.** Type on the phone and it lands on the PC's clipboard,
  ready to paste (up to 100,000 characters).
- **"Ask before running"** is an option for any tile, under More settings in
  Studio. It is on by default for Power, Program on/off and Close agent, so
  a mis-tap can no longer stop the VPN (tech debt #21).

### Tests

- A new check ties every Studio tile type to an agent command, an icon and
  its strings, so a tile can't ship half-wired.

---

## v0.5.3 — 2026-09-24

Internal milestone; published together with later work as v0.5.7.

### Added

- **Change the tokens without editing files.**
  - Studio → **Access** shows the phone token and changes either token. It
    has a **Random PIN** button.
  - The IT-Deck window gets a **New phone PIN** button.
  - Changes apply immediately. Phones on the old token are signed out and
    ask for the new one once; Studio switches to its new token by itself;
    the window's link and QR code update within two seconds.
  - A legacy Docker install shows the tokens read-only; they live in `.env`
    on the server.
- **"What's new" after an update.**
  - The IT-Deck window shows a short card once per version: "Got it" and
    "All changes".
  - Studio has a quiet **What's new** button with a dot until it has been
    opened.
  - New installs see the tour instead. The phone never shows it.
  - The notes live in `frontend/whats-new.json`, and a release is refused
    without an entry for its version.
- **Start with Windows**: a checkbox in the IT-Deck window, off by default.
  Uninstall removes it.

---

## v0.5.2 — 2026-09-24

Internal milestone; published together with later work as v0.5.7.

A full audit of the backend, agent, frontend, launcher and infrastructure. No
feature was removed. Nothing changes for a deck that was already working; the
fixes are for the situations below. Details are in `docs/ARCHITECTURE.md` and
the Tech Reference §3, §5 and §12.

### Bug fixes

- **An agent that restarted was reported offline while it was connected.**
  The old connection's cleanup removed the new one, so every tap answered
  "agent offline" until the next restart.
- **A tile named "Spotify" (or "Lights", "Sleep PC") was deleted on every
  start, and one named "Camera" was turned into a mic button.** Old startup
  clean-ups matched on the name alone.
- **Deleted or renamed built-in tiles came back on restart** (Headphones,
  Audio Switch, Screenshot, VPN, Close Agent). Each built-in insert now runs
  once per database.
- **Tiles looked live while the agent was away** after any Studio edit. The
  grey "offline" look now survives a redraw.
- **The phone could take up to 30 s to reconnect** after being picked up.
  It now reconnects as soon as the page is visible again or the network
  returns.
- **After a backend restart, the agent could wait 30 s to reconnect.** It
  now retries after 1 s when the connection had been working.
- **One bad message could drop the agent's connection**, e.g. an
  unparseable site address for a site icon. Every command now gets an
  answer, error or not.
- **A PC with no microphone sent no state at all.** One failing reading
  used to throw away the whole update; each value is now read on its own.
- Failed sends to the agent are answered "agent offline" at once instead of
  after a 5 s timeout. Unknown commands get an error reply instead of none.
- Editing a tile with an empty width or height returned a server error
  instead of a clear message.
- With browser storage blocked, the deck did not load at all.
- Uninstall left files and firewall rules behind, and no desktop shortcut
  was made, when the Windows user name contained an apostrophe.
- The deck works behind the HTTPS proxy from the Ansible playbook (the
  WebSocket used `ws://` on an `https://` page).

### Security

- The phone can no longer send arbitrary agent commands through the
  long-press override; only Force Stop is accepted.
- The client token no longer appears in `backend.log` (the dashboard link
  carries it as `?token=`).
- Tokens are compared in constant time, in one place (`backend/app/auth.py`).
- A tile's settings must be a JSON object; other JSON is refused on save.

### Performance

- **IT-Deck stays out of a game's way.**
  - While no phone or PC browser has the deck open, the agent stops reading
    audio and VPN state. It starts again the moment one connects.
  - The backend and the agent's working thread run at below-normal priority.
    Programs launched from a tile still start at normal priority.
- The VPN tile's state no longer walks the process list every second.
- `backend.log` no longer gets a line every second from the agent's state
  updates (tens of MB a day). The launcher rolls each log to `.1` past 5 MB.
- The SQLite `synchronous=NORMAL` setting now applies to every connection,
  not just the first.

### Refactoring

- Both WebSocket endpoints share one handshake and frame reader
  (`backend/app/ws/protocol.py`); the agent's command dispatch is its own
  pure module (`agents/windows/dispatch.py`).
- Startup uses FastAPI's `lifespan` instead of the deprecated `on_event`.
- New `schema_migration` table (additive) records one-time data fixes.

### Infrastructure

- **A pushed version tag no longer publishes anything.**
  - It builds the exe and keeps it as a workflow artifact, and builds the
    Docker image without pushing it.
  - Releases are published by running the workflow by hand.
    `scripts/check_release.py` refuses a release whose version isn't bumped.
- CI gains a fast Linux job: ruff, pytest and the frontend checks.
  `pyproject.toml` holds the ruff and pytest settings.
- Docker: a `HEALTHCHECK`, a `.dockerignore`, and log rotation in
  `docker-compose.yml`. Successful health probes are not logged.
- `check.sh` updated for the quieter log.
- `.bat`/`.ps1` files check out with Windows line endings.
- The legacy Scheduled Task no longer stops the agent after 72 hours.

### Tests

- New: the WebSocket endpoints (including the reconnect race), the startup
  fixups on scratch databases, agent dispatch, input validation, and a check
  that the theme lists agree everywhere they are copied. 31 → 59 Python
  tests, 5 → 6 frontend tests.

### Documentation

- New `CONTRIBUTING.md` (setup, checks, rules, releasing), linked from both
  READMEs.
- New `docs/DEVELOPMENT.md` (quick start, architecture, protocol, debugging,
  extension recipes) and `docs/ARCHITECTURE.md` (decision records).

---

## v0.5.1 — 2026-09-24

### Fixed

- **Tiles lost their state after a redraw.** A Studio edit re-renders the
  deck on the phone, and the backend only sends state when it *changes* — so
  a muted mic showed as live, VPN unlit, until something happened to change.
  The deck now remembers the last reported state and re-applies it after
  every redraw.

### Docs

- New README screenshots: the quick-launch bar in every theme, Studio with the
  bar, and the current window. README facts brought up to date (Close Agent,
  exe size, Studio's new controls, troubleshooting, status).

---

## v0.5.0 — 2026-09-24

### Added

- **A quick-launch bar.** Up to seven square buttons that sit outside the
  grid: along the bottom when the phone is upright, down the left side when it
  is sideways. Any 1×1 action can go there. Websites and programs added with
  **+ New tile** go there by default, and **Where** in Studio's step 4 moves a
  button between the bar and the grid. An empty bar takes no room, so an
  existing deck looks exactly as it did.
- **Website tiles.** They open any address in the PC's default browser, with
  one-tap presets: Telegram Web, Discord, WhatsApp, YouTube, ChatGPT, Gmail,
  GitHub and more. The browser keeps running after IT-Deck closes, the same
  as any program the deck starts.
- **Choose from installed programs.** Studio asks the PC for its Start Menu
  programs, so a program tile no longer needs a path typed in by hand. Picking
  one fills in the path, any launch arguments, the name and, for well-known
  apps, the logo. Microsoft Store apps (Claude, WhatsApp and the like) are in
  the list too; Force Stop refuses those rather than hitting Explorer, which
  is what starts them.
- **Four kinds of icon:** the built-in symbols, 21 one-colour logos (Chrome, Claude,
  Telegram, Discord, Steam, Spotify, OBS and others), up to three characters
  of text or an emoji, or the site's own icon, fetched by the PC.
- **A tour on first launch,** in the IT-Deck window on the PC and on the phone
  the first time it shows the deck. The PC one can be opened again with
  **Tutorial**.
- **A guide inside Studio:** seven short sections with pictures, in English
  and Russian. It opens by itself on the first visit and from **Guide** after
  that.
- **A Start the agent button** in the IT-Deck window. Before this, the Close
  Agent tile stopped the agent and the only way back was quitting and
  relaunching IT-Deck.

### Changed

- **The offline clock is a proper clock face.** When the PC end goes away,
  the clock drops its tile frame and shows seven-segment digits, like a
  bedside alarm clock.
- Program tiles accept `%APPDATA%`-style paths and launch arguments.
- **Updates reach the phone.** The deck's files are served with
  `Cache-Control: no-cache`, so a phone or Studio no longer runs a cached old
  script against a new page after IT-Deck is updated.
- **Tests run on GitHub Actions** for every push: the backend API, the
  agent's address checks and the frontend's pure logic (`tests/`).

---

## v0.4.6 — 2026-09-19

### Fixed

- **The uninstall button left the Windows Firewall rules behind.** Everything
  else it promised — the exe, the shortcut, the settings and tokens, the temp
  directory — was removed correctly in v0.4.5, but the one step that needs
  administrator rights quietly did nothing, so a later reinstall could still
  meet the old **Block** rules and look dead on the network.

  The elevated command was built as a string inside another string, and the
  outer shell mangled the filter before the elevated half could run it. It is
  a script file now. Measured on a disposable copy carrying two rules of its
  own: two rules before and two after in v0.4.5, two before and none after
  with this fix.

---

## v0.4.5 — 2026-09-19

### Added

- **A button that removes IT-Deck from the PC completely.** It is in step 3 of
  the window, and it deletes the exe, the Desktop shortcut, the settings,
  tokens and tile database, its Windows Firewall rules and the temp directory
  the exe unpacks into — which is everything IT-Deck ever writes; there is no
  registry key, service or scheduled task behind it.

  Deleting the exe by hand never removed the other four, and the firewall
  rules in particular are the reason a later reinstall can look dead on the
  network.

  It asks first, in a window that lists what is about to go. Cancel holds the
  focus and Enter is bound to it, so the reflex that dismisses a dialog cannot
  delete an install; the red button has to be aimed at.

---

## v0.4.4 — 2026-09-19

### Added

- **The deck becomes a clock when the PC end goes away.** Close the agent, or
  IT-Deck itself, and every tile on the deck is a button that cannot do
  anything — except the clock widget, which runs on the phone and keeps
  working. It now takes the whole screen for as long as that lasts, with a
  line saying whether the agent or the connection is what is missing. The
  tiles come back by themselves the moment the PC does; there is nothing to
  switch on or off, and a deck without a clock widget is unaffected.

  A deck opened *while* the agent was already down used to look perfectly
  live until a press timed out. It now knows straight away.

### Fixed

- **Ending IT-Deck in Task Manager left the backend and the agent running**,
  and Windows put up a modal **"Failed to remove temporary directory"**
  warning. The same dialog v0.4.3 fixed for shutdown, reached from the one
  direction no code of ours can cover: "End task" terminates the process
  outright, so nothing IT-Deck could have written would have run.

  The backend and the agent now belong to a Windows job object that the
  kernel empties when IT-Deck's process ends, however it ends. Anything
  launched *from a tile* still survives IT-Deck closing — that rule is older
  than this fix and is now tested rather than assumed.

- **A second copy of IT-Deck damaged the running one.** The startup cleanup
  added in v0.4.3 deleted what it could out of a directory another copy was
  running from before failing on the file it could not touch — measured at 32
  files. It now renames a directory to claim it, which Windows refuses while
  anything inside is open, so a live copy is left alone entirely.

- **Starting IT-Deck twice now says so.** The second copy used to disappear a
  few seconds after launch with the reason buried in a log file. It now
  explains that the port is already in use, in a window, and stops.

- **A tile turned into the clock widget came back as the old tile.** Replacing
  the Terminal tile with "Clock & weather" in Studio and leaving its label
  alone held until the next restart, when a startup fixup rewrote the tile's
  type back and the deck — finding no widget of that type — drew the Terminal
  tile again. The fixups now only touch tiles that are still what they were
  seeded as, and a tile already broken this way is repaired on the next
  start.

---

## v0.4.3 — 2026-09-19

### Fixed

- **Windows still would not shut down.** v0.4.2 fixed the first half of this
  and left the second. IT-Deck now really does close itself, and the PC goes
  down.

  v0.4.2 answered Windows correctly but did its actual cleanup 150 ms later,
  on another thread. Windows does not wait that long — it terminates an app as
  soon as its windows have answered — so that cleanup never ran, and the
  backend and the agent were left running. Both are re-invocations of
  `ITDeck.exe` sharing its unpacked temp directory, so PyInstaller's launcher
  could not delete that directory and put up a modal **"Failed to remove
  temporary directory"** warning. A modal dialog during shutdown is a shutdown
  that never finishes — the same PC left on all night, for a different reason.

  The stop now happens before IT-Deck answers Windows, and waits for the
  backend and the agent to actually be gone rather than only asking them to
  go. Verified against a test that terminates IT-Deck the instant it answers,
  exactly as Windows does — the case v0.4.2 passed only because the earlier
  test was politely waiting for it.

- **Leftover unpack directories are cleaned up on start.** Every shutdown that
  killed IT-Deck before it could tidy up left ~90 MB in `%TEMP%\_MEIxxxxx`.
  This machine had 35 of them, 1.26 GB. Directories belonging to a running
  copy are never touched.

---

## v0.4.2 — 2026-09-19

### Fixed

- **Shutting Windows down with IT-Deck running left the PC switched on.**
  Windows stopped at the "this app is preventing you from shutting down"
  screen and waited for a click, so a PC that had been told to shut down
  stayed on all night. IT-Deck now closes itself the moment Windows says the
  session is ending — shutdown, restart or sign-out — and the machine goes
  down without asking anything.

  The cause was not the info window: Tk answers the shutdown query correctly
  on its own and never opens the Quit confirmation. It was PyInstaller's
  one-file launcher, which keeps a second, invisible process alive to delete
  the exe's unpacked temp directory and asks Windows to hold the shutdown
  while it waits for IT-Deck to exit — which IT-Deck never did, because
  nothing had ever told it to. See §10.8 of the tech reference.

---

## v0.4.1 — 2026-09-16

### Added

- **Widget tiles**, starting with **Clock & weather**: the phone's time and
  date, plus the current weather for a city picked in Studio (Open-Meteo, no
  API key, cached by the backend). It has a wide layout from 2×1 up. Any tile,
  Terminal included, can be turned into one from Studio.
- **Studio redesigned** in Liquid Glass: a live preview of the deck (click to
  edit, `+` to add, drag to move) and a step-by-step editor where you pick what
  a tile does from cards instead of typing command names and JSON. EN/RU.
- **The VPN path has its own setup card** at the top of Studio until it's set.

### Fixed

- Saving a tile in Studio no longer pins teal/red onto it, overriding the
  theme's colours.
- Pressing Enter in Studio's token prompt no longer cancels it.

---

## v0.4.0 — 2026-09-16

Batched on purpose: everything below landed on `main` without a tag and ships
as one release. The headline is that the window an installed copy shows you is
now a setup guide rather than a page of raw values -- and that it tells you
when something is wrong instead of looking fine regardless.

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
- **An upgrade could leave you with no agent at all, permanently.** The agent
  exits with code 3 when another instance holds its singleton mutex, and the
  supervisor treated that as "stopped on purpose" and never retried — so if
  the outgoing install's agent was still shutting down when the new one
  started, the deck came up with a backend, a window and no agent, and nothing
  said so. Reproduced deliberately, fixed, and re-verified: it now retries
  with backoff and recovers on its own.
- **The status dot in the window is now real.** It reports whether the agent
  process is actually alive, and a line appears saying what stops working when
  it isn't. It used to be permanently green.
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
