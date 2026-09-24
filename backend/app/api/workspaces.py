import itertools
import json
import logging
import sqlite3
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.items import validate_params_json, validate_placement
from app.auth import check_agent_token
from app.db import get_connection
from app.ws.hub import hub

logger = logging.getLogger("controlhub.api")

router = APIRouter()


class WorkspaceCreate(BaseModel):
    name: str
    grid_cols: int = 3
    grid_rows: int = 5



def _pack_items(items: list[dict], grid_cols: int) -> list[tuple[int, int, int]]:
    # Pure placement pass, kept out of the endpoint so it can be exercised
    # without a DB (app.db.DB_PATH is a container path). Takes items in the
    # order they should be packed and returns (id, row, col) triples.
    occupied: set[tuple[int, int]] = set()
    placements: list[tuple[int, int, int]] = []

    for item in items:
        # Clamp for placement only -- the DB's width is left alone. An item
        # wider than the grid can never satisfy col + width <= grid_cols,
        # so without this the scan below would never find a free slot.
        width = max(1, min(item["width"], grid_cols))
        if item["width"] > grid_cols:
            logger.warning(
                "Item id=%s width=%s exceeds grid_cols=%s; clamped to %s for compaction only "
                "(stored width unchanged)",
                item["id"], item["width"], grid_cols, width,
            )
        height = max(1, item["height"])

        # Unbounded row scan on purpose: grid_rows is not ours to change and
        # the items' total area may exceed grid_cols * grid_rows. Guaranteed
        # to terminate -- a row past every occupied cell is always free, and
        # the clamped width always fits within grid_cols.
        for row, col in ((r, c) for r in itertools.count() for c in range(grid_cols)):
            if col + width > grid_cols:
                continue
            cells = {(row + dr, col + dc) for dr in range(height) for dc in range(width)}
            if cells & occupied:
                continue
            occupied |= cells
            placements.append((item["id"], row, col))
            break

    return placements


@router.post("/api/workspaces/{workspace_id}/compact")
async def compact_workspace(workspace_id: int, x_agent_token: str | None = Header(None)) -> list[dict]:
    check_agent_token(x_agent_token)

    conn = get_connection()
    try:
        workspace_row = conn.execute(
            "SELECT grid_cols FROM workspace WHERE id = ?", (workspace_id,)
        ).fetchone()
        if workspace_row is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        # No CHECK constraint backs grid_cols, and a 0 would make range(cols)
        # empty -- the placement scan would then spin forever on the first item.
        grid_cols = max(1, workspace_row["grid_cols"])

        # (row, col) is the user's existing visual reading order; id is the
        # tiebreaker, since nothing stops two items sharing a cell today and
        # overlapping items are exactly what this endpoint exists to fix --
        # without it sqlite's order between them is unspecified and repeat
        # calls could shuffle them.
        item_rows = conn.execute(
            # Grid tiles only: the quick-launch bar has no gaps to close.
            "SELECT * FROM item WHERE workspace_id = ? AND dock = 0 ORDER BY row, col, id",
            (workspace_id,),
        ).fetchall()

        placements = _pack_items([dict(row) for row in item_rows], grid_cols)

        try:
            for item_id, row, col in placements:
                conn.execute(
                    "UPDATE item SET row = ?, col = ? WHERE id = ?", (row, col, item_id)
                )
        except sqlite3.IntegrityError as e:
            raise HTTPException(status_code=400, detail=f"invalid layout: {e}")
        conn.commit()

        updated_rows = conn.execute(
            "SELECT * FROM item WHERE workspace_id = ? ORDER BY row, col, id",
            (workspace_id,),
        ).fetchall()
    finally:
        conn.close()

    logger.info("Workspace compacted: id=%s items=%s", workspace_id, len(placements))
    await hub.broadcast_to_clients({"type": "workspace_update"})
    return [dict(row) for row in updated_rows]


@router.post("/api/workspaces")
async def create_workspace(workspace: WorkspaceCreate, x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)

    conn = get_connection()
    try:
        try:
            cur = conn.execute(
                """
                INSERT INTO workspace (name, position, grid_cols, grid_rows)
                VALUES (
                    ?,
                    (SELECT COALESCE(MAX(position), -1) + 1 FROM workspace),
                    ?, ?
                )
                """,
                (workspace.name, workspace.grid_cols, workspace.grid_rows),
            )
        except sqlite3.IntegrityError as e:
            raise HTTPException(status_code=400, detail=f"invalid workspace: {e}")
        conn.commit()
        new_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, name, position, grid_cols, grid_rows FROM workspace WHERE id = ?",
            (new_id,),
        ).fetchone()
    finally:
        conn.close()

    logger.info("Workspace created: id=%s name=%s", new_id, workspace.name)
    await hub.broadcast_to_clients({"type": "workspace_update"})
    return dict(row)


