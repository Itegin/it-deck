from typing import Optional

from app.db import get_connection

# There were pydantic Item and Workspace models here. Nothing used them: every
# endpoint in app/api/ builds and returns plain dicts, and none declares a
# response_model. They described the schema a second time, in a place no code
# read, which is the kind of documentation that goes quietly wrong.


def get_workspaces_with_items() -> list[dict]:
    conn = get_connection()
    try:
        workspace_rows = conn.execute(
            "SELECT * FROM workspace ORDER BY position"
        ).fetchall()
        item_rows = conn.execute(
            "SELECT * FROM item ORDER BY workspace_id, row, col"
        ).fetchall()
    finally:
        conn.close()

    items_by_workspace: dict[int, list[dict]] = {}
    for item_row in item_rows:
        items_by_workspace.setdefault(item_row["workspace_id"], []).append(dict(item_row))

    return [
        {**dict(workspace_row), "items": items_by_workspace.get(workspace_row["id"], [])}
        for workspace_row in workspace_rows
    ]


def get_referenced_agents() -> list[str]:
    """Every agent name a tile points at, whether or not it is connected.

    The hub only knows the agents that are *here*; this is how the other half
    of the question gets answered. A client connecting has to be told which
    of the agents its tiles depend on are missing, and "missing" is by
    definition not in hub.agents -- so the list of what to check comes from
    the item rows instead.
    """
    conn = get_connection()
    try:
        return [
            row["target"]
            for row in conn.execute(
                "SELECT DISTINCT target FROM item WHERE target IS NOT NULL AND target <> ''"
            )
        ]
    finally:
        conn.close()


def get_item(item_id: int) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM item WHERE id = ?", (item_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row is not None else None


def bump_press_count(item_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE item SET press_count = press_count + 1, last_pressed = datetime('now') WHERE id = ?",
            (item_id,),
        )
        conn.commit()
    finally:
        conn.close()
