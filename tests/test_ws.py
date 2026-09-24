"""The two WebSocket endpoints: handshakes, command routing, bad input, and
the agent-reconnect race."""

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import pending
from app.main import app
from app.ws.hub import hub

AGENT_HELLO = {"type": "hello", "agent": "windows", "version": "test", "token": "test-token"}
CLIENT_HELLO = {"type": "hello", "token": "test-client"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def action_item_id(client) -> int:
    items = client.get("/api/workspaces").json()[0]["items"]
    return next(i["id"] for i in items if i["kind"] == "action" and i["target"] == "windows")


def recv_until(ws, predicate, limit=20):
    for _ in range(limit):
        message = ws.receive_json()
        if predicate(message):
            return message
    raise AssertionError("expected frame never arrived")


def open_client(client):
    ws = client.websocket_connect("/ws/client").__enter__()
    ws.send_json(CLIENT_HELLO)
    # The snapshot is always the first frame after a good hello.
    assert ws.receive_json()["type"] == "state"
    return ws


def test_client_bad_token_is_refused(client):
    with client.websocket_connect("/ws/client") as ws:
        ws.send_json({"type": "hello", "token": "wrong"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4001


def test_agent_bad_token_is_refused(client):
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_json({**AGENT_HELLO, "token": "wrong"})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 4001
    assert "windows" not in hub.agents


def test_bad_frames_and_unknown_commands_keep_the_socket(client):
    ws = open_client(client)
    try:
        ws.send_text("not json at all")
        ws.send_json(["a", "list"])
        ws.send_json({"cmd": "bogus", "req_id": "r-unknown"})
        reply = recv_until(ws, lambda m: m.get("req_id") == "r-unknown")
        assert reply["status"] == "error" and "unknown command" in reply["message"]
    finally:
        ws.__exit__(None, None, None)


def test_execute_without_an_agent_is_answered_at_once(client):
    assert "windows" not in hub.agents
    ws = open_client(client)
    try:
        ws.send_json({"cmd": "execute", "item_id": action_item_id(client), "req_id": "r-offline"})
        reply = recv_until(ws, lambda m: m.get("req_id") == "r-offline")
        assert reply["message"] == "agent offline"

        ws.send_json({"cmd": "execute", "item_id": "7; drop", "req_id": "r-junk-id"})
        reply = recv_until(ws, lambda m: m.get("req_id") == "r-junk-id")
        assert reply["message"] == "item not found"
    finally:
        ws.__exit__(None, None, None)


def test_only_force_stop_may_override_the_command(client):
    ws = open_client(client)
    try:
        ws.send_json({
            "cmd": "execute", "item_id": action_item_id(client),
            "req_id": "r-override", "override_type": "agent_shutdown",
        })
        reply = recv_until(ws, lambda m: m.get("req_id") == "r-override")
        assert reply["message"] == "command not allowed"
    finally:
        ws.__exit__(None, None, None)


def test_agent_state_is_namespaced_and_broadcast(client):
    key = f"test.{uuid.uuid4().hex[:8]}"
    ws = open_client(client)
    try:
        with client.websocket_connect("/ws/agent") as agent:
            agent.send_json(AGENT_HELLO)
            agent.send_text("{broken")  # ignored, not fatal
            agent.send_json({"type": "state", "data": "not a dict"})  # ignored too
            agent.send_json({"type": "state", "data": {key: 42}})
            frame = recv_until(ws, lambda m: m["type"] == "state")
            assert frame["data"] == {f"windows:{key}": 42}
    finally:
        ws.__exit__(None, None, None)


def test_a_reconnecting_agent_is_not_reported_offline(client):
    item_id = action_item_id(client)
    ws = open_client(client)
    try:
        old = client.websocket_connect("/ws/agent").__enter__()
        old.send_json(AGENT_HELLO)
        recv_until(ws, lambda m: m.get("type") == "agent_status" and m["status"] == "online")

        # The restarted agent arrives while the old socket is still open --
        # a killed process leaves a half-open connection behind.
        with client.websocket_connect("/ws/agent") as new:
            new.send_json(AGENT_HELLO)
            recv_until(ws, lambda m: m.get("type") == "agent_status" and m["status"] == "online")
            # The old socket's cleanup runs now, after the new one registered.
            old.__exit__(None, None, None)
            # Give that cleanup time to run. Without the fix it removes the
            # *new* socket, and this fails here rather than hanging below.
            time.sleep(0.3)
            assert "windows" in hub.agents

            # A press still reaches the new agent...
            ws.send_json({"cmd": "execute", "item_id": item_id, "req_id": "r-race"})
            command = new.receive_json()
            assert command["req_id"] == "r-race"
            new.send_json({"type": "result", "req_id": "r-race", "item_id": item_id, "status": "ok"})

            # ...and nothing claimed it went offline on the way.
            seen = []
            recv_until(ws, lambda m: seen.append(m) or m.get("req_id") == "r-race")
            assert not any(m.get("type") == "agent_status" and m["status"] == "offline" for m in seen)
            assert seen[-1]["status"] == "ok"

        # The real disconnect is still reported.
        offline = recv_until(ws, lambda m: m.get("type") == "agent_status")
        assert offline["status"] == "offline"
        assert "windows" not in hub.agents
    finally:
        ws.__exit__(None, None, None)


def test_a_repeated_req_id_does_not_time_out_the_newer_command():
    fired = []

    async def on_timeout(req_id):
        fired.append(req_id)

    async def scenario():
        pending.track("dup", 0.05, on_timeout)
        pending.track("dup", 0.3, on_timeout)  # replaces the first timer
        await asyncio.sleep(0.1)
        assert fired == []  # the first timer must not have fired for the second
        pending.resolve("dup")
        await asyncio.sleep(0.3)
        assert fired == []
        pending.track(None, 0.01, on_timeout)  # no req_id, nothing tracked
        await asyncio.sleep(0.05)
        assert fired == []

    asyncio.run(scenario())
