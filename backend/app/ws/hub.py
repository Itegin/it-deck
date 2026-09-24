import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger("controlhub.ws")

# How long one client may take to accept a broadcast frame. Sends go out one
# client at a time, so without a bound a single phone on a dead Wi-Fi link
# (TCP still "open", buffer full) would hold every other client's state
# updates behind it.
CLIENT_SEND_TIMEOUT = 2.0


async def _close_quietly(ws: WebSocket, code: int = 1000) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        # Already closed, or the transport is gone. Either way it is closed.
        pass


class ConnectionHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.agents: dict[str, WebSocket] = {}

    def register_client(self, ws: WebSocket) -> None:
        self.clients.add(ws)

    def unregister_client(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def register_agent(self, name: str, ws: WebSocket) -> None:
        previous = self.agents.get(name)
        self.agents[name] = ws
        if previous is not None and previous is not ws:
            # A restarted agent reconnects before the backend has noticed the
            # old socket is dead (a killed process leaves a half-open TCP
            # connection behind). The new one wins; the old one is closed so
            # its handler task ends now instead of whenever TCP gives up.
            logger.warning("Agent '%s' reconnected; closing its previous connection", name)
            await _close_quietly(previous)

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

    async def broadcast_to_clients(self, message: dict) -> None:
        # Copy to a list first: a client disconnecting mid-broadcast would
        # otherwise mutate self.clients while we're iterating over it.
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=CLIENT_SEND_TIMEOUT)
            except Exception:
                logger.warning("Failed to send to client, dropping connection")
                self.clients.discard(ws)
                await _close_quietly(ws)

    async def send_to_agent(self, name: str, message: dict) -> bool:
        ws = self.agents.get(name)
        if ws is None:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            # Closed, not unregistered: the socket's own handler (ws/agent.py)
            # unregisters it in its finally and tells the clients it went
            # offline, and doing it here would leave that handler with
            # nothing to unregister and no reason to say so.
            logger.warning("Failed to send to agent '%s', closing its connection", name)
            await _close_quietly(ws)
            return False


# Single-user, single-process app: a module-level singleton is simplest and
# avoids wiring a DI container just to share one hub between routes.
hub = ConnectionHub()
