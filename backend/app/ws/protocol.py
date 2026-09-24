"""What both WebSocket endpoints share: frame parsing and the hello handshake.

The two sockets speak the same dialect on purpose -- a hello frame carrying a
token first, then JSON objects -- so the rules for reading a frame and
admitting a connection live here once rather than drifting apart in
ws/agent.py and ws/client.py.
"""

import asyncio
import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.auth import token_ok

logger = logging.getLogger("controlhub.ws")

# How long a freshly accepted socket has to send its hello frame. A peer that
# connects and then says nothing is the "silent hang" this handshake exists to
# avoid -- without a bound, such a socket would sit in receive() forever,
# holding a connection that has never proved anything.
HELLO_TIMEOUT = 5.0

# Close codes. 4001 is "your hello was rejected" on both sockets; 4008 splits
# out "you never sent the hello at all", which is a different thing to debug
# from "your token was wrong". frontend/js/ws.js acts on 4001 (it forgets the
# stored token), so its meaning must not change.
CLOSE_UNAUTHORIZED = 4001
CLOSE_HELLO_TIMEOUT = 4008


async def receive_object(ws: WebSocket) -> dict | None:
    """Read one frame and return it as a JSON object, or None if it isn't one.

    A frame that is not JSON, or is JSON but not an object, is logged and
    answered with None so the caller can skip it. Before this, one bad frame
    raised out of the receive loop and dropped the connection -- for the
    agent socket that meant an "offline" flash on every phone.

    A disconnect still raises WebSocketDisconnect: that one the caller must
    see, to stop looping.
    """
    message = await ws.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    raw = message.get("text")
    if raw is None:
        raw = message.get("bytes") or b""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("Ignoring a frame that is not JSON")
        return None
    if not isinstance(data, dict):
        logger.warning("Ignoring a frame that is not a JSON object")
        return None
    return data


async def accept_hello(ws: WebSocket, token_env: str, who: str) -> dict | None:
    """Wait for the hello frame and check its token against `token_env`.

    Returns the hello on success. On any failure the socket is closed with the
    matching code and None comes back; the caller just returns. The check runs
    before the caller registers the socket anywhere, so a rejected peer never
    receives a single broadcast.
    """
    try:
        hello = await asyncio.wait_for(receive_object(ws), timeout=HELLO_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("%s sent no hello within %ss; closing", who, HELLO_TIMEOUT)
        await ws.close(code=CLOSE_HELLO_TIMEOUT)
        return None
    except WebSocketDisconnect:
        # Hung up mid-handshake. Nothing to close and nothing to log about.
        return None

    if hello is None or hello.get("type") != "hello":
        logger.warning("%s first frame was not a hello; closing", who)
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return None

    if not token_ok(token_env, hello.get("token")):
        logger.warning("%s presented an invalid token; closing", who)
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return None

    return hello
