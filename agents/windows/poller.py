import asyncio

from handlers.audio import get_default_output_name, get_muted, get_volume
from handlers.process import get_watched_process_name, is_process_running

POLL_INTERVAL_SECONDS = 1

# Each key the deck can light a tile from, and how to read it. Read one by
# one: when they were read in a single try, one failing source -- a PC with
# no microphone, a device swapped mid-read -- threw away the whole snapshot,
# so the phone got no state at all, not just no mic state.
READERS = {
    "mic.muted": lambda: get_muted("microphone"),
    "speaker.volume": lambda: get_volume("speaker"),
    "speaker.muted": lambda: get_muted("speaker"),
    "speaker.device_name": get_default_output_name,
    # Not os.environ directly: standalone mode never generates
    # VPN_PROCESS_NAME, so the name can also arrive from the VPN item's own
    # params on the first press. get_watched_process_name() is the single
    # place that knows which of the two applies -- see handlers/process.py's
    # resolve_toggle_target().
    "vpn.running": lambda: is_process_running(get_watched_process_name()),
}


def read_snapshot(readers: dict, last_errors: dict) -> dict:
    """Every key that could be read this tick. Keys that failed are left out.

    A failing key's error is printed when it first appears or changes, not
    every tick: a PC with no microphone would otherwise write the same line
    to agent.log once a second, forever. `last_errors` carries that memory
    between ticks.
    """
    snapshot = {}
    for key, read in readers.items():
        try:
            snapshot[key] = read()
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if last_errors.get(key) != message:
                print(f"Poll error reading {key}: {message}")
                last_errors[key] = message
        else:
            if key in last_errors:
                print(f"Poll recovered: {key}")
                del last_errors[key]
    return snapshot


async def poll_loop(send_state_callback) -> None:
    last_errors: dict = {}
    while True:
        snapshot = read_snapshot(READERS, last_errors)
        # Sent unconditionally every tick; the backend (app.state.update_state)
        # is what dedupes into change-only broadcasts, so no diffing here.
        if snapshot:
            await send_state_callback(snapshot)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