# ── Export / import ──────────────────────────────────────────────────────────
# A deck as a file: a backup, something to share, or a template (Studio's
# "New deck from template" imports frontend/templates/*.json). Positions,
# looks and settings travel; ids, press counts and timestamps don't -- they
# belong to the install the deck came from.

DECK_FORMAT = "itdeck-deck"
DECK_VERSION = 1
# The columns a deck file carries per tile, in the order they are written.
DECK_ITEM_FIELDS = (
    "row", "col", "width", "height", "label", "icon", "color",
    "kind", "type", "target", "params", "state_key", "dock",
)


class DeckItem(BaseModel):
    row: int
    col: int
    width: int = 1
    height: int = 1
    label: str = Field(min_length=1, max_length=80)
    icon: str | None = None
    color: str | None = "#2a2f38"
    kind: str
    type: str
    target: str = "windows"
    params: dict = {}
    state_key: str | None = None
    dock: bool = False


class DeckFile(BaseModel):
    format: Literal["itdeck-deck"]
    version: Literal[1]
    name: str = Field(min_length=1, max_length=60)
    grid_cols: int = Field(ge=1, le=8)
    grid_rows: int = Field(ge=1, le=12)
    items: list[DeckItem] = Field(max_length=100)


def _free_name(conn: sqlite3.Connection, name: str) -> str:
    # "Home" beside an existing "Home" becomes "Home (2)": importing a backup
    # must never be mistaken for, or silently merge into, the deck it saved.
    taken = {row["name"] for row in conn.execute("SELECT name FROM workspace")}
    if name not in taken:
        return name
    for n in itertools.count(2):
        candidate = f"{name} ({n})"
        if candidate not in taken:
            return candidate


@router.get("/api/workspaces/{workspace_id}/export")
def export_workspace(workspace_id: int, x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)
    conn = get_connection()
    try:
        workspace = conn.execute(
            "SELECT name, grid_cols, grid_rows FROM workspace WHERE id = ?", (workspace_id,)
        ).fetchone()
        if workspace is None:
            raise HTTPException(status_code=404, detail="workspace not found")
        rows = conn.execute(
            "SELECT * FROM item WHERE workspace_id = ? ORDER BY dock, row, col, id", (workspace_id,)
        ).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        item = {key: row[key] for key in DECK_ITEM_FIELDS}
        # Stored as a JSON string; a file reads better with the object.
        try:
            item["params"] = json.loads(item["params"] or "{}")
        except ValueError:
            item["params"] = {}
        item["dock"] = bool(item["dock"])
        items.append(item)
    return {
        "format": DECK_FORMAT,
        "version": DECK_VERSION,
        "name": workspace["name"],
        "grid_cols": workspace["grid_cols"],
        "grid_rows": workspace["grid_rows"],
        "items": items,
    }


@router.post("/api/workspaces/import")
async def import_workspace(deck: DeckFile, x_agent_token: str | None = Header(None)) -> dict:
    """Create a NEW deck from a deck file. Never touches an existing one.

    One transaction: every tile is validated by the same rules as a tile
    saved in Studio (params a JSON object, inside the grid, no overlaps, dock
    rules) against the tiles already placed from the same file, and any
    failure leaves no half-imported deck behind.
    """
    check_agent_token(x_agent_token)
    conn = get_connection()
    try:
        name = _free_name(conn, deck.name)
        cur = conn.execute(
            """
            INSERT INTO workspace (name, position, grid_cols, grid_rows)
            VALUES (?, (SELECT COALESCE(MAX(position), -1) + 1 FROM workspace), ?, ?)
            """,
            (name, deck.grid_cols, deck.grid_rows),
        )
        workspace_id = cur.lastrowid
        for index, item in enumerate(deck.items, start=1):
            params = json.dumps(item.params)
            try:
                validate_params_json(params)
                validate_placement(
                    conn, workspace_id, item.row, item.col, item.width, item.height,
                    dock=item.dock, kind=item.kind,
                )
            except HTTPException as exc:
                raise HTTPException(status_code=400, detail=f"tile {index} ({item.label}): {exc.detail}")
            conn.execute(
                """
                INSERT INTO item (workspace_id, row, col, width, height, label, icon, color,
                                  kind, type, target, params, state_key, dock)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id, item.row, item.col, item.width, item.height, item.label,
                    item.icon, item.color or "#2a2f38", item.kind, item.type, item.target,
                    params, item.state_key, int(item.dock),
                ),
            )
        conn.commit()
    finally:
        # Closing without commit() rolls the whole import back.
        conn.close()

    logger.info("Deck imported: id=%s name=%s tiles=%s", workspace_id, name, len(deck.items))
    await hub.broadcast_to_clients({"type": "workspace_update"})
    return {"id": workspace_id, "name": name, "items": len(deck.items)}
