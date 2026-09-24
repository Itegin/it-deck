import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger("controlhub.ws")

# How long one client may take to accept a broadcast frame. Sends go out one
# client at a time, so without a bound a single phone on a dead Wi-Fi link
# (TCP still "open", buffer full) would hold every other client's state
# updates behind it.
CLIENT_SEND_TIMEOUT = 2.0


# Closes in flight, held so the event loop's weak reference to each task is
# not the only one (a task nobody references can be collected mid-run).
_closing: set[asyncio.Task] = set()


async def _close_quietly(ws: WebSocket, code: int = 1000) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        # Already closed, or the transport is gone. Either way it is closed.
        pass


def _close_in_background(ws: WebSocket, code: int = 1000) -> None:
    # Never awaited inline: closing a socket whose peer is gone waits for a
    # close handshake that will not come (up to the server's close timeout),
    # and whoever awaited it -- a broadcast to every other phone, a press --
    # would stall for that long.
    task = asyncio.ensure_future(_close_quietly(ws, code))
    _closing.add(task)
    task.add_done_callback(_closing.discard)


class ConnectionHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.agents: dict[str, WebSocket] = {}
        # What the agents were last told about whether any Dashboard is
        # connected. See sync_watchers().
        self.watching = False

    def register_client(self, ws: WebSocket) -> None:
        self.clients.add(ws)

    def unregister_client(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    def register_agent(self, name: str, ws: WebSocket) -> None:
        # The newest connection wins. Typically this is a restarted agent
        # whose old, half-open socket the backend has not noticed yet; that
        # one is left for the server's own keepalive to reap rather than
        # closed here, because the other way a name is taken twice -- two PCs
        # both left on the default "windows" -- would then have them evict
        # each other in a loop.
        if name in self.agents and self.agents[name] is not ws:
            logger.warning("Agent '%s' already registered; the new connection replaces it", name)
        self.agents[name] = ws

    def unregister_agent(self, name: str, ws: WebSocket) -> bool:
        """Remove `ws` as agent `name`. Returns whether it was the current one.

        Identity-checked, and that is the point: when an agent reconnects, the
        old socket's handler runs its cleanup *after* the new socket has
        registered. Popping by name alone removed the new, healthy connection
        and told every phone the agent was offline while it was connected.
        """
        if self.agents.get(name) is ws:
            del self.agents[name]
            return True
        return False

    def watchers_frame(self) -> dict:
        return {"type": "watchers", "active": bool(self.clients)}

    async def sync_watchers(self) -> None:
        """Tell every agent when the deck gains its first viewer or loses its last.

        The agent's poller reads the PC's audio and VPN state once a second
        purely so a phone can show it. With no Dashboard connected (phone or
        a PC browser -- both are /ws/client) nobody is looking, so the agent
        pauses the poll until one connects. Commands are unaffected: they
        travel on the agent's receive loop, which never pauses, so Studio's
        queries and every tile press keep working either way.

        Only transitions are sent. An agent older than this message ignores
        it (its receive loop skips frames without a cmd) and simply keeps
        polling, as before.
        """
        active = bool(self.clients)
        if active == self.watching:
            return
        self.watching = active
        for name in list(self.agents):
            await self.send_to_agent(name, self.watchers_frame())

    def close_all_clients(self, code: int) -> None:
        """Disconnect every Dashboard, e.g. after the phone token changed.

        Each socket's own handler unregisters it (and pauses the agents'
        polling once the last one is gone), exactly as for any disconnect.
        """
        for ws in list(self.clients):
            _close_in_background(ws, code)

    async def broadcast_to_clients(self, message: dict) -> None:
        # Copy to a list first: a client disconnecting mid-broadcast would
        # otherwise mutate self.clients while we're iterating over it.
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=CLIENT_SEND_TIMEOUT)
            except Exception:
                logger.warning("Failed to send to client, dropping connection")
                self.clients.discard(ws)
                _close_in_background(ws)

    async def send_to_agent(self, name: str, message: dict) -> bool:
        ws = self.agents.get(name)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            # Out of the registry at once, so nothing else is routed to a
            # dead socket and a phone connecting now is not told "online".
            # The offline broadcast happens here too: the socket's own
            # handler will find it already unregistered and stay quiet.
            logger.warning("Failed to send to agent '%s', dropping connection", name)
            if self.unregister_agent(name, ws):
                await self.broadcast_to_clients({"type": "agent_status", "agent": name, "status": "offline"})
            _close_in_background(ws)
            return False


# Single-user, single-process app: a module-level singleton is simplest and
# avoids wiring a DI container just to share one hub between routes.
hub = ConnectionHub()
