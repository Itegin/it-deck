"""Backend API: the quick-launch bar's placement rules, the Studio queries'
auth, and how the frontend files are served."""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

TOKEN = {"X-Agent-Token": "test-token"}


@pytest.fixture(scope="module")
def client():
    # The context manager runs the startup hook: init_db, seed, fixups.
    with TestClient(app) as test_client:
        yield test_client


def workspace(client):
    return client.get("/api/workspaces").json()[0]


def dock_item(ws_id, col, **overrides):
    body = {
        "workspace_id": ws_id, "row": 0, "col": col, "width": 1, "height": 1,
        "label": f"Dock {col}", "icon": "globe", "kind": "action", "type": "open_url",
        "params": json.dumps({"url": "https://example.com/"}), "dock": True,
    }
    body.update(overrides)
    return body


def test_seeded_items_are_in_the_grid(client):
    items = workspace(client)["items"]
    assert items, "the seed should create tiles"
    assert all(item["dock"] == 0 for item in items)


def test_dock_placement_rules(client):
    ws_id = workspace(client)["id"]
    first = client.post("/api/items", json=dock_item(ws_id, 0), headers=TOKEN)
    assert first.status_code == 200, first.text
    assert first.json()["dock"] == 1

    taken = client.post("/api/items", json=dock_item(ws_id, 0, label="Twin"), headers=TOKEN)
    assert taken.status_code == 400 and "taken" in taken.json()["detail"]

    past_end = client.post("/api/items", json=dock_item(ws_id, 7), headers=TOKEN)
    assert past_end.status_code == 400

    wide = client.post("/api/items", json=dock_item(ws_id, 1, width=2), headers=TOKEN)
    assert wide.status_code == 400

    widget = client.post(
        "/api/items", json=dock_item(ws_id, 1, kind="widget", type="clock_weather"), headers=TOKEN
    )
    assert widget.status_code == 400


def test_dock_items_do_not_block_grid_cells(client):
    ws = workspace(client)
    taken = {(i["row"], i["col"]) for i in ws["items"] if not i["dock"]}
    row, col = next(
        (r, c) for r in range(ws["grid_rows"]) for c in range(ws["grid_cols"]) if (r, c) not in taken
    )
    # A bar item at row 0 and some col says nothing about the grid cell there.
    used = {i["col"] for i in ws["items"] if i["dock"]}
    slot = next(p for p in range(7) if p not in used)
    assert client.post("/api/items", json=dock_item(ws["id"], slot, label="Shadow"), headers=TOKEN).status_code == 200
    grid_tile = dock_item(ws["id"], col, row=row, dock=False, label="Grid tile")
    response = client.post("/api/items", json=grid_tile, headers=TOKEN)
    assert response.status_code == 200, response.text


def test_compact_leaves_the_dock_alone(client):
    ws = workspace(client)
    before = {i["id"]: (i["row"], i["col"]) for i in ws["items"] if i["dock"]}
    assert client.post(f"/api/workspaces/{ws['id']}/compact", headers=TOKEN).status_code == 200
    after = {i["id"]: (i["row"], i["col"]) for i in workspace(client)["items"] if i["dock"]}
    assert before == after


def test_moving_a_tile_into_the_dock(client):
    ws = workspace(client)
    tile = next(i for i in ws["items"] if not i["dock"] and i["kind"] == "action" and i["width"] == 1)
    used = {i["col"] for i in ws["items"] if i["dock"]}
    slot = next(p for p in range(7) if p not in used)
    response = client.put(
        f"/api/items/{tile['id']}", json={"dock": True, "row": 0, "col": slot}, headers=TOKEN
    )
    assert response.status_code == 200, response.text
    assert response.json()["dock"] == 1


@pytest.mark.parametrize("query", ["list_apps", "list_devices"])
def test_agent_queries_need_the_token(client, query):
    assert client.post(f"/api/agents/windows/{query}").status_code == 401
    # With the token but no agent connected: a definite "offline", not a hang.
    assert client.post(f"/api/agents/windows/{query}", headers=TOKEN).status_code == 404


def test_fetch_icon_needs_the_token(client):
    body = {"url": "https://example.com"}
    assert client.post("/api/agents/windows/fetch_icon", json=body).status_code == 401
    assert client.post("/api/agents/windows/fetch_icon", json=body, headers=TOKEN).status_code == 404


def test_frontend_is_served_revalidated(client):
    response = client.get("/js/app.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_guide_pictures_are_webp(client):
    response = client.get("/img/guide/en/add-tile.webp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


def test_explicit_null_placement_is_a_400_not_a_500(client):
    tile = next(i for i in workspace(client)["items"] if i["kind"] == "action")
    response = client.put(f"/api/items/{tile['id']}", json={"width": None}, headers=TOKEN)
    assert response.status_code == 400, response.text
    assert "width" in response.json()["detail"]
    # The nullable ones can still be cleared.
    cleared = client.put(f"/api/items/{tile['id']}", json={"icon": None}, headers=TOKEN)
    assert cleared.status_code == 200, cleared.text


@pytest.mark.parametrize("params", ["[1]", "3", '"text"', "null"])
def test_params_must_be_a_json_object(client, params):
    tile = next(i for i in workspace(client)["items"] if i["kind"] == "action")
    response = client.put(f"/api/items/{tile['id']}", json={"params": params}, headers=TOKEN)
    assert response.status_code == 400


def test_item_writes_need_the_token(client):
    tile = workspace(client)["items"][0]
    assert client.put(f"/api/items/{tile['id']}", json={"label": "x"}).status_code == 401
    assert client.put(
        f"/api/items/{tile['id']}", json={"label": "x"}, headers={"X-Agent-Token": "test-tokeN"}
    ).status_code == 401
    assert client.delete(f"/api/items/{tile['id']}").status_code == 401


def test_access_log_masks_the_client_token():
    import logging

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", "/?token=s3cret&x=1", "1.1", 200), None,
    )
    for log_filter in logging.getLogger("uvicorn.access").filters:
        log_filter.filter(record)
    line = record.getMessage()
    assert "s3cret" not in line and "token=***&x=1" in line


def test_access_log_drops_successful_health_probes():
    import logging

    def passes(status):
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 0,
            '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", "/health", "1.1", status), None,
        )
        return all(f.filter(record) for f in logging.getLogger("uvicorn.access").filters)

    assert not passes(200)
    assert passes(500)
