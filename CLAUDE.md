# IT-Deck

Self-hosted universal control surface that turns an old iPhone into a deck
for your PC. Single-user, single-process app — no multi-tenancy, no auth
beyond the agent token.

Public name: **IT-Deck**. Internal identifiers — the repo folder, logging
namespaces (`controlhub`, `controlhub.api`, `controlhub.ws`), and the
SQLite filename (`controlhub.db`) — intentionally remain `controlhub`;
this is a deliberate decision from the rebrand, not an incomplete rename.

## Architecture

Three pieces, one persistent WebSocket each:

- **backend/** — FastAPI app (`backend/app/main.py`). Serves the REST
  snapshot endpoint, the static frontend, and two WebSocket routes.
  SQLite (WAL mode) is the only datastore.
- **agents/windows/** — Python agent that runs on the controlled PC. Holds
  a persistent WebSocket to the backend, executes commands, and polls
  local state (e.g. mic mute) on a timer.
- **frontend/** — Vanilla JS + CSS PWA served straight off disk (no build
  step). Runs on the phone, talks to the backend over its own WebSocket.

```
backend/app/
├── main.py        FastAPI app, route registration, startup fixups
├── db.py          sqlite connection, schema, seed/fixup migrations
├── models.py      Item/Workspace pydantic models, query helpers
├── state.py       in-memory current-state snapshot + diffing
└── ws/
    ├── hub.py      ConnectionHub: tracks connected clients/agents, broadcast
    ├── agent.py    /ws/agent handler — agent hello/auth, state, results
    └── client.py   /ws/client handler — initial state push, execute cmd

agents/windows/
├── agent.py       connects to backend, reconnect/backoff loop, dispatches
│                  incoming commands to handlers, forwards polled state
├── poller.py      polls local state (mic mute) on an interval, pushes it
└── handlers/      one module per command type (audio.py, process.py)

frontend/
├── index.html
├── js/
│   ├── app.js     boot: fetch workspace, initial render, wire clicks
│   ├── api.js     REST fetch + params JSON parsing
│   ├── ws.js      client WebSocket: connect/reconnect, send execute, result callbacks
│   └── render.js  DOM rendering: grid, tiles, error/empty states
└── css/           base.css, grid.css, button.css
```

The frontend serves two separate pages from the same static mount, split
by what they're allowed to do to the item catalog: **Dashboard**
(`index.html`) is the phone-facing surface — it reads `/api/workspaces`
and sends `execute`/`set_value` over `/ws/client`, but never calls
`POST`/`PUT`/`DELETE` on `/api/items`. **Studio** (`studio.html` +
`js/studio.js`) is the desktop-only admin page and the only place that
mutates the item catalog through those three verbs. Every `/api/items`
request — including `GET` — requires the `X-Agent-Token` header (see
Known limitations); `/api/workspaces` itself stays unauthenticated.

## Message shapes

**Client → backend** (`/ws/client`): `{"cmd": "execute", "item_id", "req_id"}`.
Backend looks up the item, forwards to the target agent.

**Backend → agent** (`/ws/agent`): `{"cmd": <item.type>, "params": <item.params, parsed>, "req_id", "item_id"}`.

**Agent → backend**: `{"type": "result", "req_id", "item_id", "status": "ok"|"error", "message"?}`
or `{"type": "state", "data": {...}}` for polled state changes.

**Backend → client**: results and state changes are broadcast to every
connected client verbatim; a fresh client also gets a full `{"type": "state", "data": <snapshot>}` on connect since it missed prior diffs.

`item.params` is stored as a JSON string in the `item` table and parsed at
the point of use (both backend `client.py` and frontend `api.js`) — it is
not currently validated at write time because there is no create/edit API
yet; only the hardcoded seed/fixup migrations in `db.py` write it.

## Adding a new agent command

1. Add a handler function in `agents/windows/handlers/` returning
   `{"status": "ok"}` or `{"status": "error", "message": ...}`.
2. Register it in `HANDLERS` in `agents/windows/agent.py`.
3. Add/point an `item` row at it (`type` = command name, `target` = agent
   name, `params` = JSON string of static config).

If the command needs a live value the DB row can't hold (e.g. a slider
position), the client → backend → agent message shape does not currently
carry one — `sendExecute` only sends `item_id` + `req_id`. Extend the
message shape deliberately before building that handler; don't assume
today's `execute`-only shape covers it.

## Platform constraints

- Single persistent socket per agent connection (`ConnectionHub` keeps one
  `WebSocket` per agent name) — "agent not in hub.agents" is a definitive
  offline signal, not a race, so `_handle_execute` treats it as an
  immediate error rather than waiting on a timeout.
- The Windows agent depends on `pycaw`/`comtypes` (Windows COM audio
  APIs) — it only runs on Windows, by design.
- Must run in the interactive user session, not as a SYSTEM-context
  service — session 0 services cannot access a logged-in user's audio
  session (Windows Session 0 Isolation).
- The phone reaches the backend over plain `http://<lan-ip>:8000`, not
  HTTPS, so browser APIs that require a secure context (e.g.
  `crypto.randomUUID()`) are unavailable on that page; `ws.js` generates
  request IDs manually instead.
- Backend is deployed as a single Docker container (Debian host); the
  frontend directory is bind-mounted in, not baked into the image — the
  `Dockerfile` intentionally does not `COPY` `frontend/`.
- iOS Safari applies :active only if a touch listener exists somewhere on the page (empty touchstart on document.body). Verify this is present in app.js before assuming CSS :active rules will work on iPhone.
- Every execute command must resolve within 5s: ok, error, or timeout. A req_id that never gets a matching result is a bug, not an edge case.
- SERVER_PORT must be read from .env, never hardcoded.

## Commands

- `./deploy.sh` rebuilds and redeploys the Debian backend container only —
  it pulls, rebuilds, and verifies `backend/`; it has no effect on the
  Windows agent process. It's a separate, long-running process — a stale
  agent silently keeps running old code with no error until something like
  a handler bug that should've been fixed surfaces live.

- The Windows agent is started **manually**, from the "IT-Deck Agent"
  desktop shortcut, which points at `agents/windows/start_agent.bat`. The
  "IT-Deck Agent" Scheduled Task registered by `install_task.ps1` is
  **disabled on both PCs** and is not what runs the agent; ignore it.
  `start_agent.bat` exits silently on a clean exit and only leaves the
  window open (`pause`) on a non-zero exit code, so a window that stays up
  means the agent crashed and the text in it is the error.

- To restart the agent after any change to `agents/windows/`: close the
  agent's console window, then launch the desktop shortcut again. This is a
  GUI action on the Windows PC — Claude Code cannot perform it, so it has
  to be done by hand before any agent-side change can be called working.

## Known limitations

- `POST /api/screenshot` and all four `/api/items` endpoints (including
  `GET`) are gated by the same `AGENT_TOKEN` shared secret the WebSocket
  agent connection uses (an `X-Agent-Token` header check), not a real
  auth system -- no per-caller identity, no expiry, no rate limiting.
  Sufficient for this project's documented single-user, local-network
  scope; revisit if that scope ever changes. `/api/workspaces` remains
  unauthenticated.
