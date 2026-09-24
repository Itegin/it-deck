import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.models import bump_press_count, get_item, get_referenced_agents
from app.pending import track
from app.state import get_state
from app.ws.hub import hub
from app.ws.protocol import accept_hello, receive_object

logger = logging.getLogger("controlhub.ws")

# Every command resolves within this many seconds: ok, error, or a synthetic
# "timeout" broadcast by app.pending. frontend/js/ws.js keeps its own backstop
# deliberately longer (COMMAND_TIMEOUT_MS) so this one's reason always wins.
COMMAND_TIMEOUT = 5.0

# The agent commands a client may run *instead of* a tile's own type. Only the
# long-press menu's Force Stop exists (frontend/js/app.js). Without this list
# any phone could send any agent command against any tile -- agent_shutdown,
# list_apps, launch_app on a tile that was never a launcher -- because the
# override replaces `cmd` wholesale. Keep in step with the long-press menu.
ALLOWED_OVERRIDES = frozenset({"force_stop"})


async def client_ws(ws: WebSocket) -> None:
    await ws.accept()

    # Before register_client() and before the state push, and that ordering is
    # the point rather than an implementation detail: a socket in hub.clients
    # already receives every broadcast, and get_state() is itself part of what
    # the gate protects.
    #
    # Fails closed when CLIENT_TOKEN is unset, deliberately: a backend with no
    # CLIENT_TOKEN refuses every Dashboard connection rather than silently
    # running without the gate.
    if await accept_hello(ws, "CLIENT_TOKEN", "Client") is None:
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

        # Same argument, for the agents. agent_status has only ever been sent
        # when one connects or drops, so a client that arrives while an agent
        # is already down was never told -- it drew live-looking tiles for an
        # agent that could not answer any of them, and only found out when a
        # press timed out. That is also what decides whether the deck falls
        # back to its clock (see setAgentOffline in render.js), which has to
        # be right on the first frame rather than after the next event.
        #
        # One frame per *referenced* agent rather than per connected one:
        # "offline" is a statement about an agent that is not here, so the
        # names have to come from the tiles, not from the hub.
        for agent in get_referenced_agents():
            await ws.send_json({
                "type": "agent_status",
                "agent": agent,
                "status": "online" if agent in hub.agents else "offline",
            })

        while True:
            message = await receive_object(ws)
            if message is None:
                continue
            # DEBUG, not INFO: a volume drag sends dozens of these a second.
            logger.debug("Client sent: %s", message)
            cmd = message.get("cmd")
            if cmd == "execute":
                await _handle_execute(message)
            elif cmd == "set_value":
                await _handle_set_value(message)
            else:
                # Answered, to this socket only, rather than dropped: a caller
                # with a req_id is waiting on it, and the "every req_id
                # resolves" rule does not stop at commands we recognise.
                await ws.send_json(_error(message.get("req_id"), None, f"unknown command: {cmd}"))
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister_client(ws)
        logger.info("Client disconnected")


def _error(req_id, item_id, text: str) -> dict:
    result = {"type": "result", "req_id": req_id, "status": "error", "message": text}
    if item_id is not None:
        result["item_id"] = item_id
    return result


def _load_item(message: dict) -> tuple[dict | None, str | None]:
    """The item a command names, or the error text to answer with instead."""
    item_id = message.get("item_id")
    # bool is an int subclass; True is not an item id.
    if not isinstance(item_id, int) or isinstance(item_id, bool):
        return None, "item not found"
    item = get_item(item_id)
    if item is None:
        return None, "item not found"
    return item, None


def _item_params(item: dict) -> dict | None:
    # api/items.py only lets a JSON object in, but rows written before that
    # check, or by hand, may hold anything -- and one bad row must fail its own
    # press, not the connection every other press travels on.
    try:
        params = json.loads(item["params"] or "{}")
    except (ValueError, TypeError):
        return None
    return params if isinstance(params, dict) else None


async def _dispatch(item: dict, req_id, command: dict) -> None:
    """Send `command` to the item's agent and start its timeout, or fail now.

    The connectivity check runs before sending rather than being left to the
    timeout: the agent link is a single persistent socket, so "not in
    hub.agents" is already a definitive answer. A send that fails is the same
    answer arrived a moment later.
    """
    item_id = item["id"]
    target = item["target"]
    if target not in hub.agents or not await hub.send_to_agent(target, command):
        await hub.broadcast_to_clients(_error(req_id, item_id, "agent offline"))
        return

    # Started only now that the command has actually reached an agent: a
    # request that never got forwarded already has its answer above, and a
    # timer for it would just fire uselessly later on a req_id nothing is
    # waiting on anymore.
    async def _on_timeout(rid: str) -> None:
        await hub.broadcast_to_clients(_error(rid, item_id, "timeout"))

    track(req_id, COMMAND_TIMEOUT, _on_timeout)


async def _handle_execute(message: dict) -> None:
    req_id = message.get("req_id")
    item, problem = _load_item(message)
    if item is None:
        await hub.broadcast_to_clients(_error(req_id, None, problem))
        return

    # override_type lets Long Press's Force Stop menu option send a different
    # command than a normal tap on the same tile, without a second DB row per
    # action -- params and target still always come from the item row itself.
    override_type = message.get("override_type")
    if override_type is not None and override_type not in ALLOWED_OVERRIDES:
        await hub.broadcast_to_clients(_error(req_id, item["id"], "command not allowed"))
        return

    params = _item_params(item)
    if params is None:
        await hub.broadcast_to_clients(_error(req_id, item["id"], "tile settings are invalid"))
        return

    bump_press_count(item["id"])

    await _dispatch(
        item,
        req_id,
        {
            "cmd": override_type or item["type"],
            # Always the item's own type, even when overridden -- force_stop
            # needs this to derive a process name from the original item's
            # params (see handle_force_stop), since params alone don't say
            # whether "path" means a launch_app or something else.
            "item_type": item["type"],
            "params": params,
            "req_id": req_id,
            "item_id": item["id"],
        },
    )


async def _handle_set_value(message: dict) -> None:
    req_id = message.get("req_id")
    item, problem = _load_item(message)
    if item is None:
        await hub.broadcast_to_clients(_error(req_id, None, problem))
        return

    params = _item_params(item)
    if params is None:
        await hub.broadcast_to_clients(_error(req_id, item["id"], "tile settings are invalid"))
        return

    # Deliberately no bump_press_count() here: a slider fires this dozens of
    # times per drag, and press_count exists to measure discrete presses --
    # inflating it on every drag tick would make it useless for that.
    await _dispatch(
        item,
        req_id,
        {
            "cmd": item["type"],
            "params": {**params, "value": message.get("value")},
            "req_id": req_id,
            "item_id": item["id"],
        },
    )
