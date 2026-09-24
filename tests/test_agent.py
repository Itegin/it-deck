"""Agent-side checks that need no network, audio device or COM."""

import asyncio
import json

import pytest

from dispatch import receive_loop, run_handler
from handlers import process
from handlers.apps import _is_private_host, handle_fetch_icon
from handlers.process import ProcessWatch, validate_url


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
        json.dumps({"req_id": "c"}),  # a req_id with no cmd still gets an answer
    ])
    asyncio.run(receive_loop(ws, HANDLERS, on_shutdown))

    assert [m["req_id"] for m in ws.sent] == ["a", None, "b", "c"]
    assert "unknown command" in ws.sent[3]["message"]
    assert ws.sent[0]["status"] == "error" and ws.sent[0]["item_id"] == 3
    assert ws.sent[2] == {"type": "result", "req_id": "b", "item_id": None, "status": "ok", "extra": 5}
    assert shutdowns == []


def test_fetch_icon_refuses_an_unparseable_url_instead_of_raising():
    assert handle_fetch_icon({"url": "http://[x"})["status"] == "error"
    assert handle_fetch_icon({"url": "ftp://example.com"})["status"] == "error"


def test_receive_loop_hands_watcher_notices_to_the_callback():
    seen = []

    async def on_shutdown():
        pass

    ws = FakeSocket([
        json.dumps({"type": "watchers", "active": False}),
        json.dumps({"type": "watchers", "active": True}),
    ])
    asyncio.run(receive_loop(ws, HANDLERS, on_shutdown, seen.append))
    assert seen == [False, True]
    assert ws.sent == []  # a notice is not a command: nothing to answer


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_process_watch_rescans_rarely_while_the_process_is_absent(monkeypatch):
    scans = []
    running = {"pid": None}
    monkeypatch.setattr(process, "find_process", lambda name: scans.append(name) or running["pid"])
    clock = FakeClock()
    watch = ProcessWatch(rescan_seconds=3, clock=clock)

    assert watch.running("") is False and scans == []  # no name, no walk
    assert watch.running("VPN.exe") is False
    assert watch.running("VPN.exe") is False  # same second: no second walk
    assert scans == ["vpn.exe"]
    clock.now += 3
    assert watch.running("vpn.exe") is False
    assert len(scans) == 2

    # Started by the deck: rescan_soon() makes the very next tick look.
    running["pid"] = 4242
    watch.rescan_soon()

    class Alive:
        def __init__(self, pid):
            assert pid == 4242

        def name(self):
            return "VPN.EXE"

    monkeypatch.setattr(process.psutil, "Process", Alive)
    assert watch.running("vpn.exe") is True
    # Known PID: checked directly, no more walks.
    assert watch.running("vpn.exe") is True
    assert len(scans) == 3


def test_agent_token_is_reread_from_config_env(tmp_path, monkeypatch):
    from config_file import current_token

    monkeypatch.delenv("ITDECK_CONFIG_FILE", raising=False)
    assert current_token("from-env") == "from-env"

    config = tmp_path / "config.env"
    config.write_text("# comment\nCLIENT_TOKEN=phone\nAGENT_TOKEN=changed-in-studio\n")
    monkeypatch.setenv("ITDECK_CONFIG_FILE", str(config))
    assert current_token("from-env") == "changed-in-studio"

    monkeypatch.setenv("ITDECK_CONFIG_FILE", str(tmp_path / "missing.env"))
    assert current_token("from-env") == "from-env"


# --- keyboard, power and clipboard tiles (their Windows calls stubbed) --------


def test_parse_keys_understands_chords_and_names():
    from handlers.input import parse_keys

    assert parse_keys("ctrl+shift+m") == [0x11, 0x10, ord("M")]
    assert parse_keys(" Control + Alt + Del ") == [0x11, 0x12, 0x2E]
    assert parse_keys("win+5") == [0x5B, ord("5")]
    assert parse_keys("f13") == [0x7C]
    assert parse_keys("media_play_pause") == [0xB3]


@pytest.mark.parametrize("bad", ["", "ctrl+", "ctrl+shift", "ctrl+ctrl+a", "hyper+x", "f25", "a+b+c+d+e+f", None])
def test_parse_keys_refuses_what_it_cannot_press(bad):
    from handlers.input import parse_keys

    with pytest.raises(ValueError):
        parse_keys(bad)


def test_key_handlers_send_or_explain(monkeypatch):
    from handlers import input as keys

    sent = []
    monkeypatch.setattr(keys, "_send", sent.append)
    assert keys.handle_send_keys({"keys": "ctrl+shift+m"})["status"] == "ok"
    assert keys.handle_media_key({"key": "next"})["status"] == "ok"
    assert sent == [[0x11, 0x10, ord("M")], [0xB0]]
    assert "Unknown key" in keys.handle_send_keys({"keys": "ctrl+nope"})["message"]
    assert keys.handle_media_key({"key": "rewind"})["status"] == "error"


def test_power_answers_first_and_acts_after(monkeypatch):
    from handlers import power

    scheduled = []
    monkeypatch.setattr(power, "_schedule", scheduled.append)
    assert power.handle_power({"action": "shutdown"}) == {"status": "ok"}
    assert scheduled and scheduled[0] is power.ACTIONS["shutdown"]
    assert power.handle_power({"action": "format_c"})["status"] == "error"
    assert len(scheduled) == 1  # the refused one scheduled nothing


def test_clipboard_checks_the_text(monkeypatch):
    from handlers import clipboard

    got = []
    monkeypatch.setattr(clipboard, "_set_clipboard", got.append)
    assert clipboard.handle_clipboard_set({"value": "hello"})["status"] == "ok"
    assert got == ["hello"]
    for bad in ("", "   ", None, 42, "x" * (clipboard.MAX_CHARS + 1)):
        assert clipboard.handle_clipboard_set({"value": bad})["status"] == "error"
    assert got == ["hello"]


def test_net_rate_reports_bytes_per_second_for_one_interval():
    from collections import namedtuple

    from handlers.system import NetRate

    Counters = namedtuple("Counters", "bytes_recv bytes_sent")
    samples = iter([Counters(1000, 500), Counters(3000, 1500), Counters(10, 10)])
    times = iter([10.0, 12.0, 13.0])
    rate = NetRate(counters=lambda: next(samples), clock=lambda: next(times))

    assert (rate.down(), rate.up()) == (0, 0)  # baseline only
    assert (rate.down(), rate.up()) == (1000, 500)  # 2000 B and 1000 B over 2 s
    assert (rate.down(), rate.up()) == (0, 0)  # counters reset: never negative


def test_cpu_and_ram_readers_return_percentages():
    from handlers.system import cpu_percent, ram_percent

    assert 0 <= cpu_percent() <= 100
    assert 0 <= ram_percent() <= 100
