"""The Clipboard tile: text typed on the phone lands on the PC's clipboard.

It arrives through set_value (the phone's sheet sends the text as the value),
so params["value"] is the text. Checked here too -- it came over the network.
"""

import time

# Plenty for a link, a code or a paragraph; a guard against pasting a book.
MAX_CHARS = 100_000


def check_text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nothing to send -- type some text first.")
    if len(value) > MAX_CHARS:
        raise ValueError(f"That's too long -- up to {MAX_CHARS:,} characters.")
    return value


def _set_clipboard(text: str) -> None:
    import win32clipboard

    # Another program may hold the clipboard for a moment (clipboard managers
    # do); a few short retries beat failing on the first collision.
    for attempt in range(5):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(0.05)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def handle_clipboard_set(params: dict) -> dict:
    try:
        _set_clipboard(check_text(params.get("value")))
        return {"status": "ok"}
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:
        return {"status": "error", "message": f"Couldn't set the clipboard: {exc}"}
