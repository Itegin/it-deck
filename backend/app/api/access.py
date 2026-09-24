"""Studio's Access dialog: see and change the two tokens.

Both default to "admin", and most people never touch them; this is for the
ones who share their Wi-Fi. A change is written to config.env (so it holds
across restarts) *and* applied to this process at once -- app.auth reads the
environment on every check, so nothing needs restarting:

- a new phone token disconnects every phone (close code 4001, which the deck
  answers by asking for the token once), so a phone that knew the old one is
  out the moment it changes;
- a new Studio token applies from the next request. The agent's open socket
  stays up; it reads the new value from config.env the next time it connects
  (agents/windows/agent.py).
"""

import logging
import os
import re

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.auth import check_agent_token
from app.config_file import config_path, update_config
from app.ws.hub import hub
from app.ws.protocol import CLOSE_UNAUTHORIZED

logger = logging.getLogger("controlhub.api")

router = APIRouter()

# URL-safe, because the phone token rides in the dashboard link and its QR
# code (?token=...), and nothing in it can break a config.env line (no
# newline, '#', '=' or quote). 4 is the floor so "admin" stays valid.
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{4,64}$")


class AccessUpdate(BaseModel):
    client_token: str | None = None
    agent_token: str | None = None


@router.get("/api/access")
def get_access(x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)
    return {
        "editable": config_path() is not None,
        "client_token": os.environ.get("CLIENT_TOKEN", ""),
    }


@router.put("/api/access")
async def update_access(body: AccessUpdate, x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)
    path = config_path()
    if path is None:
        raise HTTPException(
            status_code=409,
            detail="tokens are set in the server's .env on this install, not from Studio",
        )

    wanted = {
        env_key: value
        for env_key, value in (("CLIENT_TOKEN", body.client_token), ("AGENT_TOKEN", body.agent_token))
        if value is not None
    }
    if not wanted:
        raise HTTPException(status_code=400, detail="nothing to change")
    for env_key, value in wanted.items():
        if not TOKEN_PATTERN.match(value):
            raise HTTPException(
                status_code=400,
                detail=f"{env_key}: 4-64 characters, letters, digits and . _ ~ - only",
            )

    changed = {key: value for key, value in wanted.items() if os.environ.get(key) != value}
    if not changed:
        return {"status": "ok", "changed": []}

    # File first: if it can't be written, nothing changes anywhere and the
    # person is told so -- rather than a token that works until the next
    # restart and then silently reverts.
    try:
        update_config(path, changed)
    except OSError as exc:
        logger.error("Could not write %s: %s", path, exc)
        raise HTTPException(status_code=500, detail="could not save config.env")
    os.environ.update(changed)
    # Names only: the values are secrets.
    logger.info("Access tokens changed: %s", ", ".join(sorted(changed)))

    if "CLIENT_TOKEN" in changed:
        hub.close_all_clients(CLOSE_UNAUTHORIZED)
    return {"status": "ok", "changed": sorted(changed)}
