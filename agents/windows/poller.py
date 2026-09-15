import asyncio

from handlers.audio import get_default_output_name, get_muted, get_volume
from handlers.process import get_watched_process_name, is_process_running

POLL_INTERVAL_SECONDS = 1


async def poll_loop(send_state_callback) -> None:
    while True:
        try:
            snapshot = {
                "mic.muted": get_muted("microphone"),
                "speaker.volume": get_volume("speaker"),
                "speaker.muted": get_muted("speaker"),
                "speaker.device_name": get_default_output_name(),
                # Not os.environ directly: standalone mode never generates
                # VPN_PROCESS_NAME, so the name can also arrive from the VPN
                # item's own params on the first press. get_watched_process_name()
                # is the single place that knows which of the two applies --
                # see handlers/process.py's resolve_toggle_target().
                "vpn.running": is_process_running(get_watched_process_name()),
            }
        except Exception as exc:
            # Transient audio-stack errors (device swap mid-read, etc.)
            # shouldn't kill the agent's connection; just skip this tick.
            print(f"Poll error: {exc}")
        else:
            # Sent unconditionally every tick; the backend (app.state.update_state)
            # is what dedupes into change-only broadcasts, so no diffing here.
            await send_state_callback(snapshot)

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
