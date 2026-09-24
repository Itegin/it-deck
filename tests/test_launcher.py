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


# --- the window's look -------------------------------------------------------

CSS = {
    "base": (REPO / "frontend" / "css" / "base.css").read_text(encoding="utf-8"),
    "themes": (REPO / "frontend" / "css" / "themes.css").read_text(encoding="utf-8"),
    "studio": (REPO / "frontend" / "css" / "studio.css").read_text(encoding="utf-8"),
}


def css_block(text: str, selector: str) -> str:
    """The body of the first rule whose selector is exactly `selector`."""
    start = text.index(selector + " {")
    return text[start : text.index("\n}", start)]


def declared(block: str, name: str) -> str:
    # The veil and the edge are single stops inside multi-part values; the
    # rest are whole declarations.
    if name == "--glass-veil":
        return re.search(r"--glass-veil:[^;]*?(rgba\([^)]*\)) 42%", block).group(1)
    if name == "--glass-edge":
        return re.search(r"--glass-edge: inset 0 1px 0 (rgba\([^)]*\))", block).group(1)
    return re.search(re.escape(name) + r":\s*([^;]+);", block).group(1).strip()


def test_window_palette_is_studios():
    # The window promises it looks like Studio. It only can while it uses
    # Studio's own numbers, so each token is read back from the CSS it was
    # copied from; a Studio palette change fails here until the window
    # follows it.
    blocks = {
        "--color-bg": css_block(CSS["themes"], '[data-theme="liquid-glass"]:not([data-mode="light"])'),
        "--glass-veil": css_block(CSS["themes"], '[data-theme="liquid-glass"]:not([data-mode="light"])'),
        "--glass-edge": css_block(CSS["themes"], '[data-theme="liquid-glass"]:not([data-mode="light"])'),
        "--studio-*": css_block(CSS["studio"], '[data-theme="liquid-glass"]:not([data-mode="light"])'),
        "--color-*": css_block(CSS["base"], ":root"),
    }
    for name, value in launcher._STUDIO_TOKENS.items():
        if name in blocks:
            block = blocks[name]
        else:
            block = blocks["--studio-*" if name.startswith("--studio-") else "--color-*"]
        assert declared(block, name).lower() == value.lower(), name
    # And the primary button's ink.
    assert launcher._ACCENT_INK in css_block(CSS["studio"], ".btn-primary")


def test_palette_compositing():
    assert launcher._over("rgba(255, 255, 255, 0.5)", "#000000") == "#808080"
    assert launcher._over("#123456", "#ffffff") == "#123456"
    assert launcher._brighten("#a78bfa", 1.08) == "#b496ff"
    g = launcher._GLASS
    # Every colour the window paints is opaque #rrggbb -- Tk has no alpha.
    assert all(re.fullmatch(r"#[0-9a-fA-F]{6}", v) for v in g.values())
    # A panel lifts off the ground, and a button off the panel.
    lum = lambda c: sum(launcher._css_rgba(c)[:3])  # noqa: E731
    assert lum(g["bg"]) < lum(g["surface"]) < lum(g["surface_raised"]) < lum(g["surface_hover"])


def test_rounded_png_is_antialiased_and_transparent_outside():
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    data = launcher._rounded_png(40, 40, 10, fill="#202020", outline="#808080", ring="#a78bfa", margin=3)
    image = Image.open(io.BytesIO(data))
    assert image.size == (40, 40) and image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0  # the corner is see-through
    assert image.getpixel((20, 20))[:3] == (0x20, 0x20, 0x20)  # the fill
    # Partial alpha somewhere along the curve: antialiased, not stair-stepped.
    alphas = {image.getpixel((x, x))[3] for x in range(0, 8)}
    assert any(0 < a < 255 for a in alphas)


def test_overlay_ground_is_studios_page():
    # The tour and What's new sit on Studio's own page background: both
    # pools, verbatim, from the dark Liquid Glass body rule.
    body = css_block(CSS["themes"], '[data-theme="liquid-glass"]:not([data-mode="light"]) body')
    for pool in launcher._STUDIO_POOLS:
        assert pool in body, pool
        assert launcher._POOL_RE.fullmatch(pool), pool


