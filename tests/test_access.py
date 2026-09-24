"""Changing the tokens from Studio (PUT /api/access)."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app

CONFIG = """# IT-Deck standalone config -- a comment a person wrote
AGENT_TOKEN=test-token
CLIENT_TOKEN=test-client
SERVER_PORT=49732

UPDATE_CHECK=0
VPN_PATH=C:\\VPN\\vpn.exe
"""


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "config.env"
    path.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("ITDECK_CONFIG_FILE", str(path))
    # monkeypatch restores both tokens after the test, whatever it changed.
    monkeypatch.setenv("AGENT_TOKEN", "test-token")
    monkeypatch.setenv("CLIENT_TOKEN", "test-client")
    return path


def headers(token="test-token"):
    return {"X-Agent-Token": token}


def test_access_needs_the_studio_token(client, config):
    assert client.get("/api/access").status_code == 401
    assert client.put("/api/access", json={"client_token": "abcd"}).status_code == 401


def test_read_only_without_a_config_file(client, monkeypatch):
    monkeypatch.delenv("ITDECK_CONFIG_FILE", raising=False)
    assert client.get("/api/access", headers=headers()).json()["editable"] is False
    response = client.put("/api/access", json={"client_token": "abcd"}, headers=headers())
    assert response.status_code == 409


@pytest.mark.parametrize("bad", [
    "abc", "has space", "new\nline", "trailing\n", "x" * 65, "a#b", "a=b", "émoji",
    "0123456789abcdef0123456789abcdef",  # the old auto-generated shape
])
def test_invalid_tokens_are_refused(client, config, bad):
    response = client.put("/api/access", json={"client_token": bad}, headers=headers())
    assert response.status_code == 400
    assert "test-client" in config.read_text()


def test_changing_tokens_rewrites_only_their_lines_and_applies_at_once(client, config):
    info = client.get("/api/access", headers=headers()).json()
    assert info == {"editable": True, "client_token": "test-client"}

    response = client.put(
        "/api/access", json={"client_token": "482913", "agent_token": "Studio-99"}, headers=headers()
    )
    assert response.status_code == 200, response.text
    assert response.json()["changed"] == ["AGENT_TOKEN", "CLIENT_TOKEN"]

    text = config.read_text()
    assert "AGENT_TOKEN=Studio-99" in text and "CLIENT_TOKEN=482913" in text
    # Everything else a person wrote is still there, untouched.
    for line in ("# IT-Deck standalone config -- a comment a person wrote", "UPDATE_CHECK=0",
                 "VPN_PATH=C:\\VPN\\vpn.exe", "SERVER_PORT=49732"):
        assert line in text

    # No restart: the old Studio token is refused, the new one works.
    assert client.get("/api/access", headers=headers()).status_code == 401
    assert client.get("/api/access", headers=headers("Studio-99")).json()["client_token"] == "482913"


def test_a_new_phone_token_disconnects_phones(client, config):
    with client.websocket_connect("/ws/client") as phone:
        phone.send_json({"type": "hello", "token": "test-client"})
        assert phone.receive_json()["type"] == "state"
        response = client.put("/api/access", json={"client_token": "new-phone"}, headers=headers())
        assert response.status_code == 200
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                phone.receive_json()
    assert closed.value.code == 4001

    # And only the new one gets in.
    with client.websocket_connect("/ws/client") as phone:
        phone.send_json({"type": "hello", "token": "new-phone"})
        assert phone.receive_json()["type"] == "state"


def test_a_key_written_twice_is_changed_everywhere(tmp_path):
    from app.config_file import update_config

    path = tmp_path / "config.env"
    path.write_text("AGENT_TOKEN=old\n# note\nAGENT_TOKEN=older\nSERVER_PORT=1\n")
    update_config(path, {"AGENT_TOKEN": "new1", "CLIENT_TOKEN": "added"})
    assert path.read_text() == "AGENT_TOKEN=new1\n# note\nAGENT_TOKEN=new1\nSERVER_PORT=1\nCLIENT_TOKEN=added\n"
