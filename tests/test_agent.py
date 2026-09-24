"""Agent-side checks that need no network, audio device or COM."""

import asyncio
import json

import pytest

from dispatch import receive_loop, run_handler
from handlers.apps import _is_private_host, handle_fetch_icon
from handlers.process import validate_url


@pytest.mark.parametrize("url", [
    "https://web.telegram.org/a/",
    "http://localhost:8080/",
    "discord://",
    "steam://open/main",
    "ms-settings:sound",
    "https://example.com/?q=1&b=2",
])
def test_open_url_accepts(url):
    assert validate_url(url) is None


@pytest.mark.parametrize("url", [
    "",
    "file:///C:/Windows/System32/calc.exe",
    "javascript:alert(1)",
    "ms-msdt:/id",
    "C:\\Windows\\notepad.exe",
    "http://",
    "https://a b.com",
    'https://example.com/"x',
    "https://example.com/|x",
])
def test_open_url_refuses(url):
    assert validate_url(url) is not None


@pytest.mark.parametrize("host,private", [
    ("127.0.0.1", True),
    ("192.168.1.1", True),
    ("10.0.0.5", True),
    ("169.254.1.1", True),
    ("198.18.0.5", False),  # VPN fake-IP range: public sites resolve here
    ("8.8.8.8", False),
])
def test_lan_addresses_are_refused_for_icon_fetches(host, private):
    assert _is_private_host(host) is private


# --- dispatch: every command with a req_id gets exactly one result ----------


class FakeSocket:
    def __init__(self, frames):
        self.frames = frames
        self.sent = []

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for frame in self.frames:
            yield frame

    async def send(self, text):
        self.sent.append(json.loads(text))


def _boom(params):
    raise RuntimeError("handler bug")


HANDLERS = {
    "ok": lambda params: {"status": "ok", "extra": params.get("x")},
    "boom": _boom,
    "none": lambda params: None,
    "force_stop": lambda params, item_type: {"status": "ok", "item_type": item_type},
}


def test_run_handler_turns_every_failure_into_a_result():
    assert run_handler(HANDLERS, {"cmd": "ok", "params": {"x": 1}}) == {"status": "ok", "extra": 1}
    assert run_handler(HANDLERS, {"cmd": "ok"})["status"] == "ok"  # params missing
    assert "unknown command" in run_handler(HANDLERS, {"cmd": "nope"})["message"]
    assert "handler bug" in run_handler(HANDLERS, {"cmd": "boom", "params": {}})["message"]
    assert run_handler(HANDLERS, {"cmd": "none", "params": {}})["status"] == "error"
    assert run_handler(HANDLERS, {"cmd": "ok", "params": [1]})["status"] == "error"
    assert run_handler(HANDLERS, {"cmd": "force_stop", "params": {}, "item_type": "launch_app"}) == {
        "status": "ok", "item_type": "launch_app",
    }


def test_receive_loop_survives_bad_frames_and_answers_each_command():
    shutdowns = []

    async def on_shutdown():
        shutdowns.append(True)

    ws = FakeSocket([
        "not json",
        "[1, 2]",
        json.dumps({"type": "noise"}),
        json.dumps({"cmd": "boom", "req_id": "a", "item_id": 3, "params": {}}),
        json.dumps({"cmd": "ok", "params": {"x": 2}}),  # no req_id at all
        json.dumps({"cmd": "ok", "req_id": "b", "params": {"x": 5}}),
    ])
    asyncio.run(receive_loop(ws, HANDLERS, on_shutdown))

    assert [m["req_id"] for m in ws.sent] == ["a", None, "b"]
    assert ws.sent[0]["status"] == "error" and ws.sent[0]["item_id"] == 3
    assert ws.sent[2] == {"type": "result", "req_id": "b", "item_id": None, "status": "ok", "extra": 5}
    assert shutdowns == []


def test_fetch_icon_refuses_an_unparseable_url_instead_of_raising():
    assert handle_fetch_icon({"url": "http://[x"})["status"] == "error"
    assert handle_fetch_icon({"url": "ftp://example.com"})["status"] == "error"
