# IT-Deck Architecture Decisions

Each entry records a decision: the context, what was decided, and the
consequence. Several are historical choices kept for compatibility; they say
so. For how the system works, see [`DEVELOPMENT.md`](DEVELOPMENT.md). For
exhaustive detail, see the
[Tech Reference](IT-Deck_Tech_Reference.md).

---

## ADR-1 Three processes, one WebSocket each, a backend in the middle

**Context.** A phone must drive a Windows PC. Phones on a home LAN cannot
reach into the PC reliably (sleep, firewalls), and the PC has no public
address.

**Decision.** Both the phone and the agent *dial out* to a backend and hold
one persistent WebSocket each. The backend owns the catalog and routes
commands. It never initiates anything toward the PC beyond relaying a command
on the agent's own socket.

**Consequences.**
- Either end can restart independently; each reconnects with backoff.
- The backend is the single place that enforces the 5-second rule, validates
  input and applies auth.
- One extra hop per press, which costs sub-millisecond latency on a LAN.

## ADR-2 Standalone exe as the main path; Docker kept as legacy

**Context.** The project started as a Docker backend on a home server
("Athlon") plus a hand-started agent. Most users have one PC and no server.

**Decision.** `standalone/launcher.py` bundles backend, agent and frontend
into one PyInstaller `ITDeck.exe` (since v0.3.0). The backend and agent run
as child processes of the launcher inside a kill-on-close job object. The
Docker path remains for the original deployment.

**Consequences.**
- Two deployment shapes share one codebase. Anything read from configuration
  (port, data dir, frontend dir) comes from the environment, never a
  hardcoded path.
- `build.ps1` and `release.yml` duplicate the PyInstaller call and must
  change together.

## ADR-3 Vanilla JS, no build step, frontend bind-mounted

**Context.** An old iPhone, a single developer, and the wish to edit and
reload.

**Decision.** ES modules served as-is, with no framework and no bundler. In
Docker, `frontend/` is bind-mounted rather than baked into the image.
Responses carry `Cache-Control: no-cache` (with an ETag).

**Consequences.**
- Frontend deploys are a `git pull` plus a reload.
- Frontend and backend deploy **separately**, which creates two ordering
  rules: themes need the backend first, and the `/ws/client` hello needs the
  frontend first. Both are documented in the Tech Reference.
- Language features must work on older iOS Safari. For example the code uses
  `hasOwnProperty.call` rather than `Object.hasOwn`.

## ADR-4 Results are broadcast; each phone filters by its own `req_id`

**Context.** The backend has no client identity beyond "holds CLIENT_TOKEN".
Several phones can show the same deck.

**Decision.** Command results and the synthetic timeout are broadcast to
every phone. `ws.js` keeps only the `req_id`s it generated. Correlation is by
`req_id`, never `item_id`, because several error paths have no item.

**Consequences.**
- It is simple, and every phone sees every state change.
- A result never "belongs" to a socket, so the backend needs no per-client
  bookkeeping.
- Unknown-command replies are the exception: they go to the asking socket
  only, because nobody else could have sent that `req_id`.

## ADR-5 Every command resolves within 5 seconds

**Context.** A press that never answers leaves the tile spinning, and the
user gets no information.

**Decision.** Every `req_id` resolves with ok, error, or a synthetic
`timeout` broadcast by `app/pending.py` 5 s after the command reached the
agent. Agent handlers keep their own budgets under 5 s. The phone has an 8 s
backstop, longer on purpose so the server's reason wins. An agent that is not
connected is answered immediately rather than after a timeout.

**Consequences.**
- "It may still have run" is a real state, and the UI says so on a timeout.
- Long-running work (launching an app) detaches after 2 s and reports ok.

## ADR-6 Agent handlers stay synchronous on the event loop

**Context.** Handlers call pycaw/COM. A version that ran them in a worker
thread let COM pointers be released off-thread and crashed the frozen agent
(0xC0000005).

**Decision.** Handlers run synchronously on the agent's asyncio thread. Each
one bounds its own work with subprocess timeouts and detaching.

**Consequences.**
- While a handler runs (≤ ~4 s), the poller pauses. That is well inside the
  WebSocket keepalive (20 s).
- A crash in dispatch must never kill the connection. Since this audit,
  `dispatch.py` turns every exception into an error result.

