import asyncio

from handlers.audio import get_default_output_name, get_muted, get_volume
from handlers.process import VPN_WATCH, get_watched_process_name
from handlers.system import NET, cpu_percent, ram_percent

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
    # Through VPN_WATCH rather than a full process walk every tick; see
    # ProcessWatch for how it stays just as current.
    "vpn.running": lambda: VPN_WATCH.running(get_watched_process_name()),
    # The PC-load widget. net_down before net_up: one sample feeds both.
    "pc.cpu": cpu_percent,
    "pc.ram": ram_percent,
    "pc.net_down": NET.down,
    "pc.net_up": NET.up,
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


async def poll_loop(send_state_callback, active: "asyncio.Event | None" = None) -> None:
    """Send the state snapshot every second while `active` is set.

    `active` is cleared while no Dashboard is connected (the backend says so;
    see ConnectionHub.sync_watchers): nobody would see the values, so the
    audio and process reads stop entirely until one connects. The first tick
    after that runs at once, so the phone that just connected gets fresh
    values rather than whatever the backend held from before the pause.
    None means "always active", which is also what happens against a backend
    that never sends the notice.
    """
    last_errors: dict = {}
    while True:
        if active is not None and not active.is_set():
            await active.wait()
        snapshot = read_snapshot(READERS, last_errors)
        # Sent unconditionally every tick; the backend (app.state.update_state)
        # is what dedupes into change-only broadcasts, so no diffing here.
        if snapshot:
            await send_state_callback(snapshot)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
