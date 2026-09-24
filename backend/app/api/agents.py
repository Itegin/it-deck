import asyncio
import logging
import secrets
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth import check_agent_token
from app.agent_requests import create_future, discard_future
from app.ws.hub import hub

logger = logging.getLogger("controlhub.api")

router = APIRouter()

# Same budget as the WebSocket execute path (app.ws.client): every command
# sent to an agent resolves within 5s -- ok, error, or timeout.
AGENT_REQUEST_TIMEOUT = 5.0



def _generate_req_id() -> str:
    # Same shape as frontend/js/ws.js's generateReqId() -- millisecond
    # timestamp plus a short random suffix -- because ids from both sources
    # share one req_id keyspace once they reach the agent. The "api-" prefix
    # makes a server-issued id impossible to confuse with a client-issued one.
    return f"api-{int(time.time() * 1000)}-{secrets.token_hex(4)}"


async def _ask_agent(agent_name: str, cmd: str, params: dict) -> dict:
    """Send one query command to a connected agent and return its answer.

    Shared by every Studio query below (list_devices, list_apps,
    fetch_icon): they differ only in the command name and its params.
    """
    # Checked before sending rather than left to the timeout: the agent link
    # is a single persistent socket, so "not in hub.agents" is already a
    # definitive answer -- same reasoning as _handle_execute in ws/client.py.
    if agent_name not in hub.agents:
        raise HTTPException(status_code=404, detail="agent offline")

    req_id = _generate_req_id()
    # Registered before the send, not after: the agent could reply before
    # this coroutine is scheduled again, and a reply arriving with no future
    # registered is silently dropped.
    fut = create_future(req_id)

    sent = await hub.send_to_agent(agent_name, {"cmd": cmd, "params": params, "req_id": req_id})
    if not sent:
        # The socket died between the membership check above and the send;
        # send_to_agent already closed it. No point parking for
        # 5s on a reply that can no longer come.
        discard_future(req_id)
        raise HTTPException(status_code=404, detail="agent offline")

    try:
        result = await asyncio.wait_for(fut, timeout=AGENT_REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        discard_future(req_id)
        logger.warning("Agent '%s' did not answer %s req_id=%s in time", agent_name, cmd, req_id)
        raise HTTPException(status_code=504, detail="agent did not respond in time")

    # Returned verbatim, deliberately. A 4xx/5xx from this endpoint means the
    # *request* failed (agent offline, no reply in time); the agent handler's
    # own {"status": "error", ...} is a successful round-trip reporting a
    # failed operation, and passes through as a normal 200 -- the same
    # distinction ws/client.py already draws between "agent offline" and a
    # result the agent actually sent back.
    return result


@router.post("/api/agents/{agent_name}/list_devices")
async def list_devices(agent_name: str, x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)
    return await _ask_agent(agent_name, "list_devices", {})


@router.post("/api/agents/{agent_name}/list_apps")
async def list_apps(agent_name: str, x_agent_token: str | None = Header(None)) -> dict:
    # Studio's "choose from installed" list: the Start Menu shortcuts of the
    # PC the agent runs on, so nobody has to type an exe path by hand.
    check_agent_token(x_agent_token)
    return await _ask_agent(agent_name, "list_apps", {})


class FetchIconRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


@router.post("/api/agents/{agent_name}/fetch_icon")
async def fetch_icon(agent_name: str, body: FetchIconRequest, x_agent_token: str | None = Header(None)) -> dict:
    # The site icon for a Website tile. Fetched by the agent, not here: the
    # agent is on the PC whose browser the tile opens, it already carries
    # Pillow for screenshots, and the backend (which may be a server on the
    # LAN) never gains an endpoint that makes outbound requests on command.
    check_agent_token(x_agent_token)
    return await _ask_agent(agent_name, "fetch_icon", {"url": body.url})
