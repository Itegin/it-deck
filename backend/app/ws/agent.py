import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.agent_requests import resolve_future
from app.pending import resolve
from app.state import update_state
from app.ws.hub import hub
from app.ws.protocol import accept_hello, receive_object

logger = logging.getLogger("controlhub.ws")

# An agent name becomes the prefix of every state key ("windows:mic.muted")
# and is matched against the item table's `target` column, so it may not
# contain the ':' that separates the two, and is kept short and printable.
MAX_AGENT_NAME = 64


async def agent_ws(ws: WebSocket) -> None:
    await ws.accept()

    # Token checked before the agent is registered, so a bad/missing token
    # never makes it into the hub, even for an instant.
    hello = await accept_hello(ws, "AGENT_TOKEN", "Agent")
    if hello is None:
        return

    name = hello.get("agent") or "windows"
    if not isinstance(name, str) or len(name) > MAX_AGENT_NAME or ":" in name or not name.isprintable():
        logger.warning("Agent hello carried an unusable name; closing")
        await ws.close(code=4001)
        return

    hub.register_agent(name, ws)
    logger.info("Agent '%s' connected (version %s)", name, hello.get("version", "?"))
    await hub.broadcast_to_clients({"type": "agent_status", "agent": name, "status": "online"})

    try:
        while True:
            message = await receive_object(ws)
            if message is None:
                continue
            # Per frame, so DEBUG: the poller sends one state frame a second,
            # forever, and at INFO this line alone grew backend.log by tens
            # of megabytes a day.
            logger.debug("Agent '%s' sent: %s", name, message)
            kind = message.get("type")
            if kind == "result":
                _log_result(name, message)
                req_id = message.get("req_id")
                # Cancel the pending timeout before broadcasting: a real
                # reply -- even a late one racing the timer -- should always
                # win over a synthetic "timeout" result.
                resolve(req_id)
                # Additive third destination: hands the reply back to an HTTP
                # caller parked on this req_id (see app.agent_requests). A
                # no-op for every ordinary execute/set_value result, which is
                # the common case -- nothing above changes because of it.
                resolve_future(req_id, message)
                await hub.broadcast_to_clients(message)
            elif kind == "state":
                data = message.get("data")
                if not isinstance(data, dict):
                    logger.warning("Agent '%s' sent a state frame without a data object", name)
                    continue
                # Agents all send the same generic keys ("mic.muted",
                # "speaker.volume", ...), so namespace them with the
                # reporting agent's name here -- otherwise two connected
                # agents overwrite each other in the single flat state dict
                # and every client sees whichever reported last.
                namespaced = {f"{name}:{key}": value for key, value in data.items()}
                changed = update_state(namespaced)
                # Skip the broadcast entirely when nothing changed (the poller
                # resends the whole snapshot every tick) to avoid spamming
                # clients.
                if changed:
                    logger.info("State changed: %s", changed)
                    await hub.broadcast_to_clients({"type": "state", "data": changed})
    except WebSocketDisconnect:
        pass
    finally:
        # Only when this socket was still the registered one. After a
        # reconnect it is not: the new connection already took the name, and
        # announcing "offline" here would grey out a live agent on every
        # phone -- see ConnectionHub.unregister_agent.
        if hub.unregister_agent(name, ws):
            logger.info("Agent '%s' disconnected", name)
            await hub.broadcast_to_clients({"type": "agent_status", "agent": name, "status": "offline"})
        else:
            logger.info("Agent '%s' previous connection closed", name)


def _log_result(name: str, message: dict) -> None:
    # One line per command, without the payload: list_apps answers with the
    # whole Start Menu and fetch_icon with a base64 image, neither of which
    # belongs in a log that is meant to be read.
    status = message.get("status")
    if status == "ok":
        logger.info("Agent '%s' result req_id=%s: ok", name, message.get("req_id"))
    else:
        logger.info(
            "Agent '%s' result req_id=%s: %s (%s)",
            name,
            message.get("req_id"),
            status,
            message.get("message", ""),
        )
