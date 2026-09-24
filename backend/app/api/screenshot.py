import logging
from datetime import datetime

from fastapi import APIRouter, File, Header, HTTPException, UploadFile

from app.auth import check_agent_token
from app.db import DB_PATH

logger = logging.getLogger("controlhub.api")

router = APIRouter()

# Same base directory as the SQLite file (DB_PATH's parent), not a second
# hardcoded copy of "/app/data" -- one source of truth for where this
# container's persistent data volume is mounted.
SCREENSHOT_DIR = DB_PATH.parent / "screenshots"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # a 1920x1080 PNG is a few hundred KB; this is headroom, not a target


@router.post("/api/screenshot")
async def upload_screenshot(
    file: UploadFile = File(...),
    x_agent_token: str | None = Header(None),
) -> dict:
    # Reuses AGENT_TOKEN -- the same shared secret the WebSocket agent
    # connection already requires -- as a lightweight gate on this HTTP
    # route too. This is not a real auth system (no per-caller identity, no
    # expiry, no rate limiting): it's the minimal guard that fits a
    # single-user, local-network project per CLAUDE.md's documented scope,
    # closing the "anyone on the LAN can write files to this server's disk"
    # gap without building out something this project doesn't need.
    check_agent_token(x_agent_token)

    # Client-declared content-type only, not a magic-bytes check -- good
    # enough to reject an obviously-wrong upload from an already-token-
    # authenticated caller, not a defense against a hostile one.
    if file.content_type != "image/png":
        raise HTTPException(status_code=400, detail="expected a PNG file (image/png)")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (max 10MB)")

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    # Server-side clock, not file.filename -- the client's claimed name is
    # never trusted for the save path.
    filename = f"{datetime.now():%Y%m%d_%H%M%S}.png"
    (SCREENSHOT_DIR / filename).write_bytes(contents)

    logger.info("Screenshot saved: %s", filename)
    return {"status": "ok", "filename": filename}