## ADR-7 A process-level singleton hub and in-memory state

**Context.** Single user, single process.

**Decision.** `ConnectionHub` and the state snapshot are module-level
singletons. State is not persisted; the agent's next one-second tick rebuilds
it.

**Consequences.**
- There are no locks and no DI container.
- A second backend replica would split the registry. That is out of scope.

## ADR-8 One agent socket per name; the newest connection wins

**Context.** A restarted agent reconnects before the backend has noticed its
old TCP connection is dead.

**Decision.** Registering an agent name replaces any previous socket under
that name. Unregistering checks **identity**, so the old socket's late
cleanup cannot remove the new one. `offline` is broadcast only when the
current socket really leaves, or when a send to it fails.

The old socket is **not** closed actively. A half-open one is reaped by
uvicorn's keepalive (20 s ping). Closing it would make two PCs left on the
default name `windows` evict each other in a reconnect loop. Closes that do
happen, of a failed phone or agent, run in the background, because awaiting
a close to a dead peer stalls the caller for the whole close timeout.

**History.** Before this audit, cleanup removed by name. After an agent
restart, phones were told the agent was offline while it was connected.
Every press then answered "agent offline" until the next restart.

## ADR-9 Two shared secrets, and `admin` by default in standalone

**Context.** Single-user, home LAN, and a phone that must be set up by
scanning a QR code.

**Decision.**
- `AGENT_TOKEN` gates every write and the agent socket.
- `CLIENT_TOKEN` gates the phone socket only. It is a separate secret
  because it ships to a browser over plain http, so leaking it must not
  grant catalog writes.
- Standalone defaults both to `admin`. That is a product decision recorded
  in CLAUDE.md, and `config.env` can override it.
- Tokens are compared in constant time, and they are masked in logs.

**Consequences.**
- Anyone on the LAN who knows the default can use Studio. Studio can create
  a tile that runs any program, which is arbitrary code execution on the PC.
  This is accepted for the home-LAN scope and stated in DEVELOPMENT.md §14.
- The phone's socket cannot run anything a tile does not already define,
  because `override_type` is allowlisted to `force_stop`.

## ADR-10 Label-keyed startup fixups, plus a `schema_migration` record

**Context.** Seeded tiles were upgraded over many releases by `fixup_*`
functions that run on every start and find their rows **by label**. This is
historical: there was no migration table.

**Decision (kept).**
- Fixups still run every start.
- Each UPDATE must be guarded on the value it upgrades **from**, never on the
  label alone.

**Decision (this audit).**
- An additive `schema_migration(name, applied_at)` table records one-shot
  fixups: the seeded-tile inserts and the Audio Switch backfill.
- Each runs its old, label-checked logic once, so upgraded databases get no
  duplicates, and never again after that. Deleting or renaming a seeded tile
  now sticks.
- Two unguarded statements were also fixed:
  - one deleted any user tile named "Spotify";
  - one converted any tile named "Camera" into a mic button.

**Consequences.**
- Older builds ignore the new table, so a downgrade is harmless.
- `fixup_widget_types()` still assumes `clock_weather` is the only widget, and
  must be revisited when a second widget type is added.

## ADR-11 Internal names stay `controlhub`

**Context.** The project was renamed from ControlHub to IT-Deck.

**Decision.** Public name IT-Deck. Logger names (`controlhub.*`), the SQLite
file (`controlhub.db`) and the repo folder stay `controlhub`.

**Consequences.** Renaming the DB file would orphan every existing
installation's data, and renaming loggers would break log greps
(`check.sh`). The mismatch is intentional.

## ADR-12 No application-level heartbeat on the phone socket (yet)

**Context.** iOS can suspend a tab and leave its socket silently dead. The
server detects dead phones through uvicorn's pings. The phone has no
equivalent, because browsers do not expose ping/pong.

**Decision.** The phone reconnects immediately when the page becomes visible
or the network comes back, which covers the common "picked the phone back up"
case. A ping/pong command was **not** added.

**Why not.** Frontend and backend deploy separately (ADR-3). A new phone
pinging an older backend would get no pong, conclude the server was dead, and
loop reconnecting. A heartbeat needs the backend to ship first and the client
to tolerate a backend that does not answer.

## ADR-13 Logs are bounded and one line per event

