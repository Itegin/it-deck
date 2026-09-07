import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection
from app.ws.hub import hub

logger = logging.getLogger("controlhub.api")

router = APIRouter()

# The Dashboard's visual theme. An allowlist, not a free string, and that is
# load-bearing rather than tidiness: the value ends up written straight into a
# data-theme attribute on <html> by the client. frontend/js/theme.js re-checks
# it against the same three names on the way in, because the client also reads
# this value from localStorage and off the WebSocket, neither of which came
# through this endpoint. Both ends validate; neither trusts the other.
THEMES = ("flat", "pastel", "glossy")
DEFAULT_THEME = "flat"


class ThemeUpdate(BaseModel):
    theme: str


# Unauthenticated, and that is a deliberate widening worth naming. Every other
# write surface in this app (all of /api/items, the /api/workspaces mutations)
# is gated on the AGENT_TOKEN shared secret -- but the only client that can
# change the theme is the Dashboard on the phone, which has no token and, over
# plain http on the LAN, has nowhere safe to keep one. So this sits with
# GET /api/workspaces on the open side of the line.
#
# What that concedes: anyone who can already reach the backend on the local
# network can change how the deck looks. It cannot execute a command, reach an
# agent, or read or alter the item catalog -- the value is constrained to one
# of three literals before it is stored. Consistent with the single-user,
# local-network scope CLAUDE.md documents; revisit alongside the rest of the
# auth story if that scope ever changes.
@router.get("/api/settings")
def get_settings() -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM setting WHERE key = 'theme'").fetchone()
    finally:
        conn.close()

    theme = row["value"] if row is not None else DEFAULT_THEME
    if theme not in THEMES:
        # A row written by an older build, a future one, or by hand. Serving it
        # would only hand the client something it is going to reject anyway, so
        # answer with the default and say so once in the log.
        logger.warning("Stored theme %r is not a known theme; serving %r", theme, DEFAULT_THEME)
        theme = DEFAULT_THEME

    return {"theme": theme}


@router.put("/api/settings/theme")
async def set_theme(update: ThemeUpdate) -> dict:
    if update.theme not in THEMES:
        raise HTTPException(
            status_code=422,
            detail=f"theme must be one of: {', '.join(THEMES)}",
        )

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO setting (key, value) VALUES ('theme', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (update.theme,),
        )
        conn.commit()
    finally:
        conn.close()

    # The theme is one shared choice, so the panel that changed it is not the
    # only one that needs to know. Carries the new value rather than being a
    # bare signal like "workspace_update": there is nothing to re-fetch and
    # re-render here, so making every connected panel round-trip back to
    # GET /api/settings just to learn one word would be pure ceremony.
    await hub.broadcast_to_clients(
        {"type": "settings_update", "settings": {"theme": update.theme}}
    )
    logger.info("Theme set to %r", update.theme)

    return {"theme": update.theme}
