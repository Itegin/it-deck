"""Deck files: export, import, and the templates Studio offers."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

TOKEN = {"X-Agent-Token": "test-token"}
TEMPLATES = sorted((Path(__file__).resolve().parent.parent / "frontend" / "templates").glob("*.json"))


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def first_deck(client):
    return client.get("/api/workspaces").json()[0]


def test_export_needs_the_token(client):
    assert client.get(f"/api/workspaces/{first_deck(client)['id']}/export").status_code == 401


def test_export_then_import_makes_an_equal_new_deck(client):
    original = first_deck(client)
    exported = client.get(f"/api/workspaces/{original['id']}/export", headers=TOKEN).json()
    assert exported["format"] == "itdeck-deck" and exported["version"] == 1
    assert all(isinstance(item["params"], dict) for item in exported["items"])
    assert not any("id" in item or "press_count" in item for item in exported["items"])

    response = client.post("/api/workspaces/import", json=exported, headers=TOKEN)
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["id"] != original["id"]
    assert created["name"] == f"{original['name']} (2)"  # never merged into the original

    copy = next(w for w in client.get("/api/workspaces").json() if w["id"] == created["id"])
    shape = lambda deck: sorted((i["label"], i["row"], i["col"], i["type"], i["dock"]) for i in deck["items"])  # noqa: E731
    assert shape(copy) == shape(original)
    # The original deck is exactly as it was.
    assert shape(first_deck(client)) == shape(original)


@pytest.mark.parametrize("change", [{"format": "something-else"}, {"version": 2}, {"grid_cols": 0}])
def test_import_refuses_files_it_does_not_understand(client, change):
    body = {"format": "itdeck-deck", "version": 1, "name": "Bad", "grid_cols": 3, "grid_rows": 5, "items": []}
    body.update(change)
    assert client.post("/api/workspaces/import", json=body, headers=TOKEN).status_code == 422


def test_a_bad_tile_imports_nothing(client):
    before = len(client.get("/api/workspaces").json())
    tile = {"row": 0, "col": 0, "label": "A", "kind": "action", "type": "screenshot"}
    body = {
        "format": "itdeck-deck", "version": 1, "name": "Overlap", "grid_cols": 3, "grid_rows": 5,
        "items": [tile, {**tile, "label": "B"}],  # both in the same cell
    }
    response = client.post("/api/workspaces/import", json=body, headers=TOKEN)
    assert response.status_code == 400 and "tile 2 (B)" in response.json()["detail"]
    assert len(client.get("/api/workspaces").json()) == before  # rolled back whole


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_every_template_imports_cleanly(client, path):
    deck = json.loads(path.read_text(encoding="utf-8"))
    response = client.post("/api/workspaces/import", json=deck, headers=TOKEN)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == len(deck["items"])


# ── Managing decks: rename, reorder, delete, and the swipe setting ──────────


def make_deck(client, name, tiles=0):
    items = [
        {"row": 0, "col": col, "label": f"T{col}", "kind": "action", "type": "screenshot"}
        for col in range(tiles)
    ]
    body = {"format": "itdeck-deck", "version": 1, "name": name, "grid_cols": 3, "grid_rows": 5, "items": items}
    response = client.post("/api/workspaces/import", json=body, headers=TOKEN)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def deck_ids(client):
    return [w["id"] for w in client.get("/api/workspaces").json()]


def test_deck_changes_need_the_token(client):
    deck = first_deck(client)["id"]
    assert client.delete(f"/api/workspaces/{deck}").status_code == 401
    assert client.patch(f"/api/workspaces/{deck}", json={"name": "X"}).status_code == 401
    assert client.put("/api/workspaces/order", json={"ids": deck_ids(client)}).status_code == 401


def test_delete_takes_the_tiles_with_it(client):
    from app.db import get_connection

    deck = make_deck(client, "Doomed", tiles=2)
    response = client.delete(f"/api/workspaces/{deck}", headers=TOKEN)
    assert response.status_code == 200 and response.json()["items"] == 2
    assert deck not in deck_ids(client)
    conn = get_connection()
    try:
        orphans = conn.execute("SELECT COUNT(*) FROM item WHERE workspace_id = ?", (deck,)).fetchone()[0]
    finally:
        conn.close()
    assert orphans == 0
    assert client.delete(f"/api/workspaces/{deck}", headers=TOKEN).status_code == 404


def test_the_last_deck_cannot_be_deleted(client):
    keep = first_deck(client)["id"]
    for deck in deck_ids(client):
        if deck != keep:
            assert client.delete(f"/api/workspaces/{deck}", headers=TOKEN).status_code == 200
    assert client.delete(f"/api/workspaces/{keep}", headers=TOKEN).status_code == 409
    assert deck_ids(client) == [keep]


def test_rename(client):
    deck = make_deck(client, "Old name")
    other = make_deck(client, "Taken")
    response = client.patch(f"/api/workspaces/{deck}", json={"name": "  New name  "}, headers=TOKEN)
    assert response.status_code == 200 and response.json()["name"] == "New name"
    assert next(w for w in client.get("/api/workspaces").json() if w["id"] == deck)["name"] == "New name"
    # Its own name again is no clash; another deck's is.
    assert client.patch(f"/api/workspaces/{deck}", json={"name": "New name"}, headers=TOKEN).status_code == 200
    assert client.patch(f"/api/workspaces/{deck}", json={"name": "Taken"}, headers=TOKEN).status_code == 409
    assert client.patch(f"/api/workspaces/{deck}", json={"name": "   "}, headers=TOKEN).status_code == 422
    assert client.patch("/api/workspaces/999999", json={"name": "X"}, headers=TOKEN).status_code == 404
    for gone in (deck, other):
        client.delete(f"/api/workspaces/{gone}", headers=TOKEN)


def test_reorder(client):
    added = [make_deck(client, "B"), make_deck(client, "C")]
    ids = deck_ids(client)
    reversed_ids = list(reversed(ids))
    assert client.put("/api/workspaces/order", json={"ids": reversed_ids}, headers=TOKEN).status_code == 200
    assert deck_ids(client) == reversed_ids
    # Anything but every deck exactly once is refused, and changes nothing.
    for bad in (ids[:-1], ids + [ids[0]], ids[:-1] + [999999]):
        assert client.put("/api/workspaces/order", json={"ids": bad}, headers=TOKEN).status_code == 409
    assert deck_ids(client) == reversed_ids
    client.put("/api/workspaces/order", json={"ids": ids}, headers=TOKEN)
    for gone in added:
        client.delete(f"/api/workspaces/{gone}", headers=TOKEN)


def test_create_numbers_a_repeated_name_and_bounds_the_grid(client):
    first = client.post("/api/workspaces", json={"name": "New deck"}, headers=TOKEN).json()
    second = client.post("/api/workspaces", json={"name": "New deck"}, headers=TOKEN).json()
    assert (first["name"], second["name"]) == ("New deck", "New deck (2)")
    assert client.post("/api/workspaces", json={"name": "Z", "grid_cols": 0}, headers=TOKEN).status_code == 422
    for gone in (first["id"], second["id"]):
        client.delete(f"/api/workspaces/{gone}", headers=TOKEN)


def test_deck_swipe_setting(client):
    assert client.get("/api/settings").json()["deck_swipe"] is True  # on until turned off
    assert client.put("/api/settings/deck-swipe", json={"enabled": False}).status_code == 401
    response = client.put("/api/settings/deck-swipe", json={"enabled": False}, headers=TOKEN)
    assert response.status_code == 200 and response.json() == {"deck_swipe": False}
    assert client.get("/api/settings").json()["deck_swipe"] is False
    client.put("/api/settings/deck-swipe", json={"enabled": True}, headers=TOKEN)
    assert client.get("/api/settings").json()["deck_swipe"] is True
