import os
from pathlib import Path

import psutil


# --- VPN / process-toggle configuration -----------------------------------

# The process name poll_loop reports as "vpn.running". Seeded from the
# environment (the legacy Docker/.env path, which is the only way this was
# ever configurable) and updated whenever a process_toggle command arrives
# carrying its own process_name -- see resolve_toggle_target().
#
# The runtime update exists because the two sides learn this name from
# different places: the handler gets it from the item's params, which is
# where standalone mode can actually put it (Studio writes params; nothing
# in standalone writes agent env vars), while poll_loop has no access to
# items at all. Without it, a params-configured VPN tile would toggle
# correctly but never light up. The cost is that state is unknown until the
# first press of that tile -- accepted rather than building an
# agent-config channel over the WebSocket for one string.
_active_process_name = os.environ.get("VPN_PROCESS_NAME", "")


def get_watched_process_name() -> str:
    return _active_process_name


def resolve_toggle_target(params: dict) -> tuple[str, str]:
    """Return (process_name, path) for a process_toggle-style item.

    Params win over the environment. That ordering is the fix for standalone
    mode: VPN_PROCESS_NAME/VPN_PATH only ever existed in the agent's .env,
    which the Docker path has and standalone/launcher.py does not generate --
    so `os.environ["VPN_PROCESS_NAME"]` raised KeyError and the VPN tile
    returned an error on every press. Reading the item's own params first
    means the tile is configurable from Studio, on the machine it controls,
    with no file editing; the env fallback keeps every existing .env install
    working exactly as before.

    Raises KeyError-free: a missing value comes back as "" and the caller
    turns it into a message a person can act on.
    """
    global _active_process_name
    process_name = params.get("process_name") or os.environ.get("VPN_PROCESS_NAME", "")
    path = params.get("path") or os.environ.get("VPN_PATH", "")
    if process_name:
        _active_process_name = process_name
    return process_name, path


def is_process_running(name: str) -> bool:
    target = name.lower()
    for proc in psutil.process_iter():
        try:
            if proc.name().lower() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Process exited mid-scan, or is a protected/system process we
            # can't query -- neither means "not the one we're looking for",
            # so skip it rather than let one flaky process fail the scan.
            continue
    return False


def start_process(path: str) -> None:
    os.startfile(path)


def kill_process(name: str) -> None:
    target = name.lower()
    for proc in psutil.process_iter():
        try:
            if proc.name().lower() == target:
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def handle_launch_app(params: dict) -> dict:
    try:
        # ShellExecute, not CreateProcess: Popen() cannot raise a UAC prompt,
        # so an exe whose manifest requires admin (v2rayTun) failed silently --
        # command received, no crash, app never opened. os.startfile() is the
        # same call Explorer's double-click uses, so Windows shows its own
        # elevation prompt; a normal exe launches exactly as before.
        os.startfile(params["path"])
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_force_stop(params: dict, item_type: str | None = None) -> dict:
    try:
        process_name = params.get("process_name")

        # Deriving from the original item's own type/params instead of
        # requiring a dedicated stored field means force-stop works
        # immediately on every existing item, with zero manual data entry
        # via Studio Mode.
        if process_name is None:
            if item_type == "launch_app" and params.get("path"):
                process_name = os.path.basename(params["path"])
            elif item_type == "process_toggle":
                # Same resolution handle_process_toggle uses, via the same
                # helper -- this call site had the identical bare
                # os.environ[...] lookup and so the identical KeyError on a
                # standalone install.
                process_name, _ = resolve_toggle_target(params)
                if not process_name:
                    return {
                        "status": "error",
                        "message": "VPN tile is not configured yet -- set process_name in this item's params (Studio)",
                    }
            else:
                return {
                    "status": "error",
                    "message": "no process identifiable for force stop on this item type",
                }

        # kill_process() is already a no-op if nothing matches, so this is
        # idempotent by construction -- "ok" regardless of whether anything
        # was actually running.
        kill_process(process_name)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_process_toggle(params: dict) -> dict:
    try:
        vpn_process_name, vpn_path = resolve_toggle_target(params)
        if not vpn_process_name or not vpn_path:
            # A named, actionable message rather than a raw KeyError string:
            # this is the state a fresh standalone install starts in, so it
            # is the first thing a new user sees from this tile.
            return {
                "status": "error",
                "message": (
                    "VPN tile is not configured yet -- set process_name and path "
                    "in this item's params (Studio), e.g. "
                    r'{"process_name": "openvpn-gui.exe", "path": "C:\\Program Files\\OpenVPN\\bin\\openvpn-gui.exe"}'
                ),
            }

        if is_process_running(vpn_process_name):
            kill_process(vpn_process_name)
        else:
            # Checked explicitly rather than letting Popen raise: a bad
            # VPN_PATH should come back as a clear message, not a raw
            # FileNotFoundError traceback string.
            if not Path(vpn_path).exists():
                return {"status": "error", "message": f"VPN_PATH does not exist: {vpn_path}"}
            start_process(vpn_path)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
