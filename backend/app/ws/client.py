import asyncio
import json
import logging
import os

from fastapi import WebSocket, WebSocketDisconnect

from app.models import bump_press_count, get_item
from app.pending import track
from app.state import get_state
from app.ws.hub import hub

logger = logging.getLogger("controlhub.ws")

# How long a freshly accepted socket has to send its hello frame. A client that
# connects and then says nothing is the "silent hang" this handshake exists to
# avoid -- without a bound, such a socket would sit in receive_json() forever,
# holding a connection that has never proved anything.
HELLO_TIMEOUT = 5.0

# Close codes. 4001 is the code ws/agent.py already uses for a rejected
# handshake, reused here so both sockets speak the same dialect; 4008 splits
# out "you never sent the hello at all", which is a different thing to debug
# from "your token was wrong".
CLOSE_UNAUTHORIZED = 4001
CLOSE_HELLO_TIMEOUT = 4008


async def _authenticate(ws: WebSocket) -> bool:
    """Consume the hello frame and decide whether this socket may proceed.

    Modelled directly on agent_ws()'s handshake, including its guard against an
    unset env var: `not expected` has to be checked separately, or a missing
    CLIENT_TOKEN (None) would compare equal to a missing token (None) and let
    an unauthenticated client straight through.

    Fails closed when CLIENT_TOKEN is unset. That is a deliberate choice and it
    has a deploy consequence worth stating plainly: a backend that ships this
    without CLIENT_TOKEN in .env refuses every Dashboard connection. The
    alternative -- treating "unset" as "no auth wanted" -- would mean the gate
    silently does nothing on exactly the installs that never configured it,
    which is the failure mode this change exists to remove.
    """
    expected = os.environ.get("CLIENT_TOKEN")
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=HELLO_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Client sent no hello within %ss; closing", HELLO_TIMEOUT)
        await ws.close(code=CLOSE_HELLO_TIMEOUT)
        return False
    except WebSocketDisconnect:
        # Hung up mid-handshake. Nothing to close and nothing to log about.
        return False
    except (ValueError, TypeError):
        # receive_json() raises on a frame that isn't JSON at all.
        logger.warning("Client hello was not valid JSON; closing")
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return False

    if not isinstance(hello, dict) or hello.get("type") != "hello":
        logger.warning("Client first frame was not a hello; closing")
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return False

    if not expected or hello.get("token") != expected:
        logger.warning("Client presented an invalid token; closing")
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return False

    return True


async def client_ws(ws: WebSocket) -> None:
    await ws.accept()

    # Before register_client() and before the state push, and that ordering is
    # the point of the change rather than an implementation detail: a socket in
    # hub.clients already receives every broadcast, and get_state() is itself
    # part of what the gate protects. Authenticating after either one would
    # leak exactly what it is meant to withhold.
    if not await _authenticate(ws):
        return

    try:
        # Registered as the first line inside try/finally so a failure
        # anywhere below -- including the initial state push failing before
        # the loop even starts -- still guarantees unregister_client runs.
        hub.register_client(ws)
        logger.info("Client connected")
        # A newly connected client has missed every diff broadcast so far, so it
        # needs the full state once up front before it can rely on diffs alone.
        await ws.send_json({"type": "state", "data": get_state()})

        while True:
            message = await ws.receive_json()
            logger.info("Client sent: %s", message)
            if message.get("cmd") == "execute":
                await _handle_execute(message)
            elif message.get("cmd") == "set_value":
                await _handle_set_value(message)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister_client(ws)
        logger.info("Client disconnected")


async def _handle_execute(message: dict) -> None:
    req_id = message.get("req_id")
    item_id = message.get("item_id")

    item = get_item(item_id)
    if item is None:
        await hub.broadcast_to_clients(
            {"type": "result", "req_id": req_id, "status": "error", "message": "item not found"}
        )
        return

    bump_press_count(item_id)

    # Check connectivity before sending rather than waiting on a response
    # timeout: the agent link is a single persistent socket, so "not in
    # hub.agents" is already a definitive answer, not a transient race.
    if item["target"] not in hub.agents:
        await hub.broadcast_to_clients(
            {
                "type": "result",
                "req_id": req_id,
                "item_id": item_id,
                "status": "error",
                "message": "agent offline",
            }
        )
        return

    # override_type lets Long Press's Force Stop menu option send a
    # different command than a normal tap on the same tile, without
    # duplicating a second DB row per action -- params and target still
    # always come from the item row itself.
    override_type = message.get("override_type")

    await hub.send_to_agent(
        item["target"],
        {
            "cmd": override_type or item["type"],
            # Always the item's own type, even when overridden -- force_stop
            # needs this to derive a process name from the original item's
            # params (see handle_force_stop), since params alone don't say
            # whether "path" means a launch_app or something else.
            "item_type": item["type"],
            "params": json.loads(item["params"]),
            "req_id": req_id,
            "item_id": item_id,
        },
    )

    # Start the timeout only now that the command has actually reached an
    # agent: a request that never got forwarded (item missing, agent
    # offline) already got its "error" result above, synchronously — a
    # timer for it would just fire uselessly 5s later on a req_id nothing
    # is waiting on anymore.
    async def _on_timeout(rid: str) -> None:
        await hub.broadcast_to_clients(
            {
                "type": "result",
                "req_id": rid,
                "item_id": item_id,
                "status": "error",
                "message": "timeout",
            }
        )

    track(req_id, 5.0, _on_timeout)


async def _handle_set_value(message: dict) -> None:
    req_id = message.get("req_id")
    item_id = message.get("item_id")
    value = message.get("value")

    item = get_item(item_id)
    if item is None:
        await hub.broadcast_to_clients(
            {"type": "result", "req_id": req_id, "status": "error", "message": "item not found"}
        )
        return

    # Deliberately no bump_press_count() here: a slider fires this dozens of
    # times per drag, and press_count exists to measure discrete presses --
    # inflating it on every drag tick would make it useless for that.

    # Same connectivity check as _handle_execute, same reasoning: the agent
    # link is a single persistent socket, so "not in hub.agents" is already
    # a definitive answer, not a transient race.
    if item["target"] not in hub.agents:
        await hub.broadcast_to_clients(
            {
                "type": "result",
                "req_id": req_id,
                "item_id": item_id,
                "status": "error",
                "message": "agent offline",
            }
        )
        return

    await hub.send_to_agent(
        item["target"],
        {
            "cmd": item["type"],
            "params": {**json.loads(item["params"]), "value": value},
            "req_id": req_id,
            "item_id": item_id,
        },
    )

    # Same timeout mechanism as _handle_execute, started only once the
    # command has actually reached an agent -- see the comment above.
    async def _on_timeout(rid: str) -> None:
        await hub.broadcast_to_clients(
            {
                "type": "result",
                "req_id": rid,
                "item_id": item_id,
                "status": "error",
                "message": "timeout",
            }
        )

    track(req_id, 5.0, _on_timeout)
