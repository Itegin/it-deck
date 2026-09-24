import json
import logging
import sqlite3

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.auth import check_agent_token
from app.db import get_connection
from app.models import get_item
from app.ws.hub import hub

logger = logging.getLogger("controlhub.api")

router = APIRouter()


class ItemCreate(BaseModel):
    workspace_id: int
    row: int
    col: int
    width: int = 1
    height: int = 1
    label: str
    icon: str | None = None
    color: str = "#2a2f38"
    kind: str
    type: str
    target: str = "windows"
    params: str = "{}"
    state_key: str | None = None
    dock: bool = False


class ItemUpdate(BaseModel):
    workspace_id: int | None = None
    row: int | None = None
    col: int | None = None
    width: int | None = None
    height: int | None = None
    label: str | None = None
    icon: str | None = None
    color: str | None = None
    kind: str | None = None
    type: str | None = None
    target: str | None = None
    params: str | None = None
    state_key: str | None = None
    dock: bool | None = None


# How many buttons the quick-launch bar holds. The bar never scrolls (no
# scroll chain on the deck -- see css/grid.css), so past this the squares
# would shrink below a comfortable finger target on a 375pt-wide phone.
DOCK_MAX = 7

# The ItemUpdate fields a PUT may explicitly set to null.
NULLABLE_FIELDS = frozenset({"icon", "color", "state_key"})



def validate_params_json(params: str) -> None:
    # A malformed params string would crash every future handler that
    # tries to json.loads() it (backend _handle_execute, frontend
    # fetchWorkspaces) -- reject it here, at the one place items are
    # actually written, rather than let it become a landmine for whoever
    # reads this row next.
    #
    # An object specifically, not just any JSON: every reader spreads or
    # indexes it by key ({**params, "value": ...} in ws/client.py,
    # item.params.url in the deck), and "[1]" or "3" parse fine and then
    # break each of those.
    try:
        parsed = json.loads(params)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="params must be a valid JSON string")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="params must be a JSON object")


def validate_placement(
    conn: sqlite3.Connection,
    workspace_id: int,
    row: int,
    col: int,
    width: int,
    height: int,
    item_id: int | None = None,
    dock: bool = False,
    kind: str | None = None,
) -> None:
    # The deck places every tile *explicitly*: render.js writes
    # `grid-row: row+1 / span height` and `grid-column: col+1 / span width`
    # straight from these columns. CSS Grid does not push explicitly-placed
    # items out of each other's way -- it stacks them -- so two rows whose
    # rectangles intersect render as one tile painted on top of another, with
    # the covered tile's taps going to whichever is on top. Reproduced
    # directly: giving the 2-wide Volume slider height=2 buried the VPN and
    # Screenshot tiles under it, identically in all four themes.
    #
    # Nothing checked this before, so Studio could write it and only the deck
    # would ever show it. Checked here rather than in the frontend because
    # this is the one place items are actually written -- the same reasoning
    # validate_params_json above already states.
    if dock:
        _validate_dock_placement(conn, workspace_id, row, col, width, height, item_id, kind)
        return

    if width < 1 or height < 1:
        raise HTTPException(status_code=400, detail="width and height must be at least 1")
    if row < 0 or col < 0:
        raise HTTPException(status_code=400, detail="row and col must not be negative")

    workspace = conn.execute(
        "SELECT grid_cols, grid_rows FROM workspace WHERE id = ?", (workspace_id,)
    ).fetchone()
    if workspace is None:
        # Reached before the INSERT's own foreign-key error would be, so the
        # message names the actual problem instead of surfacing sqlite's.
        raise HTTPException(status_code=400, detail=f"workspace {workspace_id} does not exist")

    # Out of bounds is its own failure, not a harmless overhang: an item past
    # grid_rows lands in an implicit grid track, which sizes to `auto` and --
    # because .tile is container-type: size -- computes to 0px. css/grid.css
    # now sizes implicit tracks so that degrades to an extra row rather than a
    # pile of zero-height tiles, but a placement the workspace can't hold is
    # still a mistake worth refusing at the point it's made.
    if row + height > workspace["grid_rows"] or col + width > workspace["grid_cols"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"placement ({col},{row}) {width}x{height} falls outside this "
                f"workspace's {workspace['grid_cols']}x{workspace['grid_rows']} grid"
            ),
        )

    # Grid tiles only: a quick-launch bar item keeps row 0 and a position in
    # `col` that says nothing about the grid.
    existing = conn.execute(
        "SELECT id, label, row, col, width, height FROM item WHERE workspace_id = ? AND dock = 0",
        (workspace_id,),
    ).fetchall()

    for other in existing:
        # An update re-checks the item against its neighbours, never against
        # the row it is replacing.
        if item_id is not None and other["id"] == item_id:
            continue
        # Standard rectangle intersection: they overlap unless one ends before
        # the other begins on at least one axis.
        if (
            col < other["col"] + other["width"]
            and other["col"] < col + width
            and row < other["row"] + other["height"]
            and other["row"] < row + height
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"placement ({col},{row}) {width}x{height} overlaps "
                    f"'{other['label']}' at ({other['col']},{other['row']}) "
                    f"{other['width']}x{other['height']}"
                ),
            )