def test_overlay_ground_draws_the_pools():
    pytest.importorskip("PIL")
    image = launcher._ground_image(400, 600)
    assert image.size == (400, 600)
    bg = launcher._css_rgba(launcher._GLASS["bg"])[:3]
    purple = image.getpixel((72, 48))  # the first pool's centre: 18% 8%
    teal = image.getpixel((352, 492))  # the second's: 88% 82%
    assert purple[2] > bg[2] + 20 and purple[0] > purple[1]  # violet, lifted
    assert teal[1] > bg[1] + 10 and teal[1] > teal[0]  # teal, lifted
    # Fades to the bare ground: far from both centres nothing is added, and
    # there is no hard edge where a pool's box ends.
    assert image.getpixel((399, 0)) == bg
    column = [image.getpixel((72, y))[2] for y in range(48, 400)]
    assert max(abs(a - b) for a, b in zip(column, column[1:])) <= 2


def test_nine_slice_keeps_corners_and_fills_the_middle():
    pytest.importorskip("PIL")
    tile = launcher._rounded_image(48, 48, 16, fill="#202020", outline="#808080")
    big = launcher._nine_slice(tile, 16, 300, 120)
    assert big.size == (300, 120)
    # Corners unchanged, so they stay antialiased; the middle is the fill.
    assert big.crop((0, 0, 16, 16)).tobytes() == tile.crop((0, 0, 16, 16)).tobytes()
    assert big.crop((284, 104, 300, 120)).tobytes() == tile.crop((32, 32, 48, 48)).tobytes()
    assert big.getpixel((150, 60))[:3] == (0x20, 0x20, 0x20)


def test_nine_slice_sources_cover_a_panel_in_few_tiles():
    # ttk tiles the middle and edges of an image element, and on Windows each
    # tile of a translucent image is a separate slow blend: 48-px sources
    # meant hundreds of tiles per panel and a window that painted itself in
    # visible strips. A step panel (~490 x 260) must take one tile each way.
    panel_w, panel_h = launcher._PANEL_IMAGE
    assert panel_w - 2 * launcher._RADIUS_PANEL >= 490 - 2 * launcher._RADIUS_PANEL
    assert panel_h - 2 * launcher._RADIUS_PANEL >= 260 - 2 * launcher._RADIUS_PANEL
    edge = launcher._RADIUS_CONTROL + launcher._FOCUS_MARGIN
    assert launcher._CONTROL_IMAGE[0] - 2 * edge >= 200  # the widest button's middle
    assert launcher._WELL_IMAGE[0] - 2 * launcher._RADIUS_CONTROL >= 400  # the link field


class _FakeRoot:
    def __init__(self, calls):
        self.calls = calls

    def update_idletasks(self):
        self.calls.append("idle")

    def update(self):
        self.calls.append("update")


def test_overlay_swap_still_happens_without_a_cover(monkeypatch):
    # No snapshot (no Pillow, PrintWindow refused, not Windows): the overlay
    # still goes and the screen is still brought up to date -- only the
    # one-frame cut is lost.
    pytest.importorskip("tkinter")

    def no_snapshot(_root):
        raise OSError("PrintWindow failed")

    monkeypatch.setattr(launcher, "_window_snapshot", no_snapshot)
    calls = []
    launcher._swap_behind_curtain(_FakeRoot(calls), lambda: calls.append("change"))
    # update(), not only update_idletasks(): Tk's redraws wait on the event queue.
    assert calls[-2:] == ["change", "update"]


@pytest.mark.skipif(sys.platform != "win32", reason="the cover is a Windows window")
def test_overlay_cover_always_comes_off():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        root.geometry("300x200")
        tk.Label(root, text="main screen").pack()
        overlay = tk.Frame(root, bg="black")
        overlay.place(x=0, y=0, relwidth=1, relheight=1)
        root.update()

        def toplevels():
            return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]

        launcher._swap_behind_curtain(root, overlay.destroy)
        assert not overlay.winfo_exists()
        assert toplevels() == []

        def broken():
            raise RuntimeError("change failed")

        with pytest.raises(RuntimeError):
            launcher._swap_behind_curtain(root, broken)
        assert toplevels() == []  # a failed change never leaves the window covered
    finally:
        root.destroy()
