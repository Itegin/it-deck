"""The launcher's pure helpers: "What's new", PINs, start with Windows.

The Tk window itself needs Windows and a display; what it decides is
decided here, in functions that don't.
"""

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "standalone"))

import launcher  # noqa: E402

ENTRIES = [
    {"version": "0.7.0", "en": ["seven"], "ru": ["семь"]},
    {"version": "0.6.1", "en": ["six-one"], "ru": ["шесть-один"]},
    {"version": "0.6.0", "en": ["six"], "ru": ["шесть"]},
]


def versions(entries):
    return [e["version"] for e in entries]


def test_whats_new_shows_what_is_new_since_last_time():
    assert versions(launcher.whats_new_to_show(ENTRIES, "0.6.1", "0.6.0")) == ["0.6.1"]
    assert launcher.whats_new_to_show(ENTRIES, "0.6.1", "0.6.1") == []


def test_whats_new_never_announces_a_newer_build_and_caps_at_two():
    # Seen nothing yet (an install from before this feature): newest two only.
    assert versions(launcher.whats_new_to_show(ENTRIES, "0.7.0", None)) == ["0.7.0", "0.6.1"]
    # A build older than the notes it ships with shows nothing.
    assert launcher.whats_new_to_show(ENTRIES, "0.5.5", None) == []


def test_seen_marker_round_trip(tmp_path):
    assert launcher.read_whats_new_seen(tmp_path) is None
    launcher.mark_whats_new_seen(tmp_path, "0.6.0")
    assert launcher.read_whats_new_seen(tmp_path) == "0.6.0"


def test_the_shipped_whats_new_file_is_well_formed():
    entries = json.loads((REPO / "frontend" / "whats-new.json").read_text(encoding="utf-8"))
    assert entries, "at least one entry"
    tuples = [launcher._version_tuple(e["version"]) for e in entries]
    assert tuples == sorted(tuples, reverse=True), "newest first"
    for entry in entries:
        for lang in ("en", "ru"):
            bullets = entry[lang]
            assert 1 <= len(bullets) <= 5, f"{entry['version']} {lang}: 1-5 bullets"
            assert all(isinstance(b, str) and b.strip() for b in bullets)
        assert len(entry["en"]) == len(entry["ru"]), f"{entry['version']}: same bullets in both languages"
    # Every build ships the notes of the version it will be released as.
    assert tuples[0] >= launcher._version_tuple(launcher.ITDECK_VERSION)
    assert launcher.load_whats_new(REPO / "frontend") == entries


def test_new_pin_is_six_digits():
    pins = {launcher.new_pin() for _ in range(20)}
    assert all(re.fullmatch(r"\d{6}", pin) for pin in pins)
    assert len(pins) > 1


class FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    """Just enough of winreg for the Run key."""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path):
        return FakeKey()

    def CreateKey(self, root, path):
        assert path == launcher.AUTOSTART_KEY
        return FakeKey()

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.mark.parametrize("exe", [Path("C:/Apps/ITDeck.exe")])
def test_autostart_on_off_and_moved_exe(exe):
    reg = FakeWinreg()
    assert launcher.autostart_enabled(reg, exe) is False
    launcher.set_autostart(reg, exe, True)
    assert launcher.autostart_enabled(reg, exe) is True
    # The exe moved: the old value points elsewhere, so the box reads "off".
    assert launcher.autostart_enabled(reg, Path("D:/New/ITDeck.exe")) is False
    launcher.set_autostart(reg, exe, False)
    launcher.set_autostart(reg, exe, False)  # already off: no error
    assert launcher.autostart_enabled(reg, exe) is False
