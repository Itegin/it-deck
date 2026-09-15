import os
import subprocess
import threading
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


# How long a launch may block the caller before it is left to finish on its
# own. The budget it protects is CLAUDE.md's non-negotiable one: every execute
# command must resolve within 5s. Confirmed broken before this -- a VPN tile
# press came back `{"status": "error", "message": "timeout"}` while the app
# itself started a few seconds later, because the launch runs synchronously
# inside the agent's single receive loop.
_LAUNCH_BUDGET_SECONDS = 2.0


def _spawn_detached(path: str) -> None:
    """CreateProcess the target with no console and its own process group.

    The isolation this buys is a hard requirement, not an optimisation:
    **closing IT-Deck must never take down anything it launched**, the VPN
    client above all. DETACHED_PROCESS means the child never inherits the
    agent's console, so a console close event cannot reach it;
    CREATE_NEW_PROCESS_GROUP keeps it out of the group Ctrl+C/Ctrl+Break are
    delivered to; CREATE_BREAKAWAY_FROM_JOB keeps it out of any job object
    the launcher or a terminal host might be holding, so a job-wide kill
    can't sweep it up either.

    To be clear about what this is and isn't: no measurement in this project
    has ever *reproduced* IT-Deck killing a launched process. This is the
    guarantee written into the code so the question stops being empirical.
    (The flags themselves are not new -- commit 341426b added them for this
    exact reason and 0176612 dropped them again without comment.)

    CREATE_BREAKAWAY_FROM_JOB is attempted separately because CreateProcess
    rejects it outright when the current job forbids breakaway, and losing
    the whole detached launch over an optional flag would be the wrong trade.
    """
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    try:
        subprocess.Popen([path], creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, close_fds=True)
    except OSError:
        subprocess.Popen([path], creationflags=flags, close_fds=True)


def start_process(path: str) -> None:
    """Launch `path`, detached, without blowing the command's time budget.

    Two failure modes this shape exists for, both observed:

    1. `os.startfile()` (ShellExecute) can take many seconds to return -- it
       hands off through the shell -- and it ran inline in the receive loop.
       So the launch happens on a worker thread and the caller waits only
       `_LAUNCH_BUDGET_SECONDS` for it.
    2. CreateProcess alone can't launch everything: a `.lnk`, a document, a
       URL, or an exe whose manifest demands elevation all need ShellExecute.
       So `os.startfile()` stays as the fallback, and only as the fallback.

    A launch that finishes inside the budget reports its real error, which is
    the common case (a bad path, a missing exe). One that doesn't returns as
    a success and keeps going in the background -- so **"ok" here means "the
    launch was started", not "the app is on screen"**. That is the honest
    trade for never breaching the 5s rule; the alternative was a tile that
    reported `timeout` while the app opened anyway.

    Note the fallback is the *less* isolated path: a ShellExecute'd process
    is created by the shell rather than with our flags. Everything that can
    go through CreateProcess does, which is every ordinary .exe.
    """
    failure: list[BaseException] = []

    def launch() -> None:
        try:
            _spawn_detached(path)
        except Exception:
            try:
                os.startfile(path)
            except Exception as exc:
                failure.append(exc)

    # Daemon so a wedged ShellExecute can never hold up agent shutdown.
    worker = threading.Thread(target=launch, daemon=True)
    worker.start()
    worker.join(timeout=_LAUNCH_BUDGET_SECONDS)
    if failure:
        raise failure[0]


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
        # Through start_process(), not os.startfile() directly, so a tile
        # launch gets the same two guarantees the VPN toggle needs: the
        # launched app is detached from IT-Deck (closing the deck must not
        # close it) and a slow launch cannot blow the 5s command budget.
        # ShellExecute is still reached via start_process()'s fallback, so
        # UAC-elevated exes, .lnk shortcuts and documents launch as before.
        try:
            start_process(params["path"])
        except Exception:
            # fallback_path exists for one real case: the seeded Terminal tile
            # launches `wt.exe`, and Windows Terminal is not present on every
            # Windows 10 machine. Rather than leave those installs with a
            # broken tile, the item can name a second target to try. Optional
            # everywhere -- an item without it behaves exactly as before.
            fallback = params.get("fallback_path")
            if not fallback:
                raise
            start_process(fallback)
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
