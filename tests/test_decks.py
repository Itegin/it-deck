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
