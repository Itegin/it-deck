import asyncio
import json
import os
import sys
import time

import win32api
import win32event
import winerror
from dotenv import load_dotenv
from websockets.asyncio.client import connect

from dispatch import receive_loop
from handlers.apps import handle_fetch_icon, handle_list_apps
from handlers.audio import (
    handle_audio_mute_toggle,
    handle_audio_switch,
    handle_audio_volume_set,
    handle_list_devices,
)
from handlers.process import (
    handle_force_stop,
    handle_launch_app,
    handle_open_url,
    handle_process_toggle,
)
from handlers.screenshot import handle_screenshot
from poller import poll_loop

load_dotenv()

SERVER_IP = os.environ["SERVER_IP"]
AGENT_TOKEN = os.environ["AGENT_TOKEN"]
AGENT_NAME = os.environ.get("AGENT_NAME", "windows")
# Previously hardcoded 8000 here -- SERVER_PORT was documented in
# .env.example but never actually read anywhere, in violation of
# CLAUDE.md's own "SERVER_PORT must be read from .env, never hardcoded"
# constraint. Fixed here since the screenshot upload below needs the same
# value and must not become a second hardcoded copy of it.
SERVER_PORT = os.environ.get("SERVER_PORT", "8000")
SERVER_URL = f"ws://{SERVER_IP}:{SERVER_PORT}/ws/agent"

MAX_BACKOFF = 30
# A connection that lasted this long counts as having worked; see main().
HEALTHY_CONNECTION_SECONDS = 10
SINGLETON_MUTEX_NAME = "Global\\ITDeckAgentSingleton"

# Exit code for "another agent already holds the singleton mutex". Mirrored
# in standalone/launcher.py, which must not treat it as a crash to respawn --
# see the comment at the sys.exit() call in main().
EXIT_ALREADY_RUNNING = 3

# Set in main(); see the comment there for why the handle must outlive the
# call that created it.
_singleton_handle = None

# When the current connection opened (time.monotonic()), or None before it
# has. main() reads it to tell a connection that worked from one that never
# did -- see the backoff reset there.
_opened_at = None


def handle_agent_shutdown(params: dict) -> dict:
    # No-op on purpose: the actual exit happens in _shutdown() once this
    # "ok" has gone out. Exiting from here would kill the process before the
    # result frame is written, leaving the req_id unresolved forever.
    return {"status": "ok"}


HANDLERS = {
    "launch_app": handle_launch_app,
    "open_url": handle_open_url,
    "audio_mute_toggle": handle_audio_mute_toggle,
    "audio_volume_set": handle_audio_volume_set,
    "audio_switch": handle_audio_switch,
    "list_devices": handle_list_devices,
    "list_apps": handle_list_apps,
    "fetch_icon": handle_fetch_icon,
    "screenshot": handle_screenshot,
    "process_toggle": handle_process_toggle,
    "force_stop": handle_force_stop,
    "agent_shutdown": handle_agent_shutdown,
}


async def _shutdown() -> None:
    # Brief pause so the "ok" result actually reaches the wire before we go.
    # os._exit() rather than sys.exit()/returning cleanly: anything unwinding
    # through run() lands back in main()'s reconnect loop, which would
    # immediately reconnect the agent we were just asked to shut down.
    await asyncio.sleep(0.2)
    os._exit(0)


