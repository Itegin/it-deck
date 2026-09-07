import logging

# Without force=True, uvicorn's own dictConfig (which sets
# disable_existing_loggers) wins and app loggers stay silent.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    force=True,
)
logging.getLogger("controlhub").setLevel(logging.INFO)

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from app.api.agents import router as agents_router
from app.api.items import router as items_router
from app.api.screenshot import router as screenshot_router
from app.api.settings import router as settings_router
from app.api.workspaces import router as workspaces_router
from app.db import (
    fixup_audio_switch_state_key,
    fixup_day4_items,
    fixup_legacy_seed,
    fixup_mic_item,
    fixup_volume_item,
    fixup_vpn_item,
    init_db,
    seed_if_empty,
)
from app.models import get_workspaces_with_items
from app.ws.agent import agent_ws
from app.ws.client import client_ws

# Load before anything reads os.environ (agent.py checks AGENT_TOKEN on
# each connection, not just at import time, but this keeps env setup in
# one place at process start).
load_dotenv()

app = FastAPI()


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    seed_if_empty()
    fixup_legacy_seed()
    fixup_mic_item()
    fixup_volume_item()
    # Must run after fixup_volume_item(): it places Headphones in the grid
    # cell that Volume's move vacates, so the ordering here is load-bearing.
    fixup_day4_items()
    fixup_vpn_item()
    fixup_audio_switch_state_key()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/workspaces")
def list_workspaces() -> list[dict]:
    return get_workspaces_with_items()


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    await agent_ws(ws)


@app.websocket("/ws/client")
async def ws_client(ws: WebSocket) -> None:
    await client_ws(ws)


app.include_router(screenshot_router)
app.include_router(items_router)
app.include_router(workspaces_router)
app.include_router(agents_router)
app.include_router(settings_router)


# Starlette matches routes in registration order, so a catch-all mount at "/"
# must always be registered last. StaticFiles asserts scope["type"] == "http"
# and returns 500 for anything else, so mounting it earlier than the
# websocket routes above would swallow /ws/agent and /ws/client (and shadow
# /health and /api). Keep this mount at the bottom of the file.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