**Context.** The backend logged every agent frame at INFO, which included
the one-second state tick. Standalone logs were opened in append mode forever.
One install produced tens of megabytes a day.

**Decision.**
- Log one INFO line per connect, disconnect, state *change* and command
  result, with no payloads.
- Per-frame lines are DEBUG.
- The launcher rolls each log to `.1` past 5 MB at startup.
- Docker uses json-file rotation.

**Consequences.** `check.sh` now counts `State changed` lines instead of raw
frames. An idle PC can legitimately show none.

## ADR-14 Read state only while someone is watching; stay below the foreground

**Context.**
- The agent read audio and VPN state every second forever, even with no
  phone connected.
- The backend and agent ran at normal priority beside games.
- The ask was that IT-Deck never cost a game frames.

**Decision.**
- The backend tells each agent whether any Dashboard is connected, with a
  `{"type": "watchers", "active": bool}` frame. It sends one on each
  agent's connect and on the first-viewer and last-viewer transitions. The
  agent's poller waits while `active` is false.
- The backend process starts at `BELOW_NORMAL_PRIORITY_CLASS`.
- The agent lowers only its **thread**. Child processes inherit a
  below-normal process class, and programs launched from a tile must not.
- The VPN check remembers the process's PID instead of walking every
  process each second.

**Consequences.**
- An unwatched IT-Deck is idle apart from socket keepalives.
- When the phone connects, the backend's snapshot may be stale for one
  tick; the agent's immediate read replaces it within milliseconds.
- The frame is optional in both directions, so mixed versions keep
  working.

## ADR-15 Tokens change at runtime, through one path

**Context.** Tokens could only be edited by hand in `config.env`, and each
change needed a restart. People sharing a Wi-Fi wanted to replace `admin`
without touching files.

**Decision.** `PUT /api/access` is the only way a token changes at runtime.
Studio's Access dialog and the window's "New phone PIN" button both call it.
It does three things in this order:
1. writes `config.env` **line by line**, so comments and unknown keys survive,
   with an atomic replace;
2. updates the backend's environment (`app/auth.py` reads it per request);
3. on a new phone token, closes every phone socket with `4001`.

The agent re-reads its token from `config.env` on each connect. The window
re-reads the file every 2 s to redraw the link and the QR code.

**Consequences.**
- There is no restart and no second code path for "save".
- Docker installs are read-only here (`409`): their tokens live in the
  server's `.env`, which this process can't rewrite.

## ADR-16 Widgets read live state through a subscription; decks are files

**Context.**
- The PC-load widget needs the agent's numbers.
- Widgets were mount-and-forget.
- Decks had no backup or sharing story.

**Decision.**
- `mount(tile, item, ctx)` gets `ctx.onState(callback)`, fed by
  `render.js`'s `updateTileState`. The subscription lives and dies with the
  widget.
- A deck file is `{"format": "itdeck-deck", "version": 1, …}`: positions,
  looks and settings, no ids or counters.
- Import always creates a **new** deck. It uses the same validators as a
  Studio save and runs in one transaction. Templates are ordinary deck files
  in `frontend/templates/`.

**Consequences.**
- Adding a live widget is a reader in `poller.py` plus a widget module.
- A deck file can create tiles that start programs, exactly like Studio can.
  Import needs the Studio token, and the dialog says to import only trusted
  files.

## ADR-17 The PC window borrows Studio's tokens, and a test holds it to them

**Context.**
- The Tk window and Studio looked like two different products.
- A hand-picked "similar" palette drifts the first time Studio changes.

**Decision.**
- `launcher._STUDIO_TOKENS` holds Studio's dark Liquid Glass tokens copied
  verbatim. `_studio_palette()` composites them into opaque colours (Tk has
  no alpha or blur).
- `tests/test_launcher.py` reads every token back from
  `base.css`/`themes.css`/`studio.css`.
- Rounded shapes are Pillow-drawn PNGs loaded with `PhotoImage(data=…)`
  (no ImageTk). ttk image elements stretch them nine-slice, so each shape
  is drawn once whatever its size.
- A button's style background is whatever it sits on (`button_style()`),
  because ttk fills a widget's rectangle before drawing the image.

**Consequences.**
- Changing Studio's palette fails CI until the window follows.
- Without Pillow the same styles degrade to flat colours; the window never
  depends on the images.