async def run() -> None:
    global _opened_at
    async with connect(SERVER_URL) as ws:
        _opened_at = time.monotonic()
        await ws.send(json.dumps({
            "type": "hello",
            "agent": AGENT_NAME,
            "version": "0.1.0",
            "token": AGENT_TOKEN,
        }))
        print(f"Connected to {SERVER_URL}")

        async def send_state(snapshot: dict) -> None:
            await ws.send(json.dumps({"type": "state", "data": snapshot}))

        # Both run for the lifetime of this connection, and whichever ends
        # first ends the other: a closed socket stops the receive loop and
        # makes the poller's next send raise, and either way the survivor is
        # cancelled here rather than left running against a dead connection
        # (asyncio.gather, which this used to be, does not cancel it).
        # Deliberately not asyncio.TaskGroup: that needs Python 3.11, and a
        # legacy agent may run on any interpreter comtypes supports.
        tasks = [
            asyncio.ensure_future(receive_loop(ws, HANDLERS, _shutdown)),
            asyncio.ensure_future(poll_loop(send_state)),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in done:
            # Re-raises the failure that ended the connection, if there was
            # one, so main() logs it and backs off.
            task.result()


async def main() -> None:
    # Named mutex, not a lock file: Windows releases it automatically if the
    # owning process dies, so there's no stale-lock cleanup to write. This
    # guards against the Scheduled Task autostart and a manual launch both
    # running at once -- two agents silently overwriting each other's
    # WebSocket registration on the backend caused a day of contradictory
    # VPN-state bugs.
    #
    # The handle is parked in a module global rather than discarded: a
    # PyHANDLE nobody holds is garbage-collected, and closing the last handle
    # destroys the mutex -- so this guard was only ever as durable as
    # CPython's refcounting happened to make it. That matters more now that
    # standalone/launcher.py respawns a dead agent: a fresh agent racing its
    # own dying predecessor must see a mutex that is either still genuinely
    # held or genuinely gone, never one that vanished early because a local
    # variable went out of scope.
    global _singleton_handle
    _singleton_handle = win32event.CreateMutex(None, False, SINGLETON_MUTEX_NAME)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        print("Another instance is already running -- exiting.")
        # A dedicated code, not 0 and not 1, because two callers read this
        # exit and they want opposite things from it:
        #
        #   - standalone/launcher.py's supervisor respawns the agent on a
        #     crash. It must NOT respawn this one -- a double-launch would
        #     otherwise become a restart storm between two processes that
        #     both correctly refuse to run -- so it treats this code as
        #     "don't restart", same as a clean 0.
        #   - agents/windows/start_agent.bat pauses on any non-zero exit, so
        #     the legacy shortcut keeps its window open with the message
        #     above still readable. Exiting 0 here would close that window
        #     instantly and leave a double-launch looking like nothing
        #     happened at all.
        #
        # Keep both in mind before changing it; EXIT_ALREADY_RUNNING is
        # mirrored in launcher.py.
        sys.exit(EXIT_ALREADY_RUNNING)

    global _opened_at
    backoff = 1
    while True:
        _opened_at = None
        try:
            await run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Deliberately broad, and a fix rather than sloppiness. This was
            # `except (ConnectionClosed, OSError)`, which misses the single
            # most likely failure on a machine where backend and agent start
            # together: websockets raises InvalidHandshake (InvalidStatus,
            # InvalidMessage, ...) when the server answers the upgrade with
            # something that isn't a WebSocket, and InvalidHandshake derives
            # from WebSocketException/Exception, NOT from OSError -- verified
            # against the pinned websockets==13.1. So a backend restart, or
            # the window before uvicorn has its routes mounted, killed the
            # agent process outright instead of reconnecting. Same for a
            # KeyError escaping the receive loop, or anything the audio stack
            # throws up through poll_loop.
            #
            # Nothing above this frame can recover -- main() IS the recovery
            # path -- and a reconnect that fails again simply backs off
            # further, which is the right response to every one of these.
            print(f"Disconnected ({type(exc).__name__}: {exc})")

        # A connection that held for a while was a working one, so what ended
        # it (typically a backend restart) deserves a prompt retry, not the
        # delay left over from failures before it. Before this the backoff was
        # only reset on a clean close, so after a backend restart the agent
        # could sit out a full 30s. Measured from when the socket opened, not
        # from the attempt: a connect that hangs until its own timeout is a
        # failure however long it took. And on uptime rather than on "the
        # hello went out", because a rejected token also gets that far -- and
        # must keep backing off instead of retrying every second.
        if _opened_at is not None and time.monotonic() - _opened_at >= HEALTHY_CONNECTION_SECONDS:
            backoff = 1

        print(f"Reconnecting in {backoff}s")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)


if __name__ == "__main__":
    asyncio.run(main())