def _validate_dock_placement(
    conn: sqlite3.Connection,
    workspace_id: int,
    row: int,
    col: int,
    width: int,
    height: int,
    item_id: int | None,
    kind: str | None,
) -> None:
    # The bar is a strip of square buttons, so: an action, 1x1, row 0, and
    # `col` is a slot 0..DOCK_MAX-1 that no other bar item holds.
    if kind != "action":
        raise HTTPException(status_code=400, detail="only action tiles can go in the quick-launch bar")
    if width != 1 or height != 1 or row != 0:
        raise HTTPException(status_code=400, detail="a quick-launch bar item is 1x1 at row 0")
    if not 0 <= col < DOCK_MAX:
        raise HTTPException(status_code=400, detail=f"the quick-launch bar has {DOCK_MAX} places (0-{DOCK_MAX - 1})")
    if conn.execute(
        "SELECT 1 FROM workspace WHERE id = ?", (workspace_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=400, detail=f"workspace {workspace_id} does not exist")
    other = conn.execute(
        "SELECT label FROM item WHERE workspace_id = ? AND dock = 1 AND col = ? AND id IS NOT ?",
        (workspace_id, col, item_id),
    ).fetchone()
    if other is not None:
        raise HTTPException(
            status_code=400,
            detail=f"place {col + 1} of the quick-launch bar is taken by '{other['label']}'",
        )


@router.get("/api/items/{item_id}")
def get_item_endpoint(item_id: int, x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)
    item = get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")
    return item


@router.post("/api/items")
async def create_item(item: ItemCreate, x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)
    validate_params_json(item.params)

    conn = get_connection()
    try:
        validate_placement(
            conn, item.workspace_id, item.row, item.col, item.width, item.height,
            dock=item.dock, kind=item.kind,
        )
        try:
            cur = conn.execute(
                """
                INSERT INTO item
                    (workspace_id, row, col, width, height, label, icon, color, kind, type, target, params, state_key, dock)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.workspace_id, item.row, item.col, item.width, item.height,
                    item.label, item.icon, item.color, item.kind, item.type,
                    item.target, item.params, item.state_key, int(item.dock),
                ),
            )
        except sqlite3.IntegrityError as e:
            # Most likely cause: workspace_id doesn't reference a real
            # workspace row (foreign_keys = ON, see app.db.get_connection).
            raise HTTPException(status_code=400, detail=f"invalid item: {e}")
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()

    logger.info("Item created: id=%s label=%s", new_id, item.label)
    await hub.broadcast_to_clients({"type": "workspace_update"})
    return get_item(new_id)


@router.put("/api/items/{item_id}")
async def update_item(item_id: int, item: ItemUpdate, x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)
    existing = get_item(item_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="item not found")

    # Only fields actually present in the request body -- exclude_unset
    # distinguishes "field omitted" from "field explicitly set to null"
    # (e.g. clearing icon or state_key), which a plain None-check can't.
    fields = item.model_dump(exclude_unset=True)
    # Only icon, color and state_key may be cleared. Every other column is
    # NOT NULL or feeds the placement arithmetic below, where an explicit
    # null used to surface as a 500 (None < 1) instead of a 400.
    nulled = sorted(key for key, value in fields.items() if value is None and key not in NULLABLE_FIELDS)
    if nulled:
        raise HTTPException(status_code=400, detail=f"these fields can't be null: {', '.join(nulled)}")
    if "params" in fields:
        validate_params_json(fields["params"])

    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")

    # Column names come only from ItemUpdate's own declared fields, never
    # from arbitrary request data, so building the SET clause from these
    # keys is not a SQL-injection surface; values are still parameterized.
    set_clause = ", ".join(f"{column} = ?" for column in fields)
    values = [*fields.values(), item_id]

    conn = get_connection()
    try:
        # Merged against the stored row, not read from `fields` alone: a PUT
        # that moves an item one column over sends `col` and nothing else, so
        # the other three sides of the rectangle have to come from the row as
        # it currently stands or the check would be against a phantom 0x0.
        placement = {key: existing[key] for key in ("workspace_id", "row", "col", "width", "height", "dock", "kind")}
        placement.update({key: fields[key] for key in placement if key in fields})
        placement["dock"] = bool(placement["dock"])
        validate_placement(conn, item_id=item_id, **placement)

        try:
            conn.execute(f"UPDATE item SET {set_clause} WHERE id = ?", values)
        except sqlite3.IntegrityError as e:
            raise HTTPException(status_code=400, detail=f"invalid item: {e}")
        conn.commit()
    finally:
        conn.close()

    logger.info("Item updated: id=%s fields=%s", item_id, list(fields))
    await hub.broadcast_to_clients({"type": "workspace_update"})
    return get_item(item_id)


@router.delete("/api/items/{item_id}")
async def delete_item(item_id: int, x_agent_token: str | None = Header(None)) -> dict:
    check_agent_token(x_agent_token)
    if get_item(item_id) is None:
        raise HTTPException(status_code=404, detail="item not found")

    conn = get_connection()
    try:
        conn.execute("DELETE FROM item WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()

    logger.info("Item deleted: id=%s", item_id)
    await hub.broadcast_to_clients({"type": "workspace_update"})
    return {"status": "ok"}
