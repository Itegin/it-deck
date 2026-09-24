"""Turning backend command frames into handler calls and result frames.

Kept apart from agent.py, which owns the HANDLERS table and the Windows-only
imports, so this -- the part every command passes through -- can be tested on
any machine.

The contract it keeps is CLAUDE.md's: every command that arrives with a req_id
gets exactly one result frame back. A bad frame, a missing field or a handler
that raises becomes an error *result*, never an exception out of the receive
loop -- one of those used to drop the whole connection, which every phone saw
as the agent going offline.
"""

import json
import traceback
from typing import Awaitable, Callable, Optional


def run_handler(handlers: dict, message: dict) -> dict:
    """Call the handler `message` names and return its result dict."""
    cmd = message.get("cmd")
    handler = handlers.get(cmd)
    if handler is None:
        return {"status": "error", "message": f"unknown command: {cmd}"}

    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return {"status": "error", "message": "params must be an object"}

    try:
        # force_stop is the one handler that needs more than its own params:
        # it derives a process name from the ORIGINAL item's type (see
        # handle_force_stop), which backend/app/ws/client.py sends alongside
        # the (possibly overridden) cmd specifically for this.
        if cmd == "force_stop":
            result = handler(params, message.get("item_type"))
        else:
            result = handler(params)
    except Exception as exc:
        # Handlers are written to catch their own errors; this is the net for
        # the one that doesn't. The traceback goes to agent.log, the person
        # holding the phone gets one line.
        traceback.print_exc()
        return {"status": "error", "message": f"{cmd} failed: {exc}"}

    if not isinstance(result, dict):
        return {"status": "error", "message": f"{cmd} returned no result"}
    return result


async def receive_loop(
    ws,
    handlers: dict,
    on_shutdown: Callable[[], Awaitable[None]],
    on_watchers: Optional[Callable[[bool], None]] = None,
) -> None:
    """Answer every command that arrives on `ws` until it closes.

    `on_watchers` gets the backend's {"type": "watchers", "active": bool}
    notice: whether any Dashboard is connected, which decides whether the
    poller has anyone to report to.
    """
    async for raw in ws:
        try:
            message = json.loads(raw)
        except ValueError:
            print("Ignoring a frame that is not JSON")
            continue
        if not isinstance(message, dict):
            continue
        if message.get("type") == "watchers":
            if on_watchers is not None:
                on_watchers(bool(message.get("active")))
            continue
        cmd = message.get("cmd")
        req_id = message.get("req_id")
        if cmd is None and req_id is None:
            # Nothing asked, nothing to answer. A frame with a req_id but no
            # cmd still gets its result below ("unknown command: None").
            continue
        # The command and its id, not the whole frame: params can carry paths
        # and URLs, and one line per press is what makes agent.log readable.
        print(f"Received {cmd} ({req_id})")

        result = run_handler(handlers, message)
        await ws.send(json.dumps({
            "type": "result",
            "req_id": req_id,
            "item_id": message.get("item_id"),
            # Spread last, so a handler's extra keys (list_devices' `devices`,
            # fetch_icon's `icon`) ride along with its status.
            **result,
        }))

        if cmd == "agent_shutdown" and result.get("status") == "ok":
            await on_shutdown()
