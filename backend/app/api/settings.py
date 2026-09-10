import logging
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_connection
from app.ws.hub import hub

logger = logging.getLogger("controlhub.api")

router = APIRouter()

# The Dashboard's visual theme. An allowlist, not a free string, and that is
# load-bearing rather than tidiness: the value ends up written straight into a
# data-theme attribute on <html> by the client. frontend/js/theme.js re-checks
# it against the same four names on the way in, because the client also reads
# this value from localStorage and off the WebSocket, neither of which came
# through this endpoint. Both ends validate; neither trusts the other.
#
# Four places have to agree on these slugs, byte for byte: this tuple,
# THEMES in frontend/js/theme.js, the inline boot allowlist in the <head> of
# frontend/index.html, and the [data-theme=...] blocks in css/themes.css.
# Note that this list ships in the backend image while the other three are
# bind-mounted -- deploying the frontend ahead of the backend makes a new
# theme 422 here and silently revert on the phone.
THEMES = ("flat", "pastel", "glossy", "liquid-glass")
DEFAULT_THEME = "flat"

# The light/dark axis, and a *separate* setting from the theme on purpose:
# these are two independent choices (four materials x two grounds), and
# folding them into eight theme slugs would double the theme cycle, double the
# allowlists above, and make "keep my theme, flip the ground" impossible to
# express. Same allowlist discipline, same reason -- it becomes a data-mode
# attribute on <html>.
#
# "auto" is not a browser or OS preference. It means "whichever ground this
# theme shipped with": dark for flat/glossy/liquid-glass, light for pastel.
# It exists because this setting is *new*, and a two-value key could not have
# a default that leaves every existing deck looking the way it does today --
# defaulting to dark would flip a Pastel deck, defaulting to light would flip
# the other three. The per-theme table that resolves it lives in
# frontend/js/theme.js (NATIVE_MODE) and, for the pre-paint case, in
# css/themes.css's selectors; deliberately NOT here, because the server has
# no business knowing what a theme looks like.
MODES = ("auto", "light", "dark")
DEFAULT_MODE = "auto"


class ThemeUpdate(BaseModel):
    theme: str


class ModeUpdate(BaseModel):
    mode: str


def _read_setting(
    conn: sqlite3.Connection, key: str, allowed: tuple[str, ...], default: str
) -> str:
    row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
    if row is None:
        # No row means "never set" -- there is no seed for this table, so every
        # reader supplies its own default (see the setting table's note in
        # db.py).
        return default

    value = row["value"]
    if value not in allowed:
        # A row written by an older build, a future one, or by hand. Serving it
        # would only hand the client something it is going to reject anyway, so
        # answer with the default and say so once in the log.
        logger.warning("Stored %s %r is not a known value; serving %r", key, value, default)
        return default
    return value


def _write_setting(key: str, value: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO setting (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


# Unauthenticated, and that is a deliberate widening worth naming. Every other
# write surface in this app (all of /api/items, the /api/workspaces mutations)
# is gated on the AGENT_TOKEN shared secret -- but the only client that can
# change the theme is the Dashboard on the phone, which has no token and, over
# plain http on the LAN, has nowhere safe to keep one. So this sits with
# GET /api/workspaces on the open side of the line.
#
# What that concedes: anyone who can already reach the backend on the local
# network can change how the deck looks. It cannot execute a command, reach an
# agent, or read or alter the item catalog -- each value is constrained to one
# of the literals in THEMES / MODES before it is stored. Consistent with the
# single-user, local-network scope CLAUDE.md documents; revisit alongside the
# rest of the auth story if that scope ever changes.
#
# PUT /api/settings/mode joins the theme on this side of the line for exactly
# the same reason, even though its own control lives in Studio (which does
# hold the agent token): the mode is read and applied by the tokenless
# Dashboard, and gating the write while leaving the read open would buy
# nothing.
@router.get("/api/settings")
def get_settings() -> dict:
    conn = get_connection()
    try:
        theme = _read_setting(conn, "theme", THEMES, DEFAULT_THEME)
        mode = _read_setting(conn, "mode", MODES, DEFAULT_MODE)
    finally:
        conn.close()

    return {"theme": theme, "mode": mode}


@router.put("/api/settings/theme")
async def set_theme(update: ThemeUpdate) -> dict:
    if update.theme not in THEMES:
        raise HTTPException(
            status_code=422,
            detail=f"theme must be one of: {', '.join(THEMES)}",
        )

    _write_setting("theme", update.theme)

    # The theme is one shared choice, so the panel that changed it is not the
    # only one that needs to know. Carries the new value rather than being a
    # bare signal like "workspace_update": there is nothing to re-fetch and
    # re-render here, so making every connected panel round-trip back to
    # GET /api/settings just to learn one word would be pure ceremony.
    #
    # Only the key that changed is sent, never the whole settings object --
    # which is why every client-side consumer must test for a key's *presence*
    # rather than truthiness (an absent theme in a mode frame is "unchanged",
    # not "revert to the default"). See app.js's onSettingsUpdate.
    await hub.broadcast_to_clients(
        {"type": "settings_update", "settings": {"theme": update.theme}}
    )
    logger.info("Theme set to %r", update.theme)

    return {"theme": update.theme}


@router.put("/api/settings/mode")
async def set_mode(update: ModeUpdate) -> dict:
    if update.mode not in MODES:
        raise HTTPException(
            status_code=422,
            detail=f"mode must be one of: {', '.join(MODES)}",
        )

    _write_setting("mode", update.mode)

    await hub.broadcast_to_clients(
        {"type": "settings_update", "settings": {"mode": update.mode}}
    )
    logger.info("Mode set to %r", update.mode)

    return {"mode": update.mode}
