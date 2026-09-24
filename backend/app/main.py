import logging
import mimetypes
import os
import re
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from app.api.access import router as access_router
from app.api.agents import router as agents_router
from app.api.items import router as items_router
from app.api.screenshot import router as screenshot_router
from app.api.settings import router as settings_router
from app.api.widgets import router as widgets_router
from app.api.workspaces import router as workspaces_router
from app.db import (
    fixup_audio_switch_state_key,
    fixup_close_agent_item,
    fixup_day4_items,
    fixup_legacy_seed,
    fixup_mic_item,
    fixup_remove_placeholder_tiles,
    fixup_toggle_off_colors,
    fixup_volume_item,
    fixup_vpn_item,
    fixup_vpn_tile_type,
    fixup_widget_types,
    init_db,
    seed_if_empty,
)
from app.models import get_workspaces_with_items
from app.ws.agent import agent_ws
from app.ws.client import client_ws

# Without force=True, uvicorn's own dictConfig (which sets
# disable_existing_loggers) wins and app loggers stay silent. Placed after the
# imports rather than before them: no module logs at import time, and a
# logger only needs a handler by the time it is first used.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    force=True,
)
logging.getLogger("controlhub").setLevel(logging.INFO)


class _AccessLogFilter(logging.Filter):
    """Tidy uvicorn's access log: mask tokens, drop health probes.

    The Dashboard link the launcher prints is /?token=<CLIENT_TOKEN>, so
    every phone that opened it wrote the secret into backend.log in clear.
    The request itself is unchanged; only its log line is.

    /health is polled by Docker's HEALTHCHECK every 30 s (and by deploy.sh
    and check.sh); a successful probe says nothing a reader needs, and would
    otherwise be most of the log. A failing one still gets through.
    """

    _TOKEN = re.compile(r"(token=)[^&\s]*", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple):
            return True
        # uvicorn's access record: (client, method, path, http_version, status)
        if len(args) >= 5 and args[2] == "/health" and args[4] == 200:
            return False
        record.args = tuple(
            self._TOKEN.sub(r"\1***", arg) if isinstance(arg, str) else arg for arg in args
        )
        return True


# On the logger object, which uvicorn's dictConfig reconfigures (level,
# handlers) but never strips of filters -- so this holds whether uvicorn
# configures logging before importing the app (Docker's CLI) or after
# (the standalone launcher's uvicorn.run).
logging.getLogger("uvicorn.access").addFilter(_AccessLogFilter())

# Load before anything reads os.environ (the token checks read it on every
# request, not just at import time, but this keeps env setup in one place at
# process start).
load_dotenv()


def run_startup_migrations() -> None:
    init_db()
    seed_if_empty()
    fixup_remove_placeholder_tiles()
    fixup_legacy_seed()
    fixup_mic_item()
    fixup_volume_item()
    # Must run after fixup_volume_item(): it places Headphones in the grid
    # cell that Volume's move vacates, so the ordering here is load-bearing.
    fixup_day4_items()
    fixup_vpn_item()
    # After fixup_vpn_item(), which is what creates the row on a fresh db.
    fixup_vpn_tile_type()
    fixup_close_agent_item()
    fixup_audio_switch_state_key()
    # Last: it rewrites the colour of rows the fixups above create, so it has
    # to see them in their settled state (VPN in particular does not exist
    # until fixup_vpn_item() has run at least once).
    fixup_toggle_off_colors()
    # After every fixup that writes a type, because it exists to undo what one
    # of them used to write -- running it earlier would let the same startup
    # break the row again.
    fixup_widget_types()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    run_startup_migrations()
    yield


app = FastAPI(lifespan=lifespan)


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
app.include_router(widgets_router)
app.include_router(access_router)


# Starlette matches routes in registration order, so a catch-all mount at "/"
# must always be registered last. StaticFiles asserts scope["type"] == "http"
# and returns 500 for anything else, so mounting it earlier than the
# websocket routes above would swallow /ws/agent and /ws/client (and shadow
# /health and /api). Keep this mount at the bottom of the file.
#
# Default "frontend" is unchanged (cwd-relative, matches Docker's WORKDIR
# /app where the bind mount lands at /app/frontend). The standalone launcher
# passes an absolute path instead, since a desktop shortcut/frozen exe has
# no fixed cwd to resolve "frontend" against.
FRONTEND_DIR = os.environ.get("ITDECK_FRONTEND_DIR", "frontend")
# StaticFiles takes a file's type from Python's mimetypes, which on Windows
# reads the registry -- and a stock Windows has no entry for .webp, so the
# guide's pictures went out as text/plain and the browser refused them.
# Registered here so the answer no longer depends on the machine.
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("application/manifest+json", ".webmanifest")


class RevalidatedStaticFiles(StaticFiles):
    """The frontend, served with Cache-Control: no-cache.

    Without it browsers apply heuristic caching to files with a
    Last-Modified date, and after an update a phone (or Studio) kept running
    the old app.js / grid.css against the new index.html -- a mismatch that
    can collapse the deck to nothing. no-cache still lets the browser keep a
    copy; it just has to ask first, and the ETag makes that a 304.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", RevalidatedStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
