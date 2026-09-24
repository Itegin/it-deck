"""Keyboard tiles: a hotkey (send_keys) and a media key (media_key).

Keys are synthesised with keybd_event, the same path a real keyboard's
events take into the foreground window -- so a global hotkey (Discord's
push-to-mute, OBS's scene switch) or a media key (Play/Pause for whatever is
playing) works exactly as if pressed on the PC. Some games and anti-cheat
systems ignore synthetic input on purpose; nothing here can change that.

Windows is only touched inside _send(), so the parsing -- the part with rules
worth testing -- runs anywhere.
"""

import time

# Virtual-key codes (winuser.h). Names are lower-case; aliases map onto them.
_NAMED = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
    "enter": 0x0D, "esc": 0x1B, "space": 0x20, "tab": 0x09, "backspace": 0x08,
    "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "printscreen": 0x2C, "capslock": 0x14,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play_pause": 0xB3,
}
_ALIASES = {
    "control": "ctrl", "cmd": "win", "windows": "win", "super": "win", "meta": "win",
    "return": "enter", "escape": "esc", "del": "delete", "ins": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "prtsc": "printscreen",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
}
_MODIFIERS = {0x11, 0x10, 0x12, 0x5B}
# Keys Windows expects with KEYEVENTF_EXTENDEDKEY; without it the arrows and
# friends arrive as their numpad twins, and media keys may be ignored.
_EXTENDED = {
    0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x2C, 0x5B,
    0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3,
}
# A chord, not a macro: five keys is more than any real shortcut uses.
MAX_KEYS = 5

# The Media tile's choices, by the name Studio stores in params.key.
MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "prev": 0xB1,
    "stop": 0xB2,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
}

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002


def _code(name: str) -> int:
    name = _ALIASES.get(name, name)
    if name in _NAMED:
        return _NAMED[name]
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())  # 'a'..'z' -> 0x41.., '0'..'9' -> 0x30..
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    raise ValueError(f'Unknown key "{name}". Use names like ctrl, shift, alt, win, a, 5, f5, enter, up.')


def parse_keys(text) -> list:
    """"ctrl+shift+m" -> the virtual-key codes to press, in order."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("No keys set -- add them in Studio, e.g. ctrl+shift+m.")
    names = [part.strip().lower() for part in text.split("+")]
    if any(not name for name in names):
        raise ValueError(f'"{text}" has an empty key -- write it like ctrl+shift+m.')
    codes = [_code(name) for name in names]
    if len(codes) > MAX_KEYS:
        raise ValueError(f"At most {MAX_KEYS} keys at once.")
    if len(set(codes)) != len(codes):
        raise ValueError(f'"{text}" names the same key twice.')
    if all(code in _MODIFIERS for code in codes):
        raise ValueError(f'"{text}" is only modifiers -- add the key they go with.')
    return codes


def _send(codes: list) -> None:
    """Press every key in order, then release them in reverse, like a hand."""
    import ctypes

    user32 = ctypes.windll.user32

    def event(code: int, up: bool) -> None:
        flags = (_KEYEVENTF_EXTENDEDKEY if code in _EXTENDED else 0) | (_KEYEVENTF_KEYUP if up else 0)
        user32.keybd_event(code, 0, flags, 0)

    for code in codes:
        event(code, up=False)
        # Some apps poll the keyboard state rather than reading messages;
        # a few milliseconds held is what makes a chord register for them.
        time.sleep(0.01)
    for code in reversed(codes):
        event(code, up=True)


def handle_send_keys(params: dict) -> dict:
    try:
        _send(parse_keys(params.get("keys")))
        return {"status": "ok"}
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:
        return {"status": "error", "message": f"Couldn't press the keys: {exc}"}


def handle_media_key(params: dict) -> dict:
    key = params.get("key", "play_pause")
    if key not in MEDIA_KEYS:
        return {"status": "error", "message": f'Unknown media key "{key}".'}
    try:
        _send([MEDIA_KEYS[key]])
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "message": f"Couldn't press the key: {exc}"}
