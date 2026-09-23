import itertools
import logging
import os
import sqlite3

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.db import get_connection
from app.ws.hub import hub

logger = logging.getLogger("controlhub.api")

router = APIRouter()


class WorkspaceCreate(BaseModel):
    name: str
    grid_cols: int = 3
    grid_rows: int = 5


def _check_agent_token(x_agent_token: str | None) -> None:
    # Same shared-secret gate as backend/app/api/items.py, applied here too:
    # this surface controls what workspaces exist, which drives what the
    # agent will execute, so it gets the same gate as the item catalog.
    expected_token = os.environ.get("AGENT_TOKEN")
    # "not expected_token" guards against AGENT_TOKEN being unset entirely:
    # without it, a missing env var (None) would equal a missing header
    # (None) and silently let an unauthenticated request through.
    if not expected_token or x_agent_token != expected_token:
        raise HTTPException(status_code=401, detail="missing or invalid X-Agent-Token")


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
    _check_agent_token(x_agent_token)

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
    _check_agent_token(x_agent_token)

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
