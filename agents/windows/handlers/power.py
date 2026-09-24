"""The Power tile: lock the PC, put it to sleep, restart or shut it down.

Each action is scheduled a moment *after* the handler returns, so its "ok"
reaches the phone first: sleep blocks the calling thread until the PC wakes,
and shutdown ends everything, the agent included -- answering afterwards
would mean never answering (CLAUDE.md: every req_id resolves).

The tile asks "Run?" on the phone before sending by default (the Studio
"Ask before running" option), because a mis-tap here costs unsaved work.
"""

import subprocess
import threading

# Long enough for the result frame to leave, short enough to feel immediate.
_DELAY_SECONDS = 0.5


def _lock() -> None:
    import ctypes

    ctypes.windll.user32.LockWorkStation()


def _sleep() -> None:
    import ctypes

    # (hibernate=False, force=False, disable wake events=False): sleep, not
    # hibernate, and let apps veto it as a normal Start-menu sleep would.
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def _shutdown(flag: str):
    def run() -> None:
        # An argument list, no shell. /t 0: the phone already asked "Run?".
        subprocess.Popen(["shutdown", flag, "/t", "0"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    return run


ACTIONS = {
    "lock": _lock,
    "sleep": _sleep,
    "restart": _shutdown("/r"),
    "shutdown": _shutdown("/s"),
}


def _schedule(action) -> None:
    timer = threading.Timer(_DELAY_SECONDS, action)
    timer.daemon = True
    timer.start()


def handle_power(params: dict) -> dict:
    action = ACTIONS.get(params.get("action", "lock"))
    if action is None:
        return {"status": "error", "message": f'Unknown power action "{params.get("action")}".'}
    _schedule(action)
    return {"status": "ok"}
