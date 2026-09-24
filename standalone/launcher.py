"""Standalone launcher for IT-Deck.

Generates config on first run, starts the backend and the Windows agent as
two separate OS processes, and prints the phone connection URL. No Docker,
no separate home server -- this runs directly on the PC being controlled.

Also the entry point for the frozen single .exe build (see build.ps1):
PyInstaller bundles this one script, and it re-invokes itself (via
sys.executable) with --role backend / --role agent to get two processes out
of one exe -- there's no bundled python.exe to spawn otherwise. Keeping
backend and agent as separate processes (rather than merging them into one)
reuses each side's own working reconnect/dispatch logic untouched and keeps
the existing "close the console window, relaunch" recovery story intact.
"""
import argparse
import json
import os
import queue
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

# The one place the version number lives in code. Until v0.3.1 it lived only
# in git tags and prose, which meant a running install had no way to say what
# it was -- no version in the UI, nothing to compare against for an
# update check, and nothing to put in a bug report. Bump it in the same commit
# as the tag, and keep it equal to the tag minus the leading "v".
ITDECK_VERSION = "0.5.6"

# Where an installed copy looks to find out it is out of date, and where it
# sends the user when it is. An install has no other way to learn this: the
# exe does not phone home, is not packaged by any store, and the phone side
# cannot be stale independently (it is served by this same build).
UPDATE_CHECK_URL = "https://api.github.com/repos/Itegin/it-deck/releases/latest"
RELEASES_PAGE_URL = "https://github.com/Itegin/it-deck/releases/latest"

# Info-window layout constants. PAD is the single outer gutter every block
# uses -- one number, so the window keeps a consistent rhythm.
PAD = 16
# A QR module of 5px puts a 33-module code at ~185px, which scans reliably
# from arm's length on a phone; the quiet zone is required by the spec, not
# decoration -- scanners need the clear border to find the code at all.
QR_MODULE_PX = 5
QR_QUIET_MODULES = 2
# The window raises itself once and then stops being topmost. Long enough to
# be noticed on a busy desktop, short enough that it is not in the way.
TOPMOST_RELEASE_MS = 4000
UPDATE_POLL_MS = 1000
AGENT_STATUS_POLL_MS = 2000

# How long each of the backend and the agent gets to actually be gone after
# terminate(), waited for one at a time rather than out of a shared budget.
# "Gone" is the point of it: until their process handles are signalled they
# still hold python312.dll open inside the unpacked _MEIxxxx directory, and
# that is what decides whether the shutdown stalls (see
# install_session_end_handler). terminate() is TerminateProcess on Windows, so
# this is normally over in milliseconds; the ceiling exists for the case where
# it is not. Two children at 1 s each leaves comfortable margin under
# HungAppTimeout, which is 5 s and is what Windows measures us against.
SESSION_END_CHILD_GRACE = 1.0

# The pause between the teardown finishing and this process exiting, so the
# window procedure gets to return TRUE to Windows first rather than vanishing
# mid-message. This is NOT v0.4.2's mistake repeated: nothing that matters
# happens after this sleep any more -- the backend and the agent are already
# gone by the time it starts -- so being killed during it costs nothing.
SESSION_END_EXIT_DELAY = 0.05

# A log past this size is rolled to <name>.1 when IT-Deck starts, so the three
# logs together stay within a few tens of MB instead of growing for as long as
# the PC does. At launch rather than mid-run: the children hold their files
# open, and Windows will not rename a file another process has open.
LOG_ROTATE_BYTES = 5 * 1024 * 1024


def open_log(path: Path, **kwargs):
    """Open `path` for appending, rolling it to `<name>.1` first if it is big."""
    try:
        if path.stat().st_size > LOG_ROTATE_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        # Missing (first run), or still held by another copy of IT-Deck --
        # which the port check will stop shortly anyway. Append as before.
        pass
    return open(path, "a", encoding="utf-8", **kwargs)


# The frozen exe is built --windowed, so it has NO console: sys.stdout and
# sys.stderr are None and a bare print() would raise AttributeError. They are
# pointed at a log file here instead, before anything prints.
#
# No console is a deliberate architectural choice, not a cosmetic one. A
# console gave IT-Deck a *host process* -- conhost.exe, or WindowsTerminal.exe
# when that is the system default -- and that host is a process the deck can
# be asked to kill: Force Stop on the Terminal tile took the whole of IT-Deck
# down with its target, twice, and an attempt to protect the host by walking
# its ancestors did not fix it (OpenConsole.exe's parent is svchost, not the
# Windows Terminal it belongs to, so the link isn't there to walk). Removing
# the console removes the coupling entirely rather than guarding it. It also
# removes the minimized-console stub that showed up as a stray rectangle on
# the desktop, and the "restore it from the taskbar to press Ctrl+C" story
# that the Quit button had already replaced.
def _redirect_output_to_log() -> None:
    if not is_frozen():
        # A dev run has a real terminal and its output is the point.
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass
        return
    try:
        logs = default_data_dir() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stream = open_log(logs / "launcher.log", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    except Exception:
        # Last resort: swallow writes rather than let a logging failure stop
        # IT-Deck from starting. os.devnull always opens.
        try:
            sys.stdout = sys.stderr = open(os.devnull, "w")
        except Exception:
            pass


def _ps_quote(value) -> str:
    """`value` as a PowerShell single-quoted string literal.

    Inside '...' PowerShell expands nothing and the only special character is
    the quote itself, written twice. Every path this module splices into a
    PowerShell command goes through here: an unescaped one broke the whole
    command for any profile path containing an apostrophe (a user named O'Brien),
    and each caller failed quietly -- no desktop shortcut, firewall rules and
    files left behind by the uninstall.
    """
    return "'" + str(value).replace("'", "''") + "'"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def self_invocation(role: str) -> list[str]:
    # Frozen: sys.executable IS the script (PyInstaller onefile), so no
    # extra path argument. Dev: sys.executable is python.exe, so the script
    # path has to be passed explicitly.
    if is_frozen():
        return [sys.executable, "--role", role]
    return [sys.executable, str(Path(__file__).resolve()), "--role", role]


# --- config: generate once, reuse across restarts -------------------------

CONFIG_FILENAME = "config.env"
# Keys a user might hand-edit for their own hardware -- see
# agents/windows/.env.example for what each one does. Preserved verbatim
# across restarts; never auto-generated (no safe default exists for them).
OPTIONAL_AGENT_KEYS = (
    "OUTPUT_DEVICE_PRIMARY",
    "OUTPUT_DEVICE_SECONDARY",
    "VPN_PROCESS_NAME",
    "VPN_PATH",
)


def default_data_dir() -> Path:
    override = os.environ.get("ITDECK_DATA_DIR")
    if override:
        return Path(override)
    return Path(os.environ["LOCALAPPDATA"]) / "IT-Deck"


# The port a fresh install picks. Deliberately not 8000: that is one of the
# most contested ports on a developer machine (every other dev server, Docker
# Desktop, Portainer, Jenkins, a dozen Python tutorials), and IT-Deck losing a
# coin-flip against one of them is a confusing failure for a desktop app
# nobody expects to be a web server. 49732 sits inside IANA's dynamic/private
# range (49152-65535), which is reserved for exactly this and contains no
# registered services at all -- so no lookup table to keep in sync.
#
# find_free_port() below still walks upward if even this is taken, and
# SERVER_PORT in config.env is hand-editable, so a user who wants a specific
# port has one line to change. Existing installs keep whatever port their
# config.env already holds -- load_or_create_config() is load-if-exists by
# design (CLAUDE.md: never regenerate, or the phone's stored URL breaks), so
# this only affects first runs.
DEFAULT_PORT = 49732

# Agent supervision (see the loop at the end of run_launcher()). The floor is
# a couple of seconds rather than instant so a crash-on-startup can't become a
# busy loop, and so a respawn never races the dying predecessor's singleton
# mutex; the ceiling keeps a machine that recovers later (a USB audio device
# plugged back in) from waiting minutes to notice.
AGENT_RESTART_MIN_DELAY = 2.0
AGENT_RESTART_MAX_DELAY = 30.0
# How long a fresh agent has to stay up before its predecessor's crash stops
# counting against it -- longer than the agent's own reconnect ladder, so an
# agent that is merely failing to reach the backend isn't mistaken for one
# that is up and working.
AGENT_HEALTHY_AFTER = 60.0

# The exit code that means "the agent stopped on purpose" -- never respawned.
# Only exit 0 -- agent_shutdown's os._exit(0), i.e. the Close Agent tile,
# which the user pressed and must stay pressed.
AGENT_DELIBERATE_EXIT_CODES = (0,)

# agent.py's EXIT_ALREADY_RUNNING. This used to sit in the tuple above, and
# that was wrong in the one case that matters most: on an **upgrade**, the
# outgoing install's agent can still hold the singleton mutex when the new
# launcher spawns its own, so the very first spawn exits 3, the supervisor
# reads "stopped on purpose" and never tries again -- leaving a backend, a
# window, three processes and no agent, with nothing on screen saying so.
# Reproduced deliberately: hold the mutex for 20s, kill the agent, and the
# deck stays agent-less long after the mutex is free.
#
# So 3 is retried, but only a few times: if a genuinely separate IT-Deck is
# running, no amount of respawning will ever win that mutex and something has
# to stop. Keep the value non-zero -- agents/windows/start_agent.bat pauses on
# a non-zero exit, which is how the legacy shortcut keeps its "already
# running" message readable.
AGENT_EXIT_ALREADY_RUNNING = 3
AGENT_MUTEX_RETRIES = 5


def find_free_port(preferred: int, attempts: int = 20) -> int:
    port = preferred
    for _ in range(attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                port += 1
    # Give up silently -- uvicorn will fail loudly on bind if this is also taken.
    return preferred


def parse_config(path: Path) -> dict:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def write_config(path: Path, values: dict) -> None:
    lines = [
        "# IT-Deck standalone config -- auto-generated on first run.",
        "# AGENT_TOKEN / CLIENT_TOKEN / SERVER_PORT are generated once and kept",
        "# across restarts. Deleting this file invalidates the URL your phone",
        "# already has stored -- you'd need to open the new printed URL again.",
        "#",
        "# SERVER_PORT is safe to change to any free port you prefer; IT-Deck",
        "# uses whatever is here. Changing it changes the address your phone",
        "# uses, so open the newly printed link on the phone once afterwards.",
        "#",
        "#",
        "# UPDATE_CHECK=0 turns off the one-off check for a newer release that",
        "# runs at startup. It only ever reads the public releases page, and",
        "# failing to reach it is silently ignored.",
        "#",
        "# The keys below are optional and safe to hand-edit for your own",
        "# hardware -- see agents/windows/.env.example in the source repo.",
        "",
        f"AGENT_TOKEN={values['AGENT_TOKEN']}",
        f"CLIENT_TOKEN={values['CLIENT_TOKEN']}",
        f"SERVER_PORT={values['SERVER_PORT']}",
        "",
    ]
    for key in OPTIONAL_AGENT_KEYS:
        if values.get(key):
            lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _looks_auto_generated(token: str) -> bool:
    # What the pre-v0.3.0 launcher produced: secrets.token_hex(16), i.e.
    # exactly 32 lowercase hex characters. Narrow on purpose -- a token a
    # person chose (including "admin") never matches, so the migration below
    # cannot overwrite a deliberate secret.
    return len(token) == 32 and all(c in "0123456789abcdef" for c in token)


def load_or_create_config(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / CONFIG_FILENAME
    values = parse_config(config_path)
    changed = False

    # Migrate the random tokens early installs generated to "admin".
    #
    # This reverses an earlier deliberate decision not to migrate, and the
    # reason is that the decision was wrong in practice: an install created
    # before the "admin" default keeps its random pair forever, and the
    # symptom -- a fresh download on a new PC showing 32-hex tokens in its
    # window -- reads as a bug every single time. The stated requirement is
    # "admin everywhere".
    #
    # Scoped tightly: only values matching the exact shape the old generator
    # produced (see _looks_auto_generated). A hand-picked secret in
    # config.env survives untouched, which is the whole point of that file
    # being editable.
    #
    # SERVER_PORT is deliberately NOT migrated alongside it. Moving the port
    # changes the origin and orphans whatever the phone has in localStorage,
    # on top of the token change -- two breakages where the request was for
    # one. An old install stays on its old port until config.env is deleted.
    for key in ("AGENT_TOKEN", "CLIENT_TOKEN"):
        if _looks_auto_generated(values.get(key, "")):
            values[key] = "admin"
            changed = True

    if not values.get("AGENT_TOKEN"):
        # Same reasoning as CLIENT_TOKEN below: on a self-hosted single-PC
        # LAN install, a random secret here buys little real security (it
        # gates the write endpoints and /ws/agent, but the whole trust
        # model is already "shared secret, no identity/expiry/rate-limit" --
        # see CLAUDE.md) and is one more thing a person has to copy
        # correctly, this time from Studio's own token prompt. Fixed and
        # overridable in config.env, same as CLIENT_TOKEN.
        values["AGENT_TOKEN"] = "admin"
        changed = True
    if not values.get("CLIENT_TOKEN"):
        # Not random on purpose: this is the one thing a person has to type
        # on their phone, and on a self-hosted LAN it buys little real
        # security anyway (CLAUDE.md's own auth model is "shared secret,
        # no identity/expiry/rate-limit"). A fixed, memorable default is
        # what actually gets a new install used; anyone who wants a real
        # secret can still set CLIENT_TOKEN by hand in config.env.
        values["CLIENT_TOKEN"] = "admin"
        changed = True
    if not values.get("SERVER_PORT"):
        values["SERVER_PORT"] = str(find_free_port(DEFAULT_PORT))
        changed = True
    if changed:
        write_config(config_path, values)
    return values


# --- LAN discovery / reachability ------------------------------------------


# Adapter names that are virtual by construction. Only ever used to break a
# tie -- the address itself decides first (see _rank_address), because a name
# list can never be complete and the user's own VPN client is not on it.
_VIRTUAL_ADAPTER_HINTS = (
    "veth",
    "virtual",
    "vmware",
    "virtualbox",
    "hyper-v",
    "loopback",
    "tap-",
    "tun",
    "wireguard",
    "tailscale",
    "radmin",
    "hamachi",
    "zerotier",
    "vpn",
)


def _rank_address(name: str, address: str, netmask: Optional[str]) -> tuple:
    """Sort key for "which of this PC's addresses can a phone actually reach".

    Lower sorts better. The signals, in the order they matter:

    1. **Is it a private LAN address at all.** A home network is RFC1918.
       This is what rejects Radmin VPN's 26.x.x.x, which is public IANA space
       borrowed by a virtual-LAN product.
    2. **How big is the subnet.** A real LAN is a /24 or wider; a point-to-
       point tunnel is a /30 or /32. Measured on the maintainer's machine, the
       v2RayTun adapter is 172.16.0.1/30 -- one bit of information that
       separates it from any LAN, with no name matching involved.
    3. **Which private range.** 192.168/16 is what essentially every consumer
       router hands out, 10/8 next, 172.16/12 last -- the one Hyper-V and
       tunnels like to squat in.
    4. **Does the adapter name look virtual.** A hint, and deliberately last.
    """
    octets = [int(part) for part in address.split(".")]
    if octets[0] == 192 and octets[1] == 168:
        family = 0
    elif octets[0] == 10:
        family = 1
    elif octets[0] == 172 and 16 <= octets[1] <= 31:
        family = 2
    else:
        family = 9  # not RFC1918 -- a phone on the Wi-Fi will not reach this

    width = 0
    if netmask:
        try:
            bits = sum(bin(int(part)).count("1") for part in netmask.split("."))
            # /24 or wider is a plausible LAN; anything narrower is a tunnel.
            width = 0 if bits <= 24 else 1
        except ValueError:
            width = 0

    lowered = name.lower()
    virtual = 1 if any(hint in lowered for hint in _VIRTUAL_ADAPTER_HINTS) else 0
    return (1 if family == 9 else 0, width, family, virtual, address)


def detect_primary_and_other_ips() -> tuple[Optional[str], list[str]]:
    """This PC's most phone-reachable address, then all the others.

    This used to be the UDP-connect-to-8.8.8.8 trick alone: ask the OS which
    source address it would route an external packet from. That answer is
    exactly the default route -- which is wrong for this app specifically,
    because IT-Deck ships a VPN tile and **a running VPN owns the default
    route**. Measured here: with v2RayTun up, the trick returned 172.16.0.1
    (the tunnel's own /30) while the phone-reachable address was 192.168.0.15.
    The window now puts that address in a QR code, so picking the wrong one
    stopped being a cosmetic wart and became "the code doesn't work".

    So every address is enumerated and ranked (see _rank_address), and the
    UDP answer is kept only as a tie-breaking hint and a fallback for when
    enumeration fails.
    """
    routed = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            routed = s.getsockname()[0]
    except OSError:
        pass

    candidates = []
    try:
        import psutil

        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            interface = stats.get(name)
            if interface is not None and not interface.isup:
                continue
            for addr in addrs:
                if addr.family != socket.AF_INET:
                    continue
                if addr.address.startswith("127.") or addr.address.startswith("169.254."):
                    continue
                candidates.append((_rank_address(name, addr.address, addr.netmask), addr.address))
    except Exception:
        pass

    if not candidates:
        # Enumeration unavailable: fall back to exactly the old behaviour.
        return routed, []

    candidates.sort()
    ordered = []
    for _, address in candidates:
        if address not in ordered:
            ordered.append(address)

    primary = ordered[0]
    # Only when the routed address ranks equally well does the OS's own
    # opinion win -- it is the better tie-break between two real LAN
    # adapters (a laptop on Wi-Fi and Ethernet at once).
    if routed in ordered and routed != primary:
        best = candidates[0][0][:-1]
        routed_rank = next(rank[:-1] for rank, address in candidates if address == routed)
        if routed_rank == best:
            primary = routed
    return primary, [address for address in ordered if address != primary]


def watched_process_name(data_dir: Path) -> Optional[str]:
    """The process name the agent's poller should report as `vpn.running`.

    Read straight out of the item table, because the agent cannot get it any
    other way on a standalone install and the consequence of it being unknown
    is genuinely destructive, not cosmetic:

    `process_toggle` is a TOGGLE. The agent only learns the process name from
    an item's params when a press arrives, so on a freshly started agent
    `vpn.running` reports false while the VPN is actually up. The tile renders
    "off", the user taps it expecting "on", and the handler -- seeing the
    process genuinely running -- kills the VPN instead. Every agent start
    re-armed that trap, which is why it looked like "the VPN closes when the
    agent restarts". Seeding the name here means the very first poll tick
    reports the truth and the tile is already lit before anyone touches it.

    Done as an env var rather than a new WebSocket config frame: the agent's
    configuration already travels this way (see agent_env in run_launcher),
    the agent and its poller read VPN_PROCESS_NAME today with no changes at
    all, and the legacy Docker path is untouched because it sets the variable
    itself -- load_or_create_config()'s value always wins over this.

    Returns None when there's nothing to seed (no such item, no process_name
    in its params yet, unreadable DB). A None is the previous behaviour, not
    a failure: the name still arrives on first press.
    """
    db_path = data_dir / "controlhub.db"
    if not db_path.exists():
        return None
    try:
        # Plain sqlite3 against a known path rather than importing
        # backend.app.db: that module computes DB_PATH at import time from
        # ITDECK_DATA_DIR, which the launcher role deliberately doesn't set
        # on itself (only on the backend child). One read-only query is not
        # worth coupling this to that import order.
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            # Keyed on state_key, not on type. `vpn.running` IS the thing the
            # poller reports and the tile lights from, and the tile driving it
            # can legitimately be either a `launch_app` (what the reference
            # deck uses) or a `process_toggle`. Selecting by type missed the
            # launch_app case entirely, which is every fresh install since the
            # seed changed.
            rows = conn.execute(
                "SELECT params FROM item WHERE state_key = 'vpn.running' ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None

    # One name, not a list: everything here reports through the single state
    # key `vpn.running`, so the state model has only ever had room for one
    # watched process.
    for (params,) in rows:
        try:
            values = json.loads(params)
        except (TypeError, ValueError):
            continue
        # An explicit process_name wins; otherwise derive it from the launch
        # path, which is all a launch_app tile carries. Deriving is safe here
        # in a way it isn't for the Terminal tile (whose `wt.exe` is an alias
        # for a differently-named process) because a VPN client's exe and its
        # process share a name.
        name = values.get("process_name")
        if not name and values.get("path"):
            name = os.path.basename(values["path"])
        if name:
            return name
    return None


def port_already_serving(port: int, timeout: float = 1.0) -> bool:
    """True if something is already answering IT-Deck's /health on this port.

    Asked *before* the backend is spawned, because afterwards it cannot be
    answered at all: an orphaned backend from a previous run answers /health
    exactly like a fresh one, byte for byte, and there is nothing in the
    reply to tell them apart.

    That ambiguity used to end the run in a way nobody could read. With an
    orphan holding the port, the new backend loses the bind and exits,
    wait_for_health() gets its 200 from the orphan and returns True, the
    launcher carries on, the agent loses the singleton mutex to the orphaned
    agent, and one second later the supervisor notices backend_proc is gone
    and prints "Backend process exited -- stopping". IT-Deck vanishes a few
    seconds after launch, and the only explanation is in a log file the user
    has no reason to open.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_for_health(port: int, timeout: float = 20.0,
                    proc: Optional[subprocess.Popen] = None) -> bool:
    """Wait for the backend to answer, and give up the moment it dies.

    Watching `proc` is what turns a 20-second wait for a backend that is
    never coming into a one-second one. Anything fatal in the backend --
    a bad config, a port lost between the pre-flight check and the bind, an
    import error in a fresh build -- exits the process rather than hanging,
    so its death is the earliest and most reliable signal available.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def check_reachable(ip: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# --- role: backend -----------------------------------------------------


def _line_buffer_stdio() -> None:
    """Make this role's stdout/stderr flush per line.

    The launcher hands each child a *file* for stdout, and Python
    block-buffers a non-tty stream -- so the last thing written before a
    problem is precisely what is still sitting in the buffer. Measured while
    debugging a live install: agent.log's tail was minutes behind the agent's
    actual state, which makes the log useless for the one job it has. The
    window points users at these files by name, so they have to be current.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except Exception:
            pass  # a frozen build can hand us something that isn't a TextIO


def run_backend() -> int:
    _line_buffer_stdio()
    if not is_frozen():
        sys.path.insert(0, str(REPO_ROOT / "backend"))
    os.environ.setdefault("ITDECK_FRONTEND_DIR", str(frontend_dir()))

    import uvicorn
    from app.config import SERVER_PORT
    from app.main import app

    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT, log_level="info")
    return 0


# --- role: agent -----------------------------------------------------


def run_agent() -> int:
    _line_buffer_stdio()
    if not is_frozen():
        sys.path.insert(0, str(REPO_ROOT / "agents" / "windows"))

    import asyncio

    import agent

    asyncio.run(agent.main())
    return 0


# --- desktop shortcut -------------------------------------------------


def ensure_desktop_shortcut() -> None:
    # Only meaningful for the built exe -- a dev `python launcher.py` run
    # has no sensible target to shortcut. Mirrors the pattern
    # agents/windows/start_agent.bat already uses for its own shortcut
    # (same WScript.Shell technique, same idempotency check), so the
    # running program gives itself a desktop icon exactly like the agent
    # does -- works whether this exe was built locally or just downloaded,
    # since it happens on first launch rather than at build time.
    if not is_frozen():
        return
    try:
        shortcut_path = Path(os.environ["USERPROFILE"]) / "Desktop" / "IT-Deck.lnk"
        if shortcut_path.exists():
            return
        exe_path = sys.executable
        ps_command = (
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(shortcut_path)}); "
            f"$s.TargetPath = {_ps_quote(exe_path)}; "
            f"$s.WorkingDirectory = {_ps_quote(Path(exe_path).parent)}; "
            f"$s.IconLocation = {_ps_quote(exe_path)}; "
            f"$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_command],
            capture_output=True,
            timeout=10,
            # The exe is built --windowed, so this child would otherwise
            # allocate and flash its own console window on the desktop during
            # first launch.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass  # convenience only -- never let this block IT-Deck from starting


# --- info window ---------------------------------------------------------

# This window is dressed as Studio: the same dark Liquid Glass tokens, copied
# here verbatim from the CSS and composited into the opaque colours Tk can
# paint. tests/test_launcher.py checks every value below against
# frontend/css/{base,themes,studio}.css, so a palette change in Studio fails
# CI until this window follows it. No new colours are invented here.
#
# Tk has no blur, so the frost itself can't be reproduced: a panel is the
# panel token plus the veil's middle stop, flattened over the ground. The
# rounded panels and buttons are drawn as images (see _rounded_png).
_STUDIO_TOKENS = {
    # themes.css, [data-theme="liquid-glass"] dark
    "--color-bg": "#070a11",
    "--glass-veil": "rgba(255, 255, 255, 0.06)",  # the veil's 42% stop
    "--glass-edge": "rgba(255, 255, 255, 0.42)",  # its top highlight
    # base.css, :root
    "--color-text": "#F1F5F9",
    "--color-text-muted": "#9aa3b2",
    "--color-accent": "#a78bfa",
    "--color-active": "#0d9488",
    "--color-alert-text": "#e85757",
    # studio.css, dark
    "--studio-panel": "rgba(21, 26, 35, 0.62)",
    "--studio-well": "rgba(0, 0, 0, 0.28)",
    "--studio-card": "rgba(255, 255, 255, 0.04)",
    "--studio-card-hover": "rgba(255, 255, 255, 0.08)",
    "--studio-hairline": "rgba(255, 255, 255, 0.10)",
    "--studio-accent-wash": "rgba(167, 139, 250, 0.12)",
}

# studio.css's .btn-primary ink: dark text on the accent (7.0:1).
_ACCENT_INK = "#1A1F26"


def _css_rgba(value: str) -> tuple:
    """"#rrggbb" or "rgba(r, g, b, a)" as (r, g, b, a), a in 0..1."""
    value = value.strip()
    if value.startswith("#"):
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 1.0)
    parts = [p.strip() for p in value[value.index("(") + 1 : value.rindex(")")].split(",")]
    return (int(parts[0]), int(parts[1]), int(parts[2]), float(parts[3]) if len(parts) > 3 else 1.0)


def _over(top: str, bottom: str) -> str:
    """`top` (any CSS colour) painted over the opaque `bottom`, as #rrggbb."""
    r, g, b, a = _css_rgba(top)
    br, bg, bb, _ = _css_rgba(bottom)
    mix = [round(c * a + d * (1 - a)) for c, d in ((r, br), (g, bg), (b, bb))]
    return "#{:02x}{:02x}{:02x}".format(*mix)


def _brighten(color: str, factor: float) -> str:
    """CSS filter: brightness(factor) -- .btn-primary:hover."""
    r, g, b, _ = _css_rgba(color)
    return "#{:02x}{:02x}{:02x}".format(*(min(255, round(c * factor)) for c in (r, g, b)))


def _studio_palette(t: dict) -> dict:
    bg = t["--color-bg"]
    # A Studio panel, flattened: what a person sees behind a card's text.
    surface = _over(t["--glass-veil"], _over(t["--studio-panel"], bg))
    return {
        "bg": bg,
        "surface": surface,
        # The panel's containing hairline and its brighter top edge.
        "border": _over(t["--studio-hairline"], surface),
        "edge": _over(t["--glass-edge"], surface),
        # A line drawn straight on the window ground, not on a panel.
        "rule": _over(t["--studio-hairline"], bg),
        "text": t["--color-text"],
        "text_muted": t["--color-text-muted"],
        "accent": t["--color-accent"],
        "accent_active": _brighten(t["--color-accent"], 1.08),
        "accent_ink": _ACCENT_INK,
        "accent_wash": _over(t["--studio-accent-wash"], surface),
        # .btn on a panel, and its hover.
        "surface_raised": _over(t["--studio-card"], surface),
        "surface_hover": _over(t["--studio-card-hover"], surface),
        # .input: a well sunk into the panel.
        "well": _over(t["--studio-well"], surface),
        # Studio's "done" teal: the running dot.
        "ok": t["--color-active"],
        # The one colour Studio has no token for: "the agent is down" is a
        # warning, not a destruction, so it must not borrow the alert red.
        "warn": "#f59e0b",
        # .btn-danger: alert-coloured text, never a red fill.
        "danger": t["--color-alert-text"],
    }


_GLASS = _studio_palette(_STUDIO_TOKENS)

# Corner radii, from studio.css: a panel (.glass, 20px) is scaled down with
# this much smaller window; a button or field is Studio's 10px exactly.
_RADIUS_PANEL = 16
_RADIUS_CONTROL = 10
_RADIUS_BADGE = 7
# Room around a button for its focus ring: 2px of ring, 1px of gap.
_FOCUS_MARGIN = 3


def _rounded_image(
    width: int,
    height: int,
    radius: int,
    fill: str,
    outline: "Optional[str]" = None,
    edge: "Optional[str]" = None,
    ring: "Optional[str]" = None,
    ring_width: int = 2,
    margin: int = 0,
):
    """A rounded rectangle as a Pillow RGBA image, antialiased, transparent outside.

    Drawn four times too big and scaled down: Pillow's own rounded_rectangle
    has hard edges, and so does anything Tk draws on a canvas.
    - outline: a 1-px hairline (Studio's --studio-hairline ring);
    - edge: a 1-px brighter line along the top, the glass's specular edge;
    - ring: a focus ring `margin` px outside the shape (Studio's
      outline: 2px + offset 2px), so a focused and a plain button are the
      same size and nothing jumps.
    """
    from PIL import Image, ImageDraw

    k = 4
    w, h, r, m = width * k, height * k, radius * k, margin * k
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (m, m, w - m - 1, h - m - 1)
    if ring:
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=r + m, fill=ring)
        inner = max(0, m - ring_width * k)
        draw.rounded_rectangle(
            (m - inner, m - inner, w - 1 - (m - inner), h - 1 - (m - inner)),
            radius=r + inner,
            fill=(0, 0, 0, 0),
        )
    line = k  # 1 px once scaled down
    if edge:
        draw.rounded_rectangle(box, radius=r, fill=edge)
    if outline:
        top = box[1] + (line if edge else 0)
        draw.rounded_rectangle((box[0], top, box[2], box[3]), radius=r, fill=outline)
        box = (box[0] + line, box[1] + line, box[2] - line, box[3] - line)
        r = max(0, r - line)
    elif edge:
        box = (box[0], box[1] + line, box[2], box[3])
    draw.rounded_rectangle(box, radius=r, fill=fill)
    return image.resize((width, height), Image.LANCZOS)


def _png(image) -> bytes:
    """PNG bytes, so Tk 8.6 can load the image with PhotoImage(data=...)
    without Pillow's ImageTk bridge. Fastest compression: the bytes go
    straight to Tk in memory, and a smaller file saves nothing."""
    import io

    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=1)
    return out.getvalue()


def _nine_slice(image, border: int, width: int, height: int):
    """`image` stretched to width x height with its `border`-px corners kept
    as they are -- what ttk does with an image element, done once in Pillow.

    A big panel then costs one small antialiased drawing instead of one at
    four times its full size.
    """
    from PIL import Image

    iw, ih = image.size
    b = border
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    xs = [(0, b, 0, b), (b, iw - b, b, width - b), (iw - b, iw, width - b, width)]
    ys = [(0, b, 0, b), (b, ih - b, b, height - b), (ih - b, ih, height - b, height)]
    for sx0, sx1, dx0, dx1 in xs:
        for sy0, sy1, dy0, dy1 in ys:
            if dx1 > dx0 and dy1 > dy0:
                piece = image.crop((sx0, sy0, sx1, sy1)).resize((dx1 - dx0, dy1 - dy0), Image.NEAREST)
                out.paste(piece, (dx0, dy0))
    return out


def _rounded_png(width: int, height: int, radius: int, fill: str, **kwargs) -> bytes:
    return _png(_rounded_image(width, height, radius, fill, **kwargs))


# Studio's ground: the two soft pools themes.css lays over the dark Liquid
# Glass page, copied verbatim (tests/test_launcher.py finds each one in the
# CSS). The first is painted on top, as in CSS.
_STUDIO_POOLS = (
    "radial-gradient(70% 55% at 18% 8%, rgba(142, 95, 245, 0.20) 0%, rgba(142, 95, 245, 0) 100%)",
    "radial-gradient(60% 50% at 88% 82%, rgba(13, 148, 136, 0.16) 0%, rgba(13, 148, 136, 0) 100%)",
)
_POOL_RE = re.compile(
    r"radial-gradient\((\d+)% (\d+)% at (\d+)% (\d+)%, (rgba\([^)]*\)) 0%, rgba\([^)]*\) 100%\)"
)
# Parsed once: (rx%, ry%, cx%, cy%, (r, g, b, a)), painted bottom first.
_POOLS = tuple(
    (int(m[1]), int(m[2]), int(m[3]), int(m[4]), _css_rgba(m[5]))
    for m in (_POOL_RE.fullmatch(css) for css in reversed(_STUDIO_POOLS))
)


def _ground_image(width: int, height: int):
    """Studio's page background at this size: --color-bg and the two pools.

    Each pool is an ellipse whose colour fades linearly from its centre
    alpha to nothing at its edge -- what CSS does for a two-stop
    radial-gradient with an explicit size. Drawn at 1x: a gradient has no
    edge to antialias.
    """
    from PIL import Image

    ground = Image.new("RGB", (width, height), _GLASS["bg"])
    for rx_pct, ry_pct, cx_pct, cy_pct, (r, g, b, a) in _POOLS:
        rx = max(1, round(width * rx_pct / 100))
        ry = max(1, round(height * ry_pct / 100))
        cx = round(width * cx_pct / 100)
        cy = round(height * cy_pct / 100)
        # Pillow's radial_gradient is 0 at the centre of its 256px square and
        # 255 at the *corner* (radius ~181), so x sqrt(2) makes it reach 255
        # at radius 128, the inscribed circle -- then clamped. Inverted and
        # scaled by the pool's alpha; stretched to (2rx, 2ry), the ellipse.
        # Without the rescale the pool would stop at its box with a visible
        # edge instead of fading out.
        falloff = Image.radial_gradient("L").resize((2 * rx, 2 * ry), Image.BILINEAR)
        mask = falloff.point(lambda v: round((255 - min(255, v * 1.41421356)) * a))
        ground.paste((r, g, b), (cx - rx, cy - ry, cx + rx, cy + ry), mask)
    return ground


def _ground_png(width: int, height: int, card: tuple, radius: int, fill: str, outline: str, edge: str) -> bytes:
    """The ground with one panel baked into it at `card` (x, y, w, h).

    Baked rather than layered: a ttk panel fills its corners with one flat
    colour, which on a gradient shows as a square around the curve.
    """
    ground = _ground_image(width, height).convert("RGBA")
    x, y, w, h = card
    side = 3 * radius  # corners plus a straight run of edge to stretch
    tile = _rounded_image(side, side, radius, fill, outline=outline, edge=edge)
    ground.alpha_composite(_nine_slice(tile, radius, w, h), (x, y))
    return _png(ground.convert("RGB"))


_STRINGS = {
    "en": {
        "title": "IT-Deck",
        "running": "IT-Deck is running",
        "agent_down": "the agent is not running -- tiles that control this PC won't work",
        "start_agent": "Start the agent",
        "agent_starting": "Starting…",
        "tour_again": "Tutorial",
        "tour_skip": "Skip",
        "tour_back": "Back",
        "tour_next": "Next",
        "tour_done": "Show me the QR code",
        "tour_step": "Step {n} of {total}",
        "tour1_title": "Welcome to IT-Deck",
        "tour1_body": "Your phone becomes a control panel for this PC: launch programs and websites, mute the mic, switch the audio output, take screenshots. Setting it up takes about a minute.",
        "tour2_title": "Connect the phone",
        "tour2_body": "Put the phone on the same Wi-Fi as this PC. On the next screen, point its camera at the QR code and open the link. That's the only step you have to do.",
        "tour3_title": "Put it on the Home Screen",
        "tour3_body": "In Safari tap Share, then “Add to Home Screen” -- the deck then opens full screen, like an app. The first time the icon opens it asks for a token: type {token}.",
        "tour4_title": "Make it yours in Studio",
        "tour4_body": "Studio is the editor on this PC: add tiles, pick logos, fill the quick-launch bar with your sites and programs. It has a built-in guide. Open it any time with “Open Studio” in step 2.",
        "step1_title": "Open the deck on your phone",
        "step1_body": "Point your phone's camera at the code. The phone has to be on the same Wi-Fi as this PC.",
        "step1_body_no_qr": "Open this address on your phone. It has to be on the same Wi-Fi as this PC.",
        "copy": "Copy link",
        "copied": "Copied",
        "other_ips": "Doesn't open? This PC is also reachable at:",
        "step2_title": "Set up your tiles (optional)",
        "step2_body": "Every tile works out of the box except VPN: it doesn't know which program to launch yet. Open Studio on this PC and give it the path to your VPN client.",
        "open_studio": "Open Studio",
        "show_token": "Show agent token",
        "new_pin": "New phone PIN",
        "new_pin_confirm": "Replace the phone token with a new random PIN?\n\nEvery connected phone will ask for the new PIN once. You can also change both tokens in Studio \u2192 Access.",
        "new_pin_done": "New PIN: {pin} \u2014 the link and QR code above are updated.",
        "new_pin_failed": "Couldn't change it: {error}",
        "autostart": "Start IT-Deck with Windows",
        "autostart_failed": "Couldn't change the Windows start-up setting: {error}",
        "whats_new_title": "What's new in IT-Deck {version}",
        "whats_new_ok": "Got it",
        "whats_new_all": "All changes",
        "token_hint": "Studio asks for this once:",
        "copy_token": "Copy",
        "step3_title": "When you're done here",
        "step3_body": "Minimize keeps IT-Deck running in the background -- your phone stays connected. Quit stops it, and the deck on your phone goes offline.",
        "logs_hint": "Logs:",
        "close": "Minimize",
        "quit": "Quit IT-Deck",
        "quit_confirm": "Stop IT-Deck? The deck on your phone will go offline.",
        "update_available": "Version {version} is available",
        "update_download": "Download",
        "port_busy": (
            "IT-Deck is already running on port {port} -- or another program is using it.\n\n"
            "Quit the running IT-Deck from its window (or end ITDeck.exe in Task Manager), "
            "then start this one again."
        ),
        "backend_failed": "IT-Deck could not start its server.\n\nDetails are in:\n{log}",
        "uninstall": "Remove IT-Deck from this PC",
        "uninstall_title": "Remove IT-Deck from this PC",
        "uninstall_body": (
            "This deletes, leaving nothing behind:\n"
            "   •  ITDeck.exe itself\n"
            "   •  the Desktop shortcut\n"
            "   •  settings, both tokens and your tile layout\n"
            "   •  its Windows Firewall rules\n"
            "   •  starting with Windows, if it was turned on"
        ),
        "uninstall_warning": "The deck on your phone stops working. This cannot be undone.",
        "uninstall_uac": (
            "Windows will ask for permission once — only to remove the firewall "
            "rules. Refusing it still removes everything else."
        ),
        "uninstall_phone": (
            "The home-screen icon on your phone stays where it is; remove that one "
            "on the phone."
        ),
        "uninstall_go": "Remove IT-Deck",
        "uninstall_busy": "Removing IT-Deck…",
        "cancel": "Cancel",
    },
    "ru": {
        "title": "IT-Deck",
        "running": "IT-Deck работает",
        "agent_down": "агент не запущен — плитки, которые управляют этим ПК, не сработают",
        "start_agent": "Запустить агент",
        "agent_starting": "Запускаю…",
        "tour_again": "Обучение",
        "tour_skip": "Пропустить",
        "tour_back": "Назад",
        "tour_next": "Далее",
        "tour_done": "Показать QR-код",
        "tour_step": "Шаг {n} из {total}",
        "tour1_title": "Добро пожаловать в IT-Deck",
        "tour1_body": "Телефон станет пультом для этого ПК: запускать программы и сайты, выключать микрофон, переключать звук, делать скриншоты. Настройка займёт около минуты.",
        "tour2_title": "Подключи телефон",
        "tour2_body": "Подключи телефон к той же Wi-Fi, что и этот ПК. На следующем экране наведи камеру на QR-код и открой ссылку. Это единственный обязательный шаг.",
        "tour3_title": "Добавь на экран «Домой»",
        "tour3_body": "В Safari нажми «Поделиться», затем «На экран „Домой“» — дека будет открываться на весь экран, как приложение. При первом запуске иконка спросит токен: введи {token}.",
        "tour4_title": "Настрой под себя в Studio",
        "tour4_body": "Studio — редактор на этом ПК: добавляй плитки, выбирай логотипы, собери панель быстрого запуска из своих сайтов и программ. Внутри есть гайд. Открывается кнопкой «Открыть Studio» в шаге 2.",
        "step1_title": "Открой деку на телефоне",
        "step1_body": "Наведи камеру телефона на код. Телефон должен быть в той же сети Wi-Fi, что и этот компьютер.",
        "step1_body_no_qr": "Открой этот адрес на телефоне. Он должен быть в той же сети Wi-Fi, что и этот компьютер.",
        "copy": "Скопировать ссылку",
        "copied": "Скопировано",
        "other_ips": "Не открывается? Этот ПК доступен ещё по адресам:",
        "step2_title": "Настрой плитки (не обязательно)",
        "step2_body": "Все плитки работают сразу, кроме VPN: она пока не знает, какую программу запускать. Открой Studio на этом ПК и укажи путь до своего VPN-клиента.",
        "open_studio": "Открыть Studio",
        "show_token": "Показать токен агента",
        "new_pin": "Новый PIN для телефона",
        "new_pin_confirm": "Заменить токен телефона на новый случайный PIN?\n\nКаждый подключённый телефон один раз попросит новый PIN. Оба токена можно поменять и в Studio \u2192 Доступ.",
        "new_pin_done": "Новый PIN: {pin} \u2014 ссылка и QR-код выше обновлены.",
        "new_pin_failed": "Не получилось: {error}",
        "autostart": "Запускать IT-Deck вместе с Windows",
        "autostart_failed": "Не получилось изменить автозапуск: {error}",
        "whats_new_title": "Что нового в IT-Deck {version}",
        "whats_new_ok": "Понятно",
        "whats_new_all": "Все изменения",
        "token_hint": "Studio спросит его один раз:",
        "copy_token": "Копировать",
        "step3_title": "Когда всё готово",
        "step3_body": "«Свернуть» — IT-Deck продолжит работать в фоне, телефон останется подключён. «Выйти» — остановит его, и дека на телефоне отключится.",
        "logs_hint": "Логи:",
        "close": "Свернуть",
        "quit": "Выйти из IT-Deck",
        "quit_confirm": "Остановить IT-Deck? Дека на телефоне отключится.",
        "update_available": "Доступна версия {version}",
        "update_download": "Скачать",
        "port_busy": (
            "IT-Deck уже запущен на порту {port} — или порт занят другой программой.\n\n"
            "Закрой работающий IT-Deck кнопкой «Выйти» в его окне (или заверши ITDeck.exe "
            "в диспетчере задач) и запусти этот снова."
        ),
        "backend_failed": "IT-Deck не смог запустить свой сервер.\n\nПодробности:\n{log}",
        "uninstall": "Удалить IT-Deck с этого ПК",
        "uninstall_title": "Удалить IT-Deck с этого ПК",
        "uninstall_body": (
            "Будет удалено без следа:\n"
            "   •  сам ITDeck.exe\n"
            "   •  ярлык на рабочем столе\n"
            "   •  настройки, оба токена и раскладка плиток\n"
            "   •  правила брандмауэра Windows для него\n"
            "   •  автозапуск с Windows, если он был включён"
        ),
        "uninstall_warning": "Дека на телефоне перестанет работать. Отменить это нельзя.",
        "uninstall_uac": (
            "Windows один раз спросит разрешение — оно нужно только для правил "
            "брандмауэра. Если отказаться, всё остальное всё равно удалится."
        ),
        "uninstall_phone": (
            "Иконка на домашнем экране телефона останется — её удали на самом "
            "телефоне."
        ),
        "uninstall_go": "Удалить IT-Deck",
        "uninstall_busy": "Удаляю IT-Deck…",
        "cancel": "Отмена",
    },
}


def _detect_ui_lang() -> str:
    # Two independent signals, either sufficient: Windows' own UI language
    # (most reliable when it's available) and Python's locale as a
    # fallback for a dev run or an unusual Windows configuration. Anything
    # that isn't Russian falls back to English rather than guessing.
    try:
        import ctypes

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        if (lang_id & 0xFF) == 0x19:  # LANG_RUSSIAN primary language ID
            return "ru"
    except Exception:
        pass
    try:
        import locale

        loc = locale.getdefaultlocale()[0] or ""
        if loc.lower().startswith("ru"):
            return "ru"
    except Exception:
        pass
    return "en"


def report_startup_failure(message: str) -> None:
    """Say why IT-Deck is not starting, somewhere the user will actually see.

    Every other message this module prints goes to launcher.log, which is
    correct for a running install and useless for one that never got that
    far: the exe is built --windowed, so there is no console, and the info
    window -- the only UI IT-Deck has -- is precisely what these failures
    happen instead of. Without this, double-clicking ITDeck.exe and having
    nothing whatsoever appear is the entire user-visible behaviour.

    A native MessageBox rather than a Tk window: there is nothing to keep
    alive afterwards, it cannot fail for the same reason the real window
    might, and the process exits as soon as it is dismissed. Startup only --
    a modal dialog during shutdown is the bug 10.8a exists to fix.
    """
    print(message, flush=True)
    if not is_frozen() or os.name != "nt":
        return
    try:
        import ctypes

        MB_OK = 0x0
        MB_ICONERROR = 0x10
        MB_SETFOREGROUND = 0x10000
        MB_TOPMOST = 0x40000
        ctypes.windll.user32.MessageBoxW(
            None, message, "IT-Deck", MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST
        )
    except Exception:
        pass  # the print above is still in launcher.log


def _apply_windows11_chrome(root) -> None:
    # Best-effort only: a dark title bar. The Mica backdrop
    # (DWMWA_SYSTEMBACKDROP_TYPE = DWMSBT_MAINWINDOW) that used to be applied
    # here as well is gone -- it is the prime suspect for the stray white
    # rectangle reported on the desktop during real use. Mica asks DWM to
    # composite a translucent material *behind* the window's client area,
    # which assumes the window itself leaves something for it to show
    # through; Tk paints an ordinary opaque client area and has no idea the
    # backdrop exists, so the two disagree about who owns those pixels and
    # DWM can be left holding an uncomposited region -- which is what a bare
    # white rectangle with no visible contents actually is.
    #
    # Nothing is lost that a user can see: tkinter can't do the Liquid Glass
    # theme's real backdrop blur anyway (that's why _GLASS above borrows only
    # the theme's colors), so the window still looks the same minus an effect
    # that was subtle when it worked and a desktop artifact when it didn't.
    # A correct Mica implementation needs DwmExtendFrameIntoClientArea plus a
    # transparent client brush -- a real project, not a two-line ctypes call.
    #
    # The remaining call is silently harmless on Windows 10 or anything older
    # (DwmSetWindowAttribute just returns a failure HRESULT this code doesn't
    # check) -- ctypes never raises on a merely-unsupported attribute, only on
    # a genuinely broken call, which
    # the try/except still catches.
    try:
        import ctypes

        # The native HWND isn't guaranteed to exist yet purely from Tk()
        # having been constructed -- forcing pending geometry/creation work
        # through first is what made this reliably succeed in isolated
        # testing; skipping it left DwmSetWindowAttribute silently
        # targeting a not-yet-realized window.
        root.update_idletasks()

        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass


def hide_console() -> None:
    """Take the console off the desktop once startup is done.

    SW_HIDE, not SW_MINIMIZE. The minimize left a small grey-white rectangle
    sitting above the taskbar -- reported with a screenshot, and it is the
    classic stub Windows draws for a minimized window that has no taskbar
    button to shrink into. Nothing about it was fixable by tidying the repaint
    (an earlier MoveWindow removal, already gone, did not help): a minimized
    window has to go *somewhere*, and for this one that somewhere is the
    desktop.

    Hiding it removes Ctrl+C as the way to stop IT-Deck, so the info window
    grew a Quit button in the same change -- see show_info_window(). That is
    the better affordance anyway: a console the user was told to restore from
    the taskbar in order to press Ctrl+C was never a real stop button.

    Kept, but it no longer fires on the frozen build: that is now compiled
    --windowed, so there is no console and GetConsoleWindow() returns 0. It
    stays because it costs nothing and would matter again if the build ever
    went back to --console. A dev run's terminal is the user's own to manage.
    """
    if not is_frozen():
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return
        SW_HIDE = 0
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
    except Exception:
        pass  # convenience only -- never let this block IT-Deck from running


# What a directory is renamed to once the sweep has proven nothing has it
# open. A rename this file does not recognise on the next start would be
# swept as an ordinary _MEI* directory, so the suffix is checked for.
STALE_UNPACK_SUFFIX = ".itdeck-stale"


def sweep_stale_unpack_dirs() -> None:
    """Delete _MEIxxxx directories that no longer belong to a running IT-Deck.

    PyInstaller's onefile parent unpacks the whole bundle -- interpreter, Qt-
    free as this is it is still ~90 MB -- into %TEMP%\\_MEIxxxxx, and deletes
    it on the way out. Any time it does not get to (it was killed, the machine
    lost power, or it failed because something still held a file open) the
    directory is simply left there. They accumulate silently: this was found
    at 35 of them, 1.26 GB, on a machine that had never been told.

    Every deletion is claimed by a rename first, and this is the whole safety
    story. The v0.4.3 version of this function reasoned that rmtree "cannot
    delete a file another process has open, so a live copy's directory fails
    the attempt rather than being half-deleted" -- which is half true and
    therefore wrong: rmtree deletes everything it *can* before it reaches the
    locked file and raises. Measured: start IT-Deck, wait out the 60-second
    cutoff, launch a second copy, and the second copy's sweep takes 32 files
    out of the first copy's directory. The first copy then holds a
    directory missing whatever it had not imported yet.

    os.rename on the directory is the test that does not have that failure
    mode: Windows refuses to rename a directory that has a file open anywhere
    underneath it (verified against a running instance -- "Access to the path
    ... is denied"), and it either moves the whole tree or moves nothing. So a
    live directory is never touched at all, and only a directory that was
    proven unused gets deleted, under its new name.

    The same test is what makes it safe to be looking at _MEI* at all: that
    prefix belongs to PyInstaller, not to IT-Deck, so some of these
    directories are other onefile applications' -- and a running one of those
    is protected by exactly the same refusal.

    Also deliberately timid in the cheaper ways: it skips the directory this
    process is running from, ignores anything touched in the last minute, and
    treats every failure as "leave it alone". Nothing here is allowed to
    affect startup.
    """
    if not is_frozen():
        return
    try:
        import shutil
        import tempfile

        current = os.path.normcase(str(getattr(sys, "_MEIPASS", "")))
        cutoff = time.time() - 60
        removed = 0
        for entry in Path(tempfile.gettempdir()).glob("_MEI*"):
            try:
                if not entry.is_dir() or os.path.normcase(str(entry)) == current:
                    continue
                # A claim from an earlier sweep whose delete didn't finish.
                # Renaming proved it unused then; nobody can have opened it
                # since, because nothing knows this name.
                if entry.name.endswith(STALE_UNPACK_SUFFIX):
                    shutil.rmtree(entry)
                    removed += 1
                    continue
                if entry.stat().st_mtime > cutoff:
                    continue  # too fresh to be sure it is nobody's
                claimed = entry.with_name(entry.name + STALE_UNPACK_SUFFIX)
                os.rename(entry, claimed)
            except Exception:
                continue  # in use by a live copy, or not ours to delete
            try:
                shutil.rmtree(claimed)
                removed += 1
            except Exception:
                continue  # renamed but not deletable -- next start retries
        if removed:
            print(f"Cleaned up {removed} leftover unpack director"
                  f"{'y' if removed == 1 else 'ies'} in TEMP.")
    except Exception:
        pass


# --- uninstall -----------------------------------------------------------

# How long the leftover helper keeps retrying the two files it cannot delete
# until this process is gone. Generous on purpose: it costs nothing to wait
# and the alternative is an exe left on the desktop of someone who asked for
# it to be gone.
UNINSTALL_RETRY_SECONDS = 30


def desktop_shortcut_path() -> Path:
    """Where ensure_desktop_shortcut() put the icon, so both agree."""
    return Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "IT-Deck.lnk"


def remove_firewall_rules(exe: Path) -> None:
    """Delete the Windows Firewall rules that name this exe.

    They are not ours -- Windows writes them when the user answers (or
    cancels) the "allow this app to communicate" prompt on the first launch,
    and they outlive the program that caused them. A cancelled prompt leaves
    *Block* rules, which is the documented reason a reinstall later looks
    dead on the network, so leaving them behind is the opposite of removing
    IT-Deck without a trace.

    Firewall changes need elevation, so this is the one step that can raise a
    UAC prompt. It is raised here, while the window the user just clicked in
    is still on screen, rather than from the detached helper below -- an
    unexplained consent dialog appearing after the app has vanished is how
    malware behaves. Declining it costs only this step.

    Rules are matched on the full program path, case-insensitively (Windows
    records the path it launched, often lowercased). A copy of the exe that
    was moved after the rules were made is not matched, and nothing else
    could be: a broader match would delete another program's rules.
    """
    if os.name != "nt":
        return

    # Asked first, unelevated, because reading the firewall configuration
    # needs no rights and elevating does: an uninstall that raises a consent
    # prompt to delete nothing is a prompt that teaches people to click
    # through consent prompts.
    count = (
        "(Get-NetFirewallApplicationFilter | "
        f"Where-Object {{ $_.Program -eq {_ps_quote(exe)} }} | Measure-Object).Count"
    )
    try:
        found = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", count],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if found.stdout.strip() in ("", "0"):
            return
    except Exception:
        return  # can't tell -- better than a prompt nobody can explain

    # The removal goes through a *file*, not through a command string nested
    # inside another command string. The nested version did not work and did
    # not say so: the inner text sits in double quotes inside the outer
    # -Command, so the outer shell expands `$_` -- the pipeline variable the
    # filter is built on -- to nothing before the elevated child ever sees
    # it, leaving it to run `Where-Object { .Program -eq '...' }`. Measured
    # against a disposable copy with two rules of its own: 2 rules before,
    # 2 after, everything else about the uninstall correct. A -File argument
    # has no such second round of parsing.
    import tempfile

    NEWLINE = chr(10)
    script_path = Path(tempfile.gettempdir()) / f"itdeck-firewall-{os.getpid()}.ps1"
    script_path.write_text(
        "Get-NetFirewallApplicationFilter | "
        f"Where-Object {{ $_.Program -eq {_ps_quote(exe)} }} | "
        "Get-NetFirewallRule | Remove-NetFirewallRule -ErrorAction SilentlyContinue" + NEWLINE
        + "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue" + NEWLINE,
        encoding="utf-8",
    )
    # Two levels: the outer powershell asks for elevation and waits for the
    # inner one, so the UAC prompt is resolved before this returns.
    outer = (
        "Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait -ArgumentList "
        # The path carries its own double quotes: Start-Process joins the
        # list with spaces and does not quote an element that contains one,
        # so a profile path with a space would split into two arguments.
        f"'-NoProfile','-ExecutionPolicy','Bypass','-File',{_ps_quote(chr(34) + str(script_path) + chr(34))}"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", outer],
            capture_output=True,
            # Long enough to answer a consent prompt, short enough that
            # ignoring one does not leave the dialog saying "removing" for
            # two minutes. Whatever happens here, the uninstall carries on:
            # the prompt is for the firewall rules and nothing else.
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass  # declined, timed out, or no such rules -- the rest still goes
    finally:
        # It deletes itself on success; this covers a declined prompt, where
        # it never ran at all.
        try:
            script_path.unlink()
        except Exception:
            pass


def spawn_uninstall_helper(*targets) -> None:
    """Leave something behind to delete the two files this process holds open.

    A running exe cannot delete itself: Windows keeps the image file locked
    for as long as any process is mapped to it, and with PyInstaller's onefile
    build that is two processes, not one -- the bootloader parent as well as
    this child. The unpacked _MEIxxxx directory is held the same way.

    So the last act is a tiny PowerShell script that outlives us: it retries
    both deletions until they succeed or UNINSTALL_RETRY_SECONDS is up, then
    deletes itself. Retrying rather than waiting on a process name, because
    "the file is no longer locked" is the actual condition and it stays true
    if another IT-Deck happens to be running elsewhere.

    Detached and broken out of the job object on purpose -- the job created by
    create_child_job() would otherwise kill this helper at the very moment it
    is needed.
    """
    if os.name != "nt":
        return
    wanted = [str(t) for t in targets if t]
    if not wanted:
        return
    script = (
        "$targets = @({targets})\n"
        "foreach ($t in $targets) {{\n"
        # A deadline each, not one shared across the loop: a first target that
        # spends the whole budget would leave the rest with none, which is the
        # same mistake the session-end teardown had to be fixed out of.
        "  $deadline = (Get-Date).AddSeconds({seconds})\n"
        "  while ((Get-Date) -lt $deadline) {{\n"
        "    if (-not (Test-Path -LiteralPath $t)) {{ break }}\n"
        "    try {{ Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction Stop; break }}\n"
        "    catch {{ Start-Sleep -Milliseconds 250 }}\n"
        "  }}\n"
        "}}\n"
        "Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue\n"
    ).format(
        seconds=UNINSTALL_RETRY_SECONDS,
        targets=", ".join(_ps_quote(t) for t in wanted),
    )
    try:
        import tempfile

        path = Path(tempfile.gettempdir()) / f"itdeck-uninstall-{os.getpid()}.ps1"
        path.write_text(script, encoding="utf-8")
        # CREATE_NO_WINDOW, and deliberately *not* DETACHED_PROCESS, which is
        # the flag the agent's own _spawn_detached() reaches for. Two findings
        # from testing this, in order:
        #
        # 1. the two are mutually exclusive -- CreateProcess fails outright
        #    when both are passed, which is how the first version of this
        #    managed to launch nothing at all;
        # 2. with DETACHED_PROCESS alone, powershell.exe starts, finds it has
        #    no console, and exits 0 without running a line of the script.
        #    Measured across all four combinations: only the CREATE_NO_WINDOW
        #    ones actually ran.
        #
        # Nothing is lost by the swap. DETACHED_PROCESS exists to keep a
        # console-close event from reaching the child, and this build has no
        # console to close; surviving this process is what CREATE_NEW_PROCESS_
        # GROUP and the breakaway flag below are for.
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        command = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(path),
        ]
        try:
            subprocess.Popen(command, creationflags=flags | breakaway, close_fds=True)
        except OSError:
            # Same fallback shape as the agent's _spawn_detached: a job that
            # forbids breakaway must not cost us the launch entirely.
            subprocess.Popen(command, creationflags=flags, close_fds=True)
    except Exception as exc:
        # Printed rather than swallowed: this is the step whose failure leaves
        # the exe sitting on disk after someone asked for it to be gone, and
        # launcher.log is the only place that could ever say so.
        print(f"Uninstall helper could not be started: {exc}", flush=True)


def perform_uninstall(data_dir: Path, stop_children=None) -> None:
    """Remove every trace of IT-Deck from this PC, then exit.

    The inventory, and it is the whole inventory -- IT-Deck installs no
    service and registers no scheduled task:

    - `%LOCALAPPDATA%\\IT-Deck\\` — config.env with both tokens, the tile
      database, the logs
    - the Desktop shortcut the first launch created
    - the Windows Firewall rules that name this exe (see above)
    - the "Start with Windows" Run value, if the person turned it on
    - the exe itself and its unpacked temp directory (via the helper above,
      because this process is holding both open)

    **Never runs from a source checkout.** `sys.executable` is the frozen exe
    only when frozen; in a dev run it is python.exe, and deleting the
    interpreter is not what anybody meant by removing IT-Deck. The window
    does not offer the button there either, so this guard is the second of
    two.
    """
    if not is_frozen():
        print("Uninstall ignored: this is a source checkout, not an installed exe.", flush=True)
        return

    exe = Path(sys.executable)

    # Before the teardown, so the consent prompt appears while the window the
    # user clicked in is still there to explain it.
    remove_firewall_rules(exe)

    # Whatever copy it points at: an uninstalled IT-Deck must not try to
    # start at the next logon.
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            winreg.DeleteValue(key, AUTOSTART_VALUE)
    except (OSError, ImportError):
        pass  # it was never turned on

    if stop_children is not None:
        try:
            stop_children()
        except Exception:
            pass

    # Started before the deletions below, for two reasons: it is the step
    # whose failure matters most, and this is the last moment its complaint
    # can still reach launcher.log -- which lives inside the directory the
    # next block removes. The data directory is one of its targets as well as
    # being deleted inline: this process still has launcher.log open, so the
    # inline attempt can legitimately fail, and then the retry loop gets it
    # once the handle dies with us.
    spawn_uninstall_helper(exe, getattr(sys, "_MEIPASS", ""), data_dir)

    # The inline pass is the fast path -- after the children are gone, the
    # SQLite file and the logs are nobody's any more.
    for target in (data_dir, desktop_shortcut_path()):
        try:
            if target.is_dir():
                import shutil

                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
        except Exception:
            pass  # a leftover log file is not worth aborting an uninstall for

    # os._exit, not sys.exit: there is a tkinter mainloop on another thread and
    # nothing left worth unwinding -- the files this process still holds are
    # the helper's job now.
    os._exit(0)


# --- keeping backend and agent from outliving the launcher ----------------

# Job object limit flags and the JOBOBJECTINFOCLASS value for the extended
# limit struct, straight out of winnt.h -- ctypes has no names for these.
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100

# The handle is the kill switch: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE fires
# when the *last* handle to the job closes, so a handle Python garbage-
# collects is a handle that kills the backend and the agent mid-run. One
# module-level slot, written once, never cleared.
_CHILD_JOB: list = []


def create_child_job() -> Optional[int]:
    """A job object that takes the backend and the agent down with this process.

    The gap it fills: "End task" in Task Manager is TerminateProcess, and
    TerminateProcess runs nothing. No WM_QUERYENDSESSION, so
    install_session_end_handler() never hears about it; no unwinding, so the
    supervisor loop's finally: never runs; no atexit. Observed on v0.4.3: the
    launcher was ended by hand and its two children -- both re-invocations of
    this same exe, both with python312.dll mapped out of the shared unpacked
    _MEIxxxx directory -- kept running. PyInstaller's parent then failed to
    delete that directory and put up its modal "Failed to remove temporary
    directory" warning, which is the same dialog 10.8a fixed for the shutdown
    path, arrived at from a direction no handler can cover.

    So the enforcement has to belong to something other than this process.
    A job object is exactly that: the kernel terminates every process in the
    job when the last handle to it closes, and the kernel closes this
    process's handles however this process ended -- Quit, a crash, End task,
    or anything else that ends in TerminateProcess.

    Both breakaway flags are load-bearing, not caution. Job membership is
    inherited by child processes by default, and CLAUDE.md's rule is that
    **anything the agent launches must survive IT-Deck closing** -- without
    them, Quit would take the VPN client with it, which is a worse bug than
    the one being fixed. BREAKAWAY_OK is what makes _spawn_detached's
    CREATE_BREAKAWAY_FROM_JOB succeed rather than fall through to its
    no-flags branch; SILENT_BREAKAWAY_OK covers the launches that have no way
    to ask for themselves -- start_process()'s os.startfile() fallback, the
    path for .lnk files, documents and anything demanding elevation, takes no
    creationflags at all. Between them the job holds exactly the two
    processes assigned to it below, which are exactly the two that have to be
    gone before the temp directory can be deleted.

    Returns the job handle, or None if any of this is unavailable -- in which
    case the launcher behaves precisely as it did before, since every other
    teardown path is still in place.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                # ULONG_PTR, so pointer-sized: a c_ulong here would shift
                # every field after it on 64-bit, and LimitFlags -- read back
                # by nothing, but written by us -- has to land where the
                # kernel looks for it.
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]

        # Unnamed: a named job could be opened by anything else on the
        # machine, and nobody needs to find this one by name.
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_BREAKAWAY_OK
            | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        )
        ok = kernel32.SetInformationJobObject(
            job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None

        _CHILD_JOB.append(job)
        return job
    except Exception:
        return None


def assign_to_child_job(job: Optional[int], proc: subprocess.Popen) -> None:
    """Put a freshly spawned child into the job, best-effort.

    By pid rather than through Popen's private _handle, which is safe here
    for the one reason that matters: the Popen object holds an open handle to
    the process, and Windows does not recycle a pid while a handle to it is
    open, so this cannot land on a different process than the one just
    started.

    A failure is logged and otherwise ignored. The job is a backstop for kill
    paths that leave no code of ours running; losing it means losing that
    backstop, not breaking the run in front of the user.
    """
    if job is None or os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

        handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
        if not handle:
            print(f"Warning: could not open pid {proc.pid} to put it in the cleanup job.", flush=True)
            return
        try:
            if not kernel32.AssignProcessToJobObject(job, handle):
                print(
                    f"Warning: pid {proc.pid} could not join the cleanup job "
                    f"(error {ctypes.get_last_error()}).",
                    flush=True,
                )
        finally:
            kernel32.CloseHandle(handle)
    except Exception as exc:
        print(f"Warning: cleanup job assignment failed for pid {proc.pid}: {exc}", flush=True)


# --- Windows session end (shutdown / restart / logoff) --------------------

# Kept alive for the process lifetime on purpose. ctypes does not own the
# window procedure callback, the WNDCLASSW struct or the class-name buffer
# once RegisterClassW has been handed them -- if Python collects any of them
# the class points at freed memory and Windows calls into it at shutdown.
_SESSION_END_REFS: list = []

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016


def install_session_end_handler(on_session_end):
    """Make IT-Deck get out of the way when Windows shuts down.

    The bug this fixes: with IT-Deck running, choosing Shut down left the PC
    switched on -- twice, overnight -- because Windows stopped at the "this
    app is preventing you from shutting down" screen and waited for a click
    nobody was there to give.

    It is not the info window doing it. Tk answers WM_QUERYENDSESSION with
    TRUE by itself and never routes it to the WM_DELETE_WINDOW handler, so the
    Quit confirmation dialog is not involved (verified by sending the real
    message to a real Tk HWND). The blocker is one level further out, in the
    part of the exe that is not ours: PyInstaller's --onefile bootloader runs
    the program as a *child* process and keeps the parent alive to delete the
    unpacked _MEIxxxx temp directory afterwards. To survive long enough to do
    that, the parent creates its own hidden top-level window -- window class
    "PyInstallerOnefileHiddenWindow" -- and on WM_QUERYENDSESSION it calls
    ShutdownBlockReasonCreate() and then sits in its WM_ENDSESSION handler
    waiting for the child to exit. Our child never exits on its own: it is a
    supervisor loop that only stops on Quit or on the backend dying. So the
    parent waits, times out, and Windows blames ITDeck.exe for the stall.

    The fix is therefore in the child, and it is simply "exit when asked".
    This registers a hidden window of our own -- a real top-level window, and
    that detail is the trap here: a message-only (HWND_MESSAGE) window is
    never sent session-end messages at all -- and tears IT-Deck down the
    moment one arrives. The parent then sees the child gone, cleans up its
    temp directory, releases its block reason, and the shutdown proceeds.

    WM_QUERYENDSESSION is the primary trigger, not WM_ENDSESSION: the parent
    registers its block reason during the *query* phase, so by the time
    WM_ENDSESSION is dispatched the blocking screen may already be up. The
    cost of acting a phase early is that a shutdown somebody cancels at that
    screen also stops IT-Deck -- a relaunch, weighed against a PC that stays
    on all night. Sleep and hibernate are unaffected either way: those are
    WM_POWERBROADCAST and never send this message.

    **The teardown has to be done before the window procedure returns.** Not
    after, and not on another thread: Windows terminates a process as soon as
    its windows have answered, and v0.4.2 learned this the expensive way. It
    set an Event and let a waiter thread do the work 150 ms later; the process
    was already gone by then, so the backend and the agent were never stopped,
    kept python312.dll mapped out of the shared _MEIxxxx directory, and
    PyInstaller's parent -- unable to delete it -- put up a modal "Failed to
    remove temporary directory" warning. A modal dialog during shutdown is a
    shutdown that never finishes, so the PC stayed on exactly as before, just
    for a different reason. The budget for doing it inline is HungAppTimeout,
    5 seconds; the teardown is capped well under that.

    Returns a callable that runs the same teardown and then exits, for the
    info window to hang on Tk's WM_SAVE_YOURSELF. Every route is guarded by
    one lock and one done-flag, so the work happens exactly once however many
    of them arrive.
    """
    trigger = threading.Event()
    teardown_lock = threading.Lock()
    teardown_done = []

    def run_teardown() -> None:
        # Synchronous, and called from whichever window procedure was told
        # first. See the "done before it returns" paragraph above for why it
        # cannot be deferred to a thread. The lock is not decoration: the
        # watcher window procedure and Tk's WM_SAVE_YOURSELF run on different
        # threads and can both arrive, and an Event does not stop two of them
        # being inside terminate()/wait() at once.
        with teardown_lock:
            if teardown_done:
                return
            teardown_done.append(True)
            try:
                on_session_end()
            except Exception:
                pass

    def waiter() -> None:
        # Only the exit. Everything that has to happen before Windows may
        # terminate this process has already happened in run_teardown().
        trigger.wait()
        time.sleep(SESSION_END_EXIT_DELAY)
        # os._exit, not sys.exit: this is a daemon thread, there is a tkinter
        # mainloop on another one, and interpreter shutdown here would be one
        # more place to hang -- which is the exact failure being fixed.
        os._exit(0)

    threading.Thread(target=waiter, daemon=True).start()

    def request_session_end() -> None:
        run_teardown()
        trigger.set()

    if os.name != "nt":
        return request_session_end

    def pump() -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        # LRESULT is pointer-sized. Left at the ctypes default the return
        # value is truncated to a C int, and the one return value that
        # matters here is the answer to WM_QUERYENDSESSION -- a truncated one
        # reads as FALSE, which would *add* a shutdown blocker rather than
        # remove one.
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]

        def window_proc(hwnd, msg, wparam, lparam):
            if msg == WM_QUERYENDSESSION:
                # Before the reply, not after. Windows terminates a process as
                # soon as its windows have answered, so a teardown handed to
                # another thread here is a teardown that never runs.
                run_teardown()
                trigger.set()
                return 1  # yes -- nothing here needs saving, go ahead
            if msg == WM_ENDSESSION:
                if wparam:
                    run_teardown()
                    trigger.set()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(window_proc)
        class_name = "ITDeckSessionEndWatcher"
        wnd_class = WNDCLASSW()
        wnd_class.lpfnWndProc = proc
        wnd_class.hInstance = kernel32.GetModuleHandleW(None)
        wnd_class.lpszClassName = class_name
        _SESSION_END_REFS.extend([proc, wnd_class, class_name])

        if not user32.RegisterClassW(ctypes.byref(wnd_class)):
            print(f"Session-end watcher: RegisterClassW failed ({ctypes.get_last_error()}).")
            return
        # Never shown: no WS_VISIBLE, and ShowWindow is never called on it.
        hwnd = user32.CreateWindowExW(
            0, class_name, "IT-Deck session watcher", 0, 0, 0, 0, 0,
            None, None, wnd_class.hInstance, None,
        )
        if not hwnd:
            print(f"Session-end watcher: CreateWindowExW failed ({ctypes.get_last_error()}).")
            return
        _SESSION_END_REFS.append(hwnd)
        print("Session-end watcher ready.")

        # Session-end messages go to the thread that created the window, so
        # the message loop has to live here and nowhere else.
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    try:
        threading.Thread(target=pump, daemon=True).start()
    except Exception:
        pass  # the info window's own hook below still covers the common case

    return request_session_end


def _version_tuple(text: str) -> tuple:
    """"0.3.7" / "v0.3.7" -> (0, 3, 7). Unparseable trailing parts are dropped.

    Deliberately forgiving: this compares a tag somebody typed on GitHub
    against a constant somebody typed in this file, and the only outcome that
    matters is "is the remote one bigger". A tag like "v0.4.0-beta" stops at
    the first non-digit and compares as (0, 4, 0) rather than raising.
    """
    parts = []
    for chunk in text.strip().lstrip("vV").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def newer_version_available() -> Optional[str]:
    """The latest published version if it is newer than this one, else None.

    Every failure is silent and returns None -- no network, no DNS, GitHub's
    unauthenticated rate limit, a JSON shape change, a machine with the clock
    wrong enough to break TLS. An update check is a convenience; a launcher
    that fails to start because GitHub is down would be a disaster.
    """
    try:
        request = urllib.request.Request(
            UPDATE_CHECK_URL,
            headers={
                "User-Agent": f"IT-Deck/{ITDECK_VERSION}",
                "Accept": "application/vnd.github+json",
            },
        )
        # 10s, not 5: measured on the maintainer's own machine (a VPN is
        # usually up), where the TLS handshake intermittently needed more
        # than 5 seconds while the request itself completes in ~0.7s when it
        # does connect. A short timeout here does not fail loudly -- it
        # silently disables the whole feature on exactly the networks it was
        # written for. Nothing waits on this; it runs on a daemon thread.
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        latest = str(payload.get("tag_name") or "").strip().lstrip("vV")
    except Exception:
        return None
    if not latest:
        return None
    if _version_tuple(latest) > _version_tuple(ITDECK_VERSION):
        return latest
    return None


def _update_check_worker(result_queue: queue.Queue) -> None:
    # Always puts exactly one item, even on failure, so the window's poll
    # terminates instead of re-arming its timer forever.
    version = newer_version_available()
    if version:
        print(f"Update available: v{version} (running v{ITDECK_VERSION}) -- {RELEASES_PAGE_URL}")
    result_queue.put(version)


# --- what's new after an update ---------------------------------------------

# frontend/whats-new.json: [{"version": "0.5.6", "en": [...], "ru": [...]}],
# newest first. One file for both surfaces -- this window reads it from the
# bundled frontend, Studio fetches it like any static file.
WHATS_NEW_FILENAME = "whats-new.json"
# Which version's notes this PC has already been shown. A file of its own in
# the data directory rather than a config.env key: config.env is the
# person's to edit, and this is bookkeeping.
WHATS_NEW_SEEN_FILENAME = "whats-new-seen.txt"
# A person who skipped several updates gets the latest two, and the
# "All changes" link for the rest -- a card, not a changelog.
WHATS_NEW_MAX_VERSIONS = 2


def frontend_dir() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS) / "frontend"
    return REPO_ROOT / "frontend"


def load_whats_new(frontend: Path) -> list:
    """The entries, newest first; [] if the file is missing or unreadable."""
    try:
        entries = json.loads((frontend / WHATS_NEW_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("version")]


def whats_new_to_show(entries: list, current: str, seen: Optional[str]) -> list:
    """The entries newer than `seen` and no newer than `current`, newest first.

    Nothing newer than the running version: the file ships with the build,
    and a development build ahead of its notes must not announce them.
    `seen` None -- an install from before this feature -- counts as "seen
    nothing", which the version cap keeps to a card, not a history lesson.
    """
    floor = _version_tuple(seen) if seen else ()
    ceiling = _version_tuple(current)
    picked = [e for e in entries if floor < _version_tuple(e["version"]) <= ceiling]
    picked.sort(key=lambda e: _version_tuple(e["version"]), reverse=True)
    return picked[:WHATS_NEW_MAX_VERSIONS]


def read_whats_new_seen(data_dir: Path) -> Optional[str]:
    try:
        return (data_dir / WHATS_NEW_SEEN_FILENAME).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def mark_whats_new_seen(data_dir: Path, version: str) -> None:
    try:
        (data_dir / WHATS_NEW_SEEN_FILENAME).write_text(version, encoding="utf-8")
    except OSError:
        pass  # at worst the card shows once more


# --- tokens: a new PIN from the window ----------------------------------------


def new_pin(digits: int = 6) -> str:
    """A random numeric PIN: easy to type on a phone, and URL-safe."""
    import secrets

    return "".join(secrets.choice("0123456789") for _ in range(digits))


def change_tokens_via_backend(port: int, agent_token: str, **tokens) -> Optional[str]:
    """Ask the local backend to change tokens (PUT /api/access).

    Through the backend rather than writing config.env here, so there is one
    code path for a change: the backend saves it, applies it at once and
    disconnects phones on the old phone token -- exactly as when Studio does
    it. The window then picks the new values up from config.env like any
    other change. Returns an error message, or None on success.
    """
    body = json.dumps({key: value for key, value in tokens.items() if value}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/access",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json", "X-Agent-Token": agent_token},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
        return None
    except Exception as exc:
        return str(exc)


# --- start with Windows -------------------------------------------------------

AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = "IT-Deck"


def autostart_command(exe: Path) -> str:
    return f'"{exe}"'


def autostart_enabled(winreg, exe: Path) -> bool:
    """Whether Windows starts *this* exe at logon.

    A Run value that points at another copy (the exe was moved, or an older
    download) is reported as off, so ticking the box repoints it here.
    `winreg` is passed in so this is testable off Windows.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE)
    except OSError:
        return False
    return str(value).strip().lower() == autostart_command(exe).lower()


def set_autostart(winreg, exe: Path, enabled: bool) -> None:
    """Add or remove the per-user Run value. Per-user: no admin rights needed."""
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_VALUE, 0, winreg.REG_SZ, autostart_command(exe))
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_VALUE)
            except OSError:
                pass  # already off


def show_info_window(
    dashboard_url: str,
    studio_url: str,
    agent_token: str,
    logs_dir: Path,
    on_quit,
    update_queue: "Optional[queue.Queue]" = None,
    other_ips: "Optional[list]" = None,
    agent_alive=None,
    on_session_end=None,
    on_uninstall=None,
    on_start_agent=None,
    first_run: bool = False,
    client_token: str = "admin",
    config_path: Optional[Path] = None,
    port: Optional[int] = None,
    whats_new: Optional[list] = None,
    autostart=None,
) -> None:
    # A real GUI window, not another thing to read off the console: the
    # console fills with backend/agent noise (that's why it's redirected to
    # log files below), and on the frozen --windowed build there is no
    # console at all. This window is the entire desktop-side UI.
    #
    # It is shaped as three numbered steps rather than a list of facts. The
    # previous version showed two bare URLs and a raw token, and a real new
    # user (a second PC, someone who had never seen the project) could not
    # tell what any of it was for -- including that the VPN tile does nothing
    # until it is given a path in Studio, which is the single most common
    # "it's broken" report this project gets.
    #
    # Runs tkinter's mainloop() in a daemon thread rather than on the main
    # thread: run_launcher()'s own poll loop is what actually keeps the
    # process alive and responds to Ctrl+C (per the module's own
    # backend/agent-as-separate-processes design), and tkinter's mainloop()
    # doesn't reliably notice a Ctrl+C on its own. A daemon thread means
    # closing this window or leaving it open never affects that shutdown
    # path either way -- it dies with the process, not before it.
    def worker() -> None:
        import tkinter as tk
        from tkinter import ttk
        from types import SimpleNamespace

        lang = _detect_ui_lang()
        s = _STRINGS[lang]
        g = _GLASS

        # The tokens as they are *now*. Studio (or the New PIN button) can
        # change them while this window is open; poll_config() below keeps
        # this, the link, the QR code and the shown token in step with
        # config.env, and every button reads from here rather than from the
        # values this window was opened with.
        current = {
            "dashboard_url": dashboard_url,
            "agent_token": agent_token,
            "client_token": client_token,
        }
        dashboard_base = dashboard_url.split("?token=")[0]

        root = tk.Tk()
        root.title(s["title"])
        root.configure(bg=g["bg"])
        # Vertical resize stays available on purpose. Everything here is
        # auto-sized by Tk from the content, and Windows display scaling
        # (125%/150% is ordinary) scales the fonts but not the screen -- so
        # on a small, scaled display the natural height can exceed what fits.
        # Width is still frozen: the QR block and the URL field define it and
        # nothing good comes of stretching them.
        root.resizable(False, True)

        # Raised once so it is not born behind the browser someone launched
        # it from, then released. It used to set -topmost and never clear it,
        # which left this window floating above full-screen browsers and
        # games for its entire life.
        root.attributes("-topmost", True)
        root.lift()
        root.after(TOPMOST_RELEASE_MS, lambda: root.attributes("-topmost", False))

        try:
            if is_frozen():
                icon_path = Path(sys._MEIPASS) / "icon.ico"
            else:
                icon_path = REPO_ROOT / "agents" / "windows" / "icon.ico"
            if icon_path.exists():
                root.iconbitmap(str(icon_path))
        except Exception:
            pass

        style = ttk.Style(root)
        style.theme_use("clam")
        # Studio's type: Segoe UI, semibold on buttons and titles.
        ui_font = ("Segoe UI", 9)
        button_font = ("Segoe UI Semibold", 9)

        # Flat first: every style works with plain colours, so a checkout
        # without Pillow (or a Tk that can't load the images) still gets a
        # usable window. glass_images() below then swaps in the rounded
        # look where it can.
        style.configure("Card.TFrame", background=g["surface"])
        style.configure("Notice.TFrame", background=g["accent_wash"])
        style.configure(
            "Glass.TEntry",
            fieldbackground=g["well"],
            foreground=g["text"],
            insertcolor=g["text"],
            borderwidth=1,
            relief="flat",
            padding=(8, 5),
        )
        # clam draws a light focus/border ring on an Entry, which on a
        # read-only field that exists only to be copied reads as "this is
        # selected, type here". Pin every border colour to the hairline.
        style.map(
            "Glass.TEntry",
            bordercolor=[("focus", g["border"]), ("!focus", g["border"])],
            lightcolor=[("focus", g["border"]), ("!focus", g["border"])],
            darkcolor=[("focus", g["border"]), ("!focus", g["border"])],
            fieldbackground=[("readonly", g["well"])],
            foreground=[("readonly", g["text"])],
        )
        # studio.css .btn / .btn-primary / .btn-danger, and a smaller .btn
        # for the quiet secondary actions (Studio's chips).
        buttons_spec = {
            "Glass.TButton": (g["surface_raised"], g["surface_hover"], g["text"], button_font, (14, 6)),
            "Accent.TButton": (g["accent"], g["accent_active"], g["accent_ink"], button_font, (14, 6)),
            "Danger.TButton": (g["surface_raised"], g["surface_hover"], g["danger"], button_font, (14, 6)),
            "Mini.TButton": (g["surface_raised"], g["surface_hover"], g["text_muted"], ui_font, (10, 3)),
        }
        for name, (fill, hover, ink, font, padding) in buttons_spec.items():
            style.configure(
                name,
                background=fill,
                foreground=ink,
                bordercolor=g["border"],
                lightcolor=fill,
                darkcolor=fill,
                focuscolor=g["accent"],
                borderwidth=1,
                relief="flat",
                padding=padding,
                font=font,
            )
            style.map(
                name,
                background=[("disabled", g["surface_raised"]), ("pressed", hover), ("active", hover)],
                foreground=[("disabled", g["text_muted"])],
                lightcolor=[("active", hover)],
                darkcolor=[("active", hover)],
            )

        # Kept alive for as long as the window: Tk drops an image the moment
        # Python's last reference to it goes.
        images: dict = {}

        def photo(key: str, **spec) -> "tk.PhotoImage":
            if key not in images:
                images[key] = tk.PhotoImage(master=root, data=_rounded_png(**spec))
            return images[key]

        def glass_images() -> bool:
            """Rounded panels, buttons and fields, as ttk image elements.

            Each is a small image stretched nine-slice style (ttk's
            `border`), so a panel of any size costs one image, drawn once.
            Returns False, leaving the flat styles above, if Pillow or the
            PNG load isn't available.
            """
            try:
                panel = dict(width=48, height=48, radius=_RADIUS_PANEL)
                photo("card", **panel, fill=g["surface"], outline=g["border"], edge=g["edge"])
                # Studio's .setup-card: the accent wash inside an accent ring.
                photo("notice", **panel, fill=g["accent_wash"], outline=g["accent"])
                # Buttons carry a transparent margin: the focus ring (2px,
                # just outside, like Studio's :focus-visible) lives in it, so
                # focusing a button never changes its size.
                ctl = dict(width=36, height=36, radius=_RADIUS_CONTROL, margin=_FOCUS_MARGIN)
                for name, (fill, hover, _ink, _font, _pad) in buttons_spec.items():
                    base = name.split(".")[0]
                    line = None if base == "Accent" else g["border"]
                    hover_line = g["danger"] if base == "Danger" else line
                    photo(f"{base}", **ctl, fill=fill, outline=line)
                    photo(f"{base}-hover", **ctl, fill=hover, outline=hover_line)
                    photo(f"{base}-focus", **ctl, fill=fill, outline=line, ring=g["accent"])
                    photo(f"{base}-focus-hover", **ctl, fill=hover, outline=hover_line, ring=g["accent"])
                    photo(f"{base}-disabled", **ctl, fill=g["surface_raised"], outline=g["border"])
                photo("well", width=32, height=32, radius=_RADIUS_CONTROL, fill=g["well"], outline=g["border"])
            except Exception as exc:
                print(f"Window: flat look, rounded images unavailable: {exc}")
                return False

            # ttk paints a widget's whole rectangle in its style background
            # before drawing the image, so that background must be whatever
            # is *behind* the widget or the rounded corners show a square.
            # Panels always sit on the window ground.
            for name, key in (("Card.TFrame", "card"), ("Notice.TFrame", "notice")):
                element = f"{key}.panel"
                style.element_create(
                    element, "image", images[key], border=_RADIUS_PANEL, padding=0, sticky="nsew"
                )
                style.layout(name, [(element, {"sticky": "nsew"})])
                style.configure(name, background=g["bg"])

            for name in buttons_spec:
                base = name.split(".")[0]
                element = f"{base}.face"
                style.element_create(
                    element,
                    "image",
                    images[base],
                    ("disabled", images[f"{base}-disabled"]),
                    ("focus", "pressed", images[f"{base}-focus-hover"]),
                    ("focus", "active", images[f"{base}-focus-hover"]),
                    ("focus", images[f"{base}-focus"]),
                    ("pressed", images[f"{base}-hover"]),
                    ("active", images[f"{base}-hover"]),
                    border=_RADIUS_CONTROL + _FOCUS_MARGIN,
                    padding=_FOCUS_MARGIN,
                    sticky="nsew",
                )
                style.layout(
                    name,
                    [(element, {"sticky": "nsew", "children": [
                        ("Button.padding", {"sticky": "nsew", "children": [
                            ("Button.label", {"sticky": "nsew"}),
                        ]}),
                    ]})],
                )
                # The image draws the fill now; the style background is
                # only what shows around the corners -- see button_style().
                style.map(name, background=[], lightcolor=[], darkcolor=[])

            style.element_create(
                "well.field", "image", images["well"], border=_RADIUS_CONTROL, padding=1, sticky="nsew"
            )
            # Fields only ever sit on a panel. Mapped, not just configured:
            # clam maps a read-only entry's background to its own grey, and
            # a map beats a plain setting.
            style.configure("Glass.TEntry", background=g["surface"], padding=(10, 6))
            style.map("Glass.TEntry", background=[("readonly", g["surface"]), ("!readonly", g["surface"])])
            style.layout(
                "Glass.TEntry",
                [("well.field", {"sticky": "nsew", "children": [
                    ("Entry.padding", {"sticky": "nsew", "children": [
                        ("Entry.textarea", {"sticky": "nsew"}),
                    ]}),
                ]})],
            )
            return True

        rounded = glass_images()

        def button_style(parent, kind: str) -> str:
            """The ttk style for a `kind` button sitting on `parent`.

            With the rounded look, a button's style background is what shows
            around its corners and focus ring, so it has to be the colour of
            whatever it sits on -- a panel, the notice or the window ground.
            One derived style per (colour, kind), made on first use; ttk
            inherits everything else from "<kind>.TButton".
            """
            base = f"{kind}.TButton"
            if not rounded:
                return base
            behind = parent.cget("bg")
            name = f"on{behind.lstrip('#')}.{base}"
            if name not in derived_styles:
                style.configure(name, background=behind)
                derived_styles.add(name)
            return name

        derived_styles: set = set()

        def glass_button(parent, kind: str, **options) -> "ttk.Button":
            """A Studio button of `kind` (Glass, Accent, Danger, Mini) on
            `parent`. The only way buttons are made here: the style has to
            be derived from the very widget the button sits on, and taking
            both from one argument makes getting that wrong impossible."""
            return ttk.Button(parent, style=button_style(parent, kind), **options)

        # --- small helpers -------------------------------------------------

        def label(parent, text, muted=False, bold=False, size=9, wrap=None):
            # bg is taken from the parent so the same helper works on the
            # window background and inside a card, which are different
            # colours -- a label carrying the wrong bg is the one thing that
            # makes a flat tkinter layout look broken rather than plain.
            widget = tk.Label(
                parent,
                text=text,
                font=("Segoe UI", size, "bold" if bold else "normal"),
                bg=parent.cget("bg"),
                fg=g["text_muted"] if muted else g["text"],
                anchor="w",
                justify="left",
            )
            if wrap:
                widget.configure(wraplength=wrap)
            return widget

        def copy_text(text: str, feedback: tk.Label) -> None:
            root.clipboard_clear()
            root.clipboard_append(text)
            feedback.configure(text=s["copied"])
            root.after(1500, lambda: feedback.configure(text=""))

        def fit_window(place=None) -> None:
            """Resize to the content, clamped to the screen; optionally move.

            Called once at the end of build-up and again any time a block is
            added afterwards (the update notice). Tk auto-sizes a window only
            until it is given an explicit geometry -- after that, new content
            is simply clipped, which is how the update notice first pushed the
            Quit button off the bottom edge.
            """
            root.update_idletasks()
            width = root.winfo_reqwidth()
            height = min(root.winfo_reqheight(), int(root.winfo_screenheight() * 0.9))
            if place is None:
                root.geometry(f"{width}x{height}")
            else:
                root.geometry(f"{width}x{height}+{place[0]}+{place[1]}")

        def panel(parent, notice: bool = False):
            """A Studio panel: (outer to pack, inner to fill).

            The outer ttk frame draws the rounded glass (or, flat, just the
            colour); the inner plain frame carries the content, inset past
            the corners so no rectangle pokes out of the curve.
            """
            fill = g["accent_wash"] if notice else g["surface"]
            outer = ttk.Frame(parent, style="Notice.TFrame" if notice else "Card.TFrame")
            inner = tk.Frame(outer, bg=fill)
            inner.pack(fill="both", expand=True, padx=14 if rounded else 12, pady=12 if rounded else 10)
            return outer, inner

        def badge(parent, number: str) -> tk.Label:
            # Studio's .setup-icon: the accent square with the dark ink.
            common = dict(font=("Segoe UI Semibold", 9), fg=g["accent_ink"], bd=0)
            if rounded:
                image = photo("badge", width=22, height=22, radius=_RADIUS_BADGE, fill=g["accent"])
                return tk.Label(parent, text=number, image=image, compound="center", bg=parent.cget("bg"), **common)
            return tk.Label(parent, text=f" {number} ", bg=g["accent"], **common)

        def card(number: str, title: str) -> tk.Frame:
            """One numbered step: a Studio panel with a badge and a title."""
            outer, inner = panel(root)
            outer.pack(fill="x", padx=PAD, pady=(0, 10))
            head = tk.Frame(inner, bg=g["surface"])
            head.pack(fill="x", pady=(0, 8))
            badge(head, number).pack(side="left")
            tk.Label(
                head,
                text=title,
                font=("Segoe UI Semibold", 11),
                bg=g["surface"],
                fg=g["text"],
            ).pack(side="left", padx=(10, 0))
            body = tk.Frame(inner, bg=g["surface"])
            body.pack(fill="x")
            return body

        def draw_qr(parent, data: str):
            """The dashboard URL as a scannable code, or None.

            Deliberately black on white rather than themed: a QR code is read
            by a camera, not by a person, and the scanners on phones want the
            contrast and the quiet zone they were designed for. Tinting it to
            match the window would look tidier and scan worse.

            Every failure path returns None and the window simply shows the
            link instead -- the import (a dev checkout may not have qrcode
            installed) and the encode both.
            """
            try:
                import qrcode
            except Exception:
                return None
            try:
                code = qrcode.QRCode(
                    box_size=1, border=0, error_correction=qrcode.constants.ERROR_CORRECT_M
                )
                code.add_data(data)
                code.make(fit=True)
                matrix = code.get_matrix()
            except Exception:
                return None

            modules = len(matrix)
            side = (modules + QR_QUIET_MODULES * 2) * QR_MODULE_PX
            canvas = tk.Canvas(
                parent, width=side, height=side, bg="#ffffff", highlightthickness=0, bd=0
            )
            for y, row in enumerate(matrix):
                for x, filled in enumerate(row):
                    if not filled:
                        continue
                    x0 = (x + QR_QUIET_MODULES) * QR_MODULE_PX
                    y0 = (y + QR_QUIET_MODULES) * QR_MODULE_PX
                    canvas.create_rectangle(
                        x0, y0, x0 + QR_MODULE_PX, y0 + QR_MODULE_PX, fill="#000000", outline=""
                    )
            return canvas

        # --- header ---------------------------------------------------------

        # Studio's top bar: one panel with the name on the left and the
        # quiet controls on the right.
        header_panel, header = panel(root)
        header_panel.pack(fill="x", padx=PAD, pady=(PAD, 10))
        status_dot = tk.Label(header, text="●", font=("Segoe UI", 9), bg=g["surface"], fg=g["ok"])
        status_dot.pack(side="left", padx=(0, 6))
        label(header, s["running"], bold=True, size=12).pack(side="left")
        label(header, f"v{ITDECK_VERSION}", muted=True, size=9).pack(side="right")
        tour_button = glass_button(header, "Mini", text=s["tour_again"])
        tour_button.pack(side="right", padx=(0, 10))

        # A dot that is always green is decoration pretending to be status.
        # The launcher knows whether the agent process is alive -- it
        # supervises it -- so the dot reports that, and a line appears saying
        # what it means for the user. Without this, an agent that died (or one
        # that lost the singleton mutex five times and gave up) leaves the
        # window showing three confident steps and a healthy green dot.
        # The Close Agent tile stops the agent on purpose, and the supervisor
        # rightly leaves it stopped -- so this row is also the way back: one
        # button that asks run_launcher()'s loop to start it again. Without it
        # the only way back was quitting IT-Deck and relaunching.
        agent_warning = tk.Frame(root, bg=g["bg"])
        label(agent_warning, s["agent_down"], muted=True, size=8, wrap=330).pack(side="left", anchor="w")
        start_agent_button = None
        if on_start_agent is not None:
            def start_agent() -> None:
                start_agent_button.configure(text=s["agent_starting"], state="disabled")
                on_start_agent()
                # If it didn't come up (another agent holds the mutex, say),
                # the button must not stay greyed out forever.
                root.after(10000, lambda: start_agent_button.configure(text=s["start_agent"], state="normal"))

            start_agent_button = glass_button(
                agent_warning, "Mini", text=s["start_agent"], command=start_agent
            )
            start_agent_button.pack(side="right", padx=(8, 0))

        def poll_agent() -> None:
            try:
                alive = bool(agent_alive())
            except Exception:
                alive = True  # never let a broken probe raise an alarm
            status_dot.configure(fg=g["ok"] if alive else g["warn"])
            if alive and start_agent_button is not None:
                start_agent_button.configure(text=s["start_agent"], state="normal")
            # "Shown" means packed, not mapped: winfo_ismapped() is False for
            # everything in a minimised window, and read that way the warning
            # was re-packed and the window re-fitted every two seconds while
            # IT-Deck sat minimised with the agent down.
            if alive:
                if agent_warning.winfo_manager():
                    # Shrink back too, not just hide: fit_window() is the only
                    # thing that resizes an explicitly-sized window, so
                    # without this the window keeps the taller geometry after
                    # the agent recovers.
                    agent_warning.pack_forget()
                    fit_window()
            elif not agent_warning.winfo_manager():
                agent_warning.pack(fill="x", padx=PAD, pady=(0, 10), after=header_panel)
                fit_window()
            root.after(AGENT_STATUS_POLL_MS, poll_agent)

        if agent_alive is not None:
            root.after(AGENT_STATUS_POLL_MS, poll_agent)

        # --- update notice (hidden until the check says otherwise) -----------

        # Studio's setup card: the one panel with an accent ring, because it
        # is the one thing here asking for something to be done.
        update_bar, update_row = panel(root, notice=True)
        update_text = label(update_row, "", size=9)
        update_text.pack(side="left")
        glass_button(
            update_row, "Accent",
            text=s["update_download"],
            command=lambda: webbrowser.open(RELEASES_PAGE_URL),
        ).pack(side="right")

        def show_update(version: str) -> None:
            update_text.configure(text=s["update_available"].format(version=version))
            above = agent_warning if agent_warning.winfo_manager() else header_panel
            update_bar.pack(fill="x", padx=PAD, pady=(0, 10), after=above)
            # The window already has an explicit geometry by the time this
            # runs, so it will NOT grow on its own -- packing a new block into
            # a fixed-height window pushes the footer buttons off the bottom
            # instead. Confirmed by screenshot before this call was added.
            fit_window()

        def poll_update() -> None:
            # The check runs on its own thread, and tkinter must only ever be
            # touched from the thread running its mainloop -- so the worker
            # hands the answer over through a Queue and this, on the Tk
            # thread, picks it up. Scheduling root.after() from the worker
            # instead would be touching Tk from the wrong thread.
            try:
                version = update_queue.get_nowait()
            except queue.Empty:
                root.after(UPDATE_POLL_MS, poll_update)
                return
            if version:
                show_update(version)

        if update_queue is not None:
            root.after(UPDATE_POLL_MS, poll_update)

        # --- step 1: the phone ----------------------------------------------

        step1 = card("1", s["step1_title"])
        qr = draw_qr(step1, dashboard_url)
        if qr is not None:
            qr.pack(side="left", anchor="n", padx=(0, 14))

        right = tk.Frame(step1, bg=g["surface"])
        right.pack(side="left", fill="both", expand=True)
        label(
            right, s["step1_body"] if qr is not None else s["step1_body_no_qr"], muted=True, wrap=300
        ).pack(anchor="w", pady=(0, 8))

        url_entry = ttk.Entry(
            right, width=38, font=("Consolas", 9), style="Glass.TEntry", takefocus=False
        )
        url_entry.insert(0, dashboard_url)
        url_entry.configure(state="readonly")
        url_entry.pack(fill="x", pady=(0, 8))

        dash_row = tk.Frame(right, bg=g["surface"])
        dash_row.pack(fill="x")
        dash_feedback = label(dash_row, "", muted=True)
        glass_button(
            dash_row, "Accent",
            text=s["copy"],
            command=lambda: copy_text(current["dashboard_url"], dash_feedback),
        ).pack(side="left")
        dash_feedback.pack(side="left", padx=(10, 0))

        # The address above is a ranked guess (see detect_primary_and_other_ips),
        # and a guess needs a visible fallback: on a PC with several adapters
        # the runner-up is often the right one. The console has printed this
        # line for a while; on a --windowed build nobody can read the console.
        if other_ips:
            label(
                right,
                f"{s['other_ips']} {', '.join(other_ips)}",
                muted=True,
                size=8,
                wrap=300,
            ).pack(anchor="w", pady=(8, 0))

        # A new random phone PIN in one click, for the person who shares
        # their Wi-Fi and wants the default "admin" gone. Through the
        # backend (change_tokens_via_backend), so phones on the old token are
        # disconnected at once; the refresh below then redraws the link and
        # the QR code from config.env.
        pin_row = tk.Frame(right, bg=g["surface"])
        pin_feedback = label(right, "", muted=True, size=8, wrap=300)
        if config_path is not None and port is not None:
            def request_new_pin() -> None:
                from tkinter import messagebox

                if not messagebox.askokcancel(s["title"], s["new_pin_confirm"], parent=root):
                    return
                pin = new_pin()
                error = change_tokens_via_backend(port, current["agent_token"], client_token=pin)
                if error:
                    pin_feedback.configure(text=s["new_pin_failed"].format(error=error))
                else:
                    pin_feedback.configure(text=s["new_pin_done"].format(pin=pin))
                    poll_config(reschedule=False)
                fit_window()

            pin_row.pack(fill="x", pady=(8, 0))
            glass_button(pin_row, "Mini", text=s["new_pin"], command=request_new_pin).pack(
                side="left"
            )
            pin_feedback.pack(anchor="w", pady=(4, 0))

        def refresh_link() -> None:
            nonlocal qr
            url_entry.configure(state="normal")
            url_entry.delete(0, "end")
            url_entry.insert(0, current["dashboard_url"])
            url_entry.configure(state="readonly")
            if qr is not None:
                qr.destroy()
                qr = draw_qr(step1, current["dashboard_url"])
                if qr is not None:
                    qr.pack(side="left", anchor="n", padx=(0, 14), before=right)

        # (mtime, size, inode): Windows file times have a coarse tick, and
        # two quick rewrites (Studio changing both tokens) can share one.
        # os.replace always brings a new file, so the inode tells them apart.
        config_stamp = {"mtime": None}

        def poll_config(reschedule: bool = True) -> None:
            # One stat() every two seconds; the file is read only when it
            # has changed, and only a changed token touches the widgets.
            # Rescheduled whatever happens: the backend swaps the file in
            # with os.replace, and a read that lands on that moment must cost
            # one tick, not the whole refresh (the stamp is only kept once
            # the read succeeded, so the next tick tries again).
            try:
                info = config_path.stat()
                mtime = (info.st_mtime_ns, info.st_size, info.st_ino)
            except OSError:
                mtime = None  # no file: nothing to follow (a silent no-op, as before)
            if mtime is not None and (mtime != config_stamp["mtime"] or not reschedule):
                try:
                    apply_config(parse_config(config_path))
                    config_stamp["mtime"] = mtime
                except Exception as exc:
                    print(f"Window: couldn't re-read config.env: {exc}")
            if reschedule:
                root.after(AGENT_STATUS_POLL_MS, poll_config)

        def apply_config(values: dict) -> None:
            new_client = values.get("CLIENT_TOKEN") or current["client_token"]
            new_agent = values.get("AGENT_TOKEN") or current["agent_token"]
            if new_client != current["client_token"]:
                current["client_token"] = new_client
                current["dashboard_url"] = f"{dashboard_base}?token={new_client}"
                refresh_link()
            if new_agent != current["agent_token"]:
                current["agent_token"] = new_agent
                if token_revealed["on"]:
                    reveal_token()

        # --- step 2: Studio --------------------------------------------------

        step2 = card("2", s["step2_title"])
        label(step2, s["step2_body"], muted=True, wrap=460).pack(anchor="w", pady=(0, 8))

        studio_row = tk.Frame(step2, bg=g["surface"])
        studio_row.pack(fill="x")
        glass_button(
            studio_row, "Glass",
            text=s["open_studio"],
            command=lambda: webbrowser.open(studio_url),
        ).pack(side="left")

        # The agent token used to sit on screen as a bare 32-character string
        # with no explanation, which is exactly the "what is this" the rework
        # is about. It is needed once, by Studio, and only sometimes -- so it
        # is behind a button until it is actually wanted.
        token_holder = tk.Frame(step2, bg=g["surface"])
        token_holder.pack(fill="x", pady=(8, 0))

        token_revealed = {"on": False}

        def reveal_token() -> None:
            token_revealed["on"] = True
            for child in token_holder.winfo_children():
                child.destroy()
            label(token_holder, s["token_hint"], muted=True, size=8).pack(anchor="w")
            row = tk.Frame(token_holder, bg=g["surface"])
            row.pack(fill="x", pady=(2, 0))
            entry = ttk.Entry(
                row, width=20, font=("Consolas", 9), style="Glass.TEntry", takefocus=False
            )
            entry.insert(0, current["agent_token"])
            entry.configure(state="readonly")
            entry.pack(side="left")
            feedback = label(row, "", muted=True, size=8)
            glass_button(
                row, "Mini",
                text=s["copy_token"],
                command=lambda: copy_text(current["agent_token"], feedback),
            ).pack(side="left", padx=(8, 0))
            feedback.pack(side="left", padx=(6, 0))
            # Same reason as the update notice: the window has an explicit
            # geometry by now, so replacing the button with this taller row
            # clips the footer instead of growing the window.
            fit_window()

        glass_button(
            token_holder, "Mini", text=s["show_token"], command=reveal_token
        ).pack(anchor="w")

        # --- step 3: what the two buttons do ---------------------------------

        step3 = card("3", s["step3_title"])
        label(step3, s["step3_body"], muted=True, wrap=460).pack(anchor="w")
        label(step3, f"{s['logs_hint']} {logs_dir}", muted=True, size=8, wrap=460).pack(
            anchor="w", pady=(8, 0)
        )

        def confirm_uninstall() -> None:
            """What stands between a stray click and a deleted install.

            A modal dialog that spells out what will go, rather than a yes/no
            question about a sentence nobody reads. Cancel holds the focus and
            Enter is bound to it, so the reflex that dismisses every other
            dialog dismisses this one too; the red button has to be aimed at
            and clicked. Escape closes it, and closing it does nothing.
            """
            dialog = tk.Toplevel(root)
            dialog.title(s["uninstall_title"])
            dialog.configure(bg=g["bg"])
            dialog.resizable(False, False)
            # transient + grab_set: it stays on top of its own window and takes
            # the input, so the deck behind cannot be clicked mid-uninstall.
            dialog.transient(root)
            dialog.grab_set()

            body = tk.Frame(dialog, bg=g["bg"])
            body.pack(fill="both", expand=True, padx=PAD, pady=PAD)

            label(body, s["uninstall_title"], bold=True, size=12).pack(anchor="w")
            label(body, s["uninstall_body"], muted=True, size=9, wrap=420).pack(
                anchor="w", pady=(10, 0)
            )
            label(body, s["uninstall_warning"], size=9, wrap=420).pack(anchor="w", pady=(10, 0))
            label(body, s["uninstall_uac"], muted=True, size=8, wrap=420).pack(
                anchor="w", pady=(8, 0)
            )
            label(body, s["uninstall_phone"], muted=True, size=8, wrap=420).pack(
                anchor="w", pady=(4, 0)
            )

            status = label(body, "", muted=True, size=9)
            status.pack(anchor="w", pady=(10, 0))

            buttons = tk.Frame(body, bg=g["bg"])
            buttons.pack(fill="x", pady=(14, 0))

            def close() -> None:
                dialog.grab_release()
                dialog.destroy()

            cancel_button = glass_button(
                buttons, "Glass", text=s["cancel"], command=close
            )
            cancel_button.pack(side="left")

            def run_uninstall() -> None:
                status.configure(text=s["uninstall_busy"])
                go_button.state(["disabled"])
                cancel_button.state(["disabled"])
                # On a thread: the firewall step waits for a UAC prompt the
                # user still has to answer, and doing that on the tkinter
                # thread would freeze this dialog mid-sentence. The work ends
                # in os._exit, so nothing comes back.
                threading.Thread(target=on_uninstall, daemon=True).start()

            go_button = glass_button(
                buttons, "Danger", text=s["uninstall_go"], command=run_uninstall
            )
            go_button.pack(side="right")

            # Enter is bound to closing, not to the red button. A dialog
            # whose default action is the destructive one gets answered by the
            # same reflex that dismisses every other dialog -- so the only way
            # to the deletion is to aim at it and click.
            dialog.bind("<Return>", lambda _event: close())
            dialog.bind("<Escape>", lambda _event: close())
            dialog.protocol("WM_DELETE_WINDOW", close)

            dialog.update_idletasks()
            # Centred on the main window rather than on the screen: it belongs
            # to this window, and a dialog this consequential should not
            # appear somewhere unrelated.
            x = root.winfo_x() + (root.winfo_width() - dialog.winfo_reqwidth()) // 2
            y = root.winfo_y() + (root.winfo_height() - dialog.winfo_reqheight()) // 3
            dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")
            # Focus goes to Cancel, and it is set *after* the dialog is mapped
            # -- focus_set() on a widget in a window that does not exist on
            # screen yet is silently dropped, which left the focus on the
            # toplevel itself and Space one Tab away from the red button.
            dialog.after(10, cancel_button.focus_set)

        # Only on an installed exe. A source checkout has no exe, no shortcut
        # and no firewall rules to remove, and perform_uninstall() refuses
        # there anyway -- but an offer that cannot be honoured should not be
        # on screen in the first place.
        #
        # Deliberately down here, small and quiet, and not in the footer next
        # to Quit: those two are the buttons people press, and the one that
        # deletes the install has no business being a neighbour of the one
        # that ends the session.
        # Off by default: starting with Windows is the person's call, not
        # something an app should do to itself on first run.
        if autostart is not None and is_frozen():
            autostart_get, autostart_set = autostart
            autostart_var = tk.BooleanVar(value=autostart_get())
            autostart_feedback = label(step3, "", muted=True, size=8, wrap=460)

            def toggle_autostart() -> None:
                try:
                    autostart_set(autostart_var.get())
                    autostart_feedback.configure(text="")
                except OSError as exc:
                    autostart_var.set(autostart_get())
                    autostart_feedback.configure(text=s["autostart_failed"].format(error=exc))
                fit_window()

            tk.Checkbutton(
                step3,
                text=s["autostart"],
                variable=autostart_var,
                command=toggle_autostart,
                bg=g["surface"],
                fg=g["text"],
                activebackground=g["surface"],
                activeforeground=g["text"],
                selectcolor=g["surface_raised"],
                font=("Segoe UI", 9),
                anchor="w",
                highlightthickness=0,
                bd=0,
            ).pack(anchor="w", pady=(10, 0))
            autostart_feedback.pack(anchor="w")

        if on_uninstall is not None and is_frozen():
            glass_button(
                step3, "Mini", text=s["uninstall"], command=confirm_uninstall
            ).pack(anchor="w", pady=(10, 0))

        # --- footer ----------------------------------------------------------

        # Two buttons, and the distinction is load-bearing: this window is
        # the only interface IT-Deck has. The exe is built --windowed, so
        # there is no console to fall back to -- no Ctrl+C, and nothing to
        # restore from the taskbar. "Minimize" therefore iconifies rather
        # than destroys: destroying it left IT-Deck running with no way to
        # see the URL again and no way to stop it short of Task Manager.
        buttons = tk.Frame(root, bg=g["bg"])
        buttons.pack(fill="x", padx=PAD, pady=(4, 14))

        def quit_itdeck() -> None:
            from tkinter import messagebox

            if not messagebox.askokcancel(s["title"], s["quit_confirm"], parent=root):
                return
            # Only signals; the supervisor loop in run_launcher() owns the
            # actual teardown of the backend and agent processes. Doing it
            # from this thread would race that loop and leave orphans.
            on_quit()
            root.destroy()

        glass_button(buttons, "Glass", text=s["close"], command=root.iconify).pack(
            side="left"
        )
        glass_button(buttons, "Glass", text=s["quit"], command=quit_itdeck).pack(
            side="right"
        )

        # The title bar's X goes to Quit, not to Tk's default destroy. With
        # no console behind it, a destroyed window is an IT-Deck nobody can
        # see, reach or stop -- so the close box means what it means in every
        # other desktop app, and the confirmation dialog is what keeps a
        # stray click from taking the phone offline.
        root.protocol("WM_DELETE_WINDOW", quit_itdeck)

        # A second, independent route into the same shutdown teardown as
        # install_session_end_handler()'s hidden window. Tk maps Windows'
        # WM_QUERYENDSESSION onto the X11-flavoured WM_SAVE_YOURSELF protocol
        # -- so this fires when the machine is shutting down, and never when
        # the user closes the window (that is WM_DELETE_WINDOW above). It
        # costs one line and covers the case the hidden window cannot be
        # tested for without an actual shutdown: that it was created at all.
        # Both routes end in the same Event, so whichever arrives first wins
        # and the other is a no-op. Deliberately no confirmation dialog: a
        # modal prompt during shutdown is the failure mode, not the fix.
        if on_session_end is not None:
            root.protocol("WM_SAVE_YOURSELF", on_session_end)

        # Nothing should open with a focus ring drawn around a read-only URL
        # field, which is what happens otherwise -- the first focusable widget
        # takes focus and clam renders it selected.
        root.focus_set()

        root.update_idletasks()
        screen_h = root.winfo_screenheight()
        max_h = int(screen_h * 0.9)
        if qr is not None and root.winfo_reqheight() > max_h:
            # Out of vertical room -- the QR is the one big optional block,
            # and a window whose Quit button is off-screen is worse than a
            # window with no QR in it. This is what makes the layout survive
            # a small screen at 150% display scaling.
            qr.destroy()
            qr = None
        x = max(0, (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2)
        y = max(0, (screen_h - root.winfo_reqheight()) // 3)
        fit_window(place=(x, y))

        # After every widget is packed, not before: applying this earlier
        # (when the window was still its default un-sized shape) meant
        # resolving the real HWND -- which needs update_idletasks() to
        # force it into existence -- also forced Tk to commit to that
        # premature, too-small size instead of auto-sizing to the content
        # added afterward. Confirmed the hard way: the window rendered
        # correctly styled but cropped mid-text.
        _apply_windows11_chrome(root)

        # --- first-run tour --------------------------------------------------
        # Four short pages laid *over* the finished window with place(), not a
        # second window: the window keeps the size its real content gave it,
        # nothing here can close it, and when the tour ends the steps are
        # simply uncovered. Shown by itself on the very first launch (no
        # config.env existed), and from the header's Tutorial button after.
        tour_pages = [
            ("tour1_title", "tour1_body"),
            ("tour2_title", "tour2_body"),
            ("tour3_title", "tour3_body"),
            ("tour4_title", "tour4_body"),
        ]

        # Both overlays (the tour, What's new) sit on Studio's own ground --
        # the dark page with its purple and teal pools -- with the card baked
        # into that one image, instead of a card floating on flat black.
        #
        # Nothing about the geometry changes while one is open: the card is
        # measured once against every page it will show and fixed at the
        # tallest, and the buttons keep their places and widths. A page turn
        # then only swaps label text. (It used to resize the card, pack and
        # unpack Back and change the Next button's width, and on Windows each
        # click relaid and repainted the window in several visible passes.)
        # The ground is drawn once, and again only if the window itself is
        # resized -- debounced, never per frame.
        overlay_pad_x, overlay_pad_y = 26, 22

        def open_overlay(pages: int = 1, render=None) -> SimpleNamespace:
            overlay = tk.Frame(root, bg=g["bg"])
            ground = tk.Label(overlay, bd=0, highlightthickness=0, bg=g["bg"])
            ground.place(x=0, y=0, relwidth=1, relheight=1)
            # The flat card: only shown if the image can't be drawn.
            flat_card = tk.Frame(overlay, bg=g["surface"])
            content = tk.Frame(overlay, bg=g["surface"])
            width = max(root.winfo_width(), root.winfo_reqwidth())
            card_w = min(width - 2 * PAD, 470)
            state = {"size": None, "pending": None, "image": None, "height": 0}

            def overlay_size() -> tuple:
                w, h = overlay.winfo_width(), overlay.winfo_height()
                if w > 1:
                    return w, h
                # Not mapped yet (the first-run tour opens before mainloop):
                # winfo_width/height still say 1, but geometry() already holds
                # the size fit_window() gave the window -- which may be less
                # than it asked for on a small screen.
                match = re.match(r"(\d+)x(\d+)", root.geometry())
                if match and int(match.group(1)) > 1:
                    return int(match.group(1)), int(match.group(2))
                return root.winfo_reqwidth(), root.winfo_reqheight()

            def draw() -> None:
                state["pending"] = None
                w, h = overlay_size()
                if state["size"] == (w, h):
                    return
                state["size"] = (w, h)
                # Never taller than the window: the buttons are packed first
                # at the bottom, so on a small screen the text is what gives,
                # never Next/OK.
                card_h = min(state["height"] + 2 * overlay_pad_y, h - 2 * PAD)
                x = (w - card_w) // 2
                y = max(PAD, round(h * 0.45 - card_h / 2))
                content.place(
                    x=x + overlay_pad_x,
                    y=y + overlay_pad_y,
                    width=card_w - 2 * overlay_pad_x,
                    height=card_h - 2 * overlay_pad_y,
                )
                if rounded:
                    try:
                        data = _ground_png(w, h, (x, y, card_w, card_h), _RADIUS_PANEL, g["surface"], g["border"], g["edge"])
                        state["image"] = tk.PhotoImage(master=root, data=data)
                        ground.configure(image=state["image"])
                        flat_card.place_forget()
                        return
                    except Exception as exc:
                        print(f"Window: flat overlay, ground image unavailable: {exc}")
                flat_card.place(x=x, y=y, width=card_w, height=card_h)

            def on_configure(_event) -> None:
                if state["pending"] is not None:
                    root.after_cancel(state["pending"])
                state["pending"] = root.after(120, draw)

            def finish() -> None:
                """Measure every page, fix the card at the tallest, show it."""
                heights = []
                for page in range(pages):
                    if render is not None:
                        render(page)
                    content.update_idletasks()
                    heights.append(content.winfo_reqheight())
                if render is not None:
                    render(0)
                state["height"] = max(heights)
                overlay.place(x=0, y=0, relwidth=1, relheight=1)
                draw()
                overlay.bind("<Configure>", on_configure)

            def refit() -> None:
                """After a page turn: grow once if the page no longer fits.

                Pages are measured on open, but the tour quotes the phone
                token, and Studio can change that while the tour is open.
                """
                content.update_idletasks()
                if content.winfo_reqheight() > state["height"]:
                    state["height"] = content.winfo_reqheight()
                    state["size"] = None
                    draw()

            def close() -> None:
                if state["pending"] is not None:
                    root.after_cancel(state["pending"])
                root.unbind("<Escape>")
                overlay.destroy()

            # The wrap leaves a few px spare: a tk.Label's own padding and
            # border sit outside its wraplength, and a label exactly as wide
            # as the card's content clips its last letters.
            return SimpleNamespace(
                content=content, wrap=card_w - 2 * overlay_pad_x - 8, finish=finish, refit=refit, close=close
            )

        def show_tour() -> None:
            state = {"page": 0}

            def render_page(page: int) -> None:
                key_title, key_body = tour_pages[page]
                counter.configure(text=s["tour_step"].format(n=page + 1, total=len(tour_pages)))
                title.configure(text=s[key_title])
                body.configure(text=s[key_body].format(token=current["client_token"]))
                last = page == len(tour_pages) - 1
                next_button.configure(text=s["tour_done"] if last else s["tour_next"])
                # Always there, greyed on the first page: a button that
                # appears and disappears moves its neighbours.
                back_button.state(["disabled"] if page == 0 else ["!disabled"])

            ov = open_overlay(pages=len(tour_pages), render=render_page)
            box = ov.content
            # The buttons first, pinned to the bottom of the fixed-height card,
            # so a shorter page doesn't pull them up.
            nav = tk.Frame(box, bg=g["surface"])
            nav.pack(side="bottom", fill="x")
            counter = label(box, "", muted=True, size=9)
            counter.pack(anchor="w", pady=(0, 4))
            title = label(box, "", bold=True, size=15, wrap=ov.wrap)
            title.pack(anchor="w")
            body = label(box, "", size=10, wrap=ov.wrap)
            body.pack(anchor="w", pady=(8, 16))

            def go(delta: int) -> None:
                page = state["page"] + delta
                if page >= len(tour_pages):
                    ov.close()
                    return
                state["page"] = max(0, page)
                render_page(state["page"])
                ov.refit()
                next_button.focus_set()

            glass_button(nav, "Mini", text=s["tour_skip"], command=ov.close).pack(side="left")
            # Wide enough for the longer of its two labels, so it doesn't
            # change size on the last page.
            next_button = glass_button(
                nav, "Accent",
                width=max(len(s["tour_next"]), len(s["tour_done"])),
                command=lambda: go(1),
            )
            next_button.pack(side="right")
            # Packed after Next with side="right": it lands to Next's left,
            # where Back belongs.
            back_button = glass_button(nav, "Glass", text=s["tour_back"], command=lambda: go(-1))
            back_button.pack(side="right", padx=(0, 8))
            # On root: focus sits on the Next button, so an overlay binding
            # would never see the key.
            root.bind("<Escape>", lambda _event: ov.close())
            ov.finish()
            next_button.focus_set()

        tour_button.configure(command=show_tour)
        if first_run:
            show_tour()

        if config_path is not None:
            root.after(AGENT_STATUS_POLL_MS, poll_config)

        # --- what's new after an update ---------------------------------------
        # The same overlay as the tour, once per version (run_launcher decides
        # which entries, and has already marked them seen). A fresh install
        # gets the tour instead: release notes mean nothing to a new user.
        def show_whats_new(entries: list) -> None:
            ov = open_overlay()
            box = ov.content
            nav = tk.Frame(box, bg=g["surface"])
            nav.pack(side="bottom", fill="x", pady=(14, 0))
            label(
                box, s["whats_new_title"].format(version=entries[0]["version"]), bold=True, size=15, wrap=ov.wrap
            ).pack(anchor="w", pady=(0, 8))
            for index, entry in enumerate(entries):
                if len(entries) > 1:
                    label(box, entry["version"], muted=True, size=9).pack(anchor="w", pady=(6 if index else 0, 2))
                for bullet in entry.get(lang) or entry.get("en") or []:
                    label(box, f"\u2022  {bullet}", size=10, wrap=ov.wrap).pack(anchor="w", pady=1)

            ok = glass_button(nav, "Accent", text=s["whats_new_ok"], command=ov.close)
            ok.pack(side="right")
            glass_button(
                nav, "Glass",
                text=s["whats_new_all"],
                command=lambda: webbrowser.open(RELEASES_PAGE_URL),
            ).pack(side="right", padx=(0, 8))
            root.bind("<Escape>", lambda _event: ov.close())
            ov.finish()
            ok.focus_set()

        if whats_new and not first_run:
            show_whats_new(whats_new)

        root.mainloop()

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pass  # convenience only -- the console block below still has everything


# --- role: launcher (default) -----------------------------------------


def run_launcher() -> int:
    ensure_desktop_shortcut()
    # Before anything else claims disk: see sweep_stale_unpack_dirs().
    sweep_stale_unpack_dirs()
    data_dir = default_data_dir()
    # Before load_or_create_config() writes it: no config.env yet means this
    # is the first launch on this PC, which is when the window shows its tour.
    first_run = not (data_dir / CONFIG_FILENAME).exists()
    config = load_or_create_config(data_dir)
    port = int(config["SERVER_PORT"])
    agent_token = config["AGENT_TOKEN"]
    client_token = config["CLIENT_TOKEN"]

    # Both children write to a *file*, and Python block-buffers a non-tty
    # stdout -- so without this their logs lag by kilobytes and the last
    # thing that happened before a problem is exactly what has not been
    # flushed yet. Measured while debugging this build: agent.log's tail was
    # several minutes stale. The window now points users at these files, so
    # they have to be current, and one unbuffered pipe per process costs
    # nothing at these volumes.
    backend_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "AGENT_TOKEN": agent_token,
        "CLIENT_TOKEN": client_token,
        "SERVER_PORT": str(port),
        "ITDECK_DATA_DIR": str(data_dir),
        # Where Studio's Access dialog saves a changed token (see
        # backend/app/api/access.py); the agent re-reads its token from the
        # same file on every reconnect.
        "ITDECK_CONFIG_FILE": str(data_dir / CONFIG_FILENAME),
    }
    agent_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "AGENT_TOKEN": agent_token,
        "SERVER_IP": "127.0.0.1",
        "SERVER_PORT": str(port),
        "AGENT_NAME": "windows",
        "ITDECK_CONFIG_FILE": str(data_dir / CONFIG_FILENAME),
    }
    for key in OPTIONAL_AGENT_KEYS:
        if config.get(key):
            agent_env[key] = config[key]

    # Backend/agent logs go to files, not this console: uvicorn logs every
    # request and the agent logs a state line roughly once a second, so
    # within a few seconds either one scrolls the connection URL below off
    # the screen entirely -- confirmed the hard way on a real install, where
    # it was the actual reason the URL looked "impossible to find". This
    # console now only ever prints what run_launcher() itself writes.
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    backend_log = open_log(logs_dir / "backend.log")
    agent_log = open_log(logs_dir / "agent.log")

    print(f"IT-Deck v{ITDECK_VERSION} starting...")
    print(f"Data/config: {data_dir}")

    # Created before anything is spawned, so both children can join it the
    # instant they exist. See create_child_job() for what it is for; the
    # short version is that it is the only teardown path that still works
    # when this process is ended by Task Manager.
    child_job = create_child_job()
    if child_job is None:
        print("Note: running without the cleanup job -- children will be stopped "
              "by the usual paths only.")

    strings = _STRINGS[_detect_ui_lang()]
    if port_already_serving(port):
        report_startup_failure(strings["port_busy"].format(port=port))
        return 1
    # CREATE_NO_WINDOW: without it each child would allocate its own console
    # window, since the parent (built --windowed) has none to inherit -- two
    # terminal windows flashing onto the desktop at every launch. Their output
    # already goes to the log files opened above.
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # Below normal priority, so a game (or anything else in the foreground)
    # always wins the CPU when both want it; the backend's work -- relaying a
    # press, diffing a state tick -- can wait a few milliseconds. The whole
    # process is safe to lower because it starts nothing. The agent does start
    # things (every program a tile launches inherits a below-normal class from
    # its parent), so it lowers only its own thread instead -- see
    # agents/windows/agent.py's _yield_to_foreground_apps().
    below_normal = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    backend_proc = subprocess.Popen(
        self_invocation("backend"), env=backend_env, stdout=backend_log,
        stderr=subprocess.STDOUT, creationflags=no_window | below_normal,
    )
    assign_to_child_job(child_job, backend_proc)

    if not wait_for_health(port, proc=backend_proc):
        report_startup_failure(strings["backend_failed"].format(log=logs_dir / "backend.log"))
        backend_proc.terminate()
        return 1

    # Read after wait_for_health(), never before: the item table doesn't
    # exist until the backend's startup hook has run init_db()/seed_if_empty()
    # and the fixups, and on a first-ever launch that is the same moment the
    # VPN item comes into being.
    def spawn_agent() -> subprocess.Popen:
        # Re-read on every spawn, not once: a VPN process name changed in
        # Studio then takes effect on the next agent restart rather than
        # requiring a full IT-Deck restart. config.env still wins -- setdefault
        # semantics, so a hand-set VPN_PROCESS_NAME is never overwritten.
        name = watched_process_name(data_dir)
        env = dict(agent_env)
        if name and not env.get("VPN_PROCESS_NAME"):
            env["VPN_PROCESS_NAME"] = name
        proc = subprocess.Popen(
            self_invocation("agent"), env=env, stdout=agent_log,
            stderr=subprocess.STDOUT, creationflags=no_window,
        )
        # Every spawn, not just the first: the supervisor below respawns the
        # agent on a crash, and an unassigned respawn is exactly the orphan
        # the job exists to prevent.
        assign_to_child_job(child_job, proc)
        return proc

    agent_proc = spawn_agent()
    agent_started_at = time.time()
    # A one-slot holder so the info window can ask about the *current* agent:
    # the supervisor loop below rebinds agent_proc on every respawn, and a
    # closure over the variable would keep reporting on a process that is
    # already gone.
    agent_handle = {"proc": agent_proc}

    # Pick one link to lead with, not three -- a phone user has no way to
    # tell which of several printed addresses is the right one.
    primary_ip, other_ips = detect_primary_and_other_ips()
    if primary_ip is None:
        primary_ip = other_ips[0] if other_ips else "127.0.0.1"
        other_ips = other_ips[1:]
    dashboard_url = f"http://{primary_ip}:{port}/?token={client_token}"
    studio_url = f"http://{primary_ip}:{port}/studio.html"

    # A same-machine connect only proves the port is listening -- it cannot
    # prove another device can reach this address (see
    # detect_primary_and_other_ips()) -- so this stays a soft hint, not the
    # thing that picked primary_ip.
    reachable = check_reachable(primary_ip, port)

    print()
    print("=" * 64)
    print(f"  Dashboard (open on your phone, same Wi-Fi): {dashboard_url}")
    print(f"  Studio (this PC): {studio_url}")
    if not reachable:
        print("  <-- not reachable from another device yet -- check the Windows")
        print("      Firewall prompt (Allow access) if one appeared.")
    if other_ips:
        print(f"  If that doesn't work, this PC also has: {', '.join(other_ips)}")
    print("=" * 64)
    print("A window with these links (and a copy button) should have opened.")
    print("Stop IT-Deck with the Quit button in that window.")
    print()

    # Set by the info window's Quit button, which runs on the tkinter thread
    # and must not tear down processes itself -- the supervisor loop below
    # owns that, and doing it from two threads would leave orphans.
    quit_requested = threading.Event()

    # The update check runs on its own thread so a slow or unreachable GitHub
    # can never delay startup, and hands its answer to the window through a
    # Queue -- tkinter may only be touched from the thread running its own
    # mainloop. Opt out with UPDATE_CHECK=0 in config.env; a config.env
    # written before this existed has no such key, which reads as enabled.
    update_queue: queue.Queue = queue.Queue(maxsize=1)
    if config.get("UPDATE_CHECK", "1").strip().lower() in ("0", "no", "off", "false"):
        update_queue.put(None)
    else:
        threading.Thread(target=_update_check_worker, args=(update_queue,), daemon=True).start()

    def stop_children(reason: str) -> None:
        """Terminate the backend and the agent, and wait until they are gone.

        Shared by the two paths that must leave nothing of IT-Deck running:
        Windows ending the session, and the uninstall. "Gone" rather than
        "asked to go" is the point in both -- until their handles are
        signalled they still hold python312.dll inside the unpacked _MEIxxxx
        directory, which is what decides whether a shutdown stalls (10.8a) or
        an uninstall leaves that directory behind.
        """
        print(reason, flush=True)
        children = [agent_handle["proc"], backend_proc]
        for proc in children:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        # One budget each, not one shared between them: computing the second
        # wait's timeout from a deadline the first had already consumed meant
        # it got zero and returned immediately without waiting for anything.
        for proc in children:
            try:
                proc.wait(timeout=SESSION_END_CHILD_GRACE)
            except Exception:
                pass
        alive = [proc.pid for proc in children if proc.poll() is None]
        if alive:
            # Nothing further can be done from here -- terminate() already is
            # TerminateProcess on Windows, so kill() would be the same call
            # again -- but say so, because this line in launcher.log is what
            # explains a temp directory that could not be removed.
            print(f"Warning: {alive} still running after terminate().", flush=True)
        else:
            print("Backend and agent gone -- the unpacked temp directory is "
                  "free to delete.", flush=True)
        try:
            sys.stdout.flush()
        except Exception:
            pass

    def uninstall_itdeck() -> None:
        """Handed to the info window; runs on its thread and never returns."""
        perform_uninstall(
            data_dir,
            stop_children=lambda: stop_children("Uninstall requested -- stopping IT-Deck."),
        )

    def stop_for_session_end() -> None:
        # Runs when Windows is shutting down, restarting or logging off, on
        # whichever window-procedure thread was told first -- not on the
        # supervisor loop below, which polls once a second and is far too slow
        # to be what Windows waits on. It therefore does the teardown itself
        # rather than signalling quit_requested: this is the one case where
        # the loop does not get to own it. terminate() rather than a graceful
        # stop, because every one of these processes is about to be killed by
        # the shutdown anyway.
        #
        # Waiting for them to be *gone* is the part that matters, and it is
        # not tidiness. The backend and the agent are re-invocations of this
        # same exe and share its unpacked _MEIxxxx directory -- each has
        # python312.dll mapped out of it. Until their process handles are
        # signalled the directory cannot be deleted, and when PyInstaller's
        # parent fails to delete it, it puts up a modal "Failed to remove
        # temporary directory" warning. A modal dialog during shutdown is a
        # shutdown that never finishes, which is precisely the bug v0.4.2
        # left behind.
        stop_children("Windows is ending the session -- stopping IT-Deck.")

    session_end_trigger = install_session_end_handler(stop_for_session_end)

    # Set by the window's "Start the agent" button (Tk thread); the supervisor
    # loop below owns every spawn, so it is the one that acts on it.
    agent_start_requested = threading.Event()

    # Which "What's new" notes to show, decided and recorded now: shown once
    # per version however the window is closed. A first run records the
    # current version without showing anything -- the tour is its welcome.
    whats_new = []
    if first_run:
        mark_whats_new_seen(data_dir, ITDECK_VERSION)
    else:
        whats_new = whats_new_to_show(
            load_whats_new(frontend_dir()), ITDECK_VERSION, read_whats_new_seen(data_dir)
        )
        if whats_new:
            mark_whats_new_seen(data_dir, ITDECK_VERSION)

    autostart = None
    if is_frozen():
        import winreg

        exe = Path(sys.executable)
        autostart = (
            lambda: autostart_enabled(winreg, exe),
            lambda enabled: set_autostart(winreg, exe, enabled),
        )

    show_info_window(
        dashboard_url,
        studio_url,
        agent_token,
        logs_dir,
        quit_requested.set,
        update_queue,
        other_ips,
        lambda: agent_handle["proc"].poll() is None,
        session_end_trigger,
        uninstall_itdeck,
        on_start_agent=agent_start_requested.set,
        first_run=first_run,
        client_token=client_token,
        config_path=data_dir / CONFIG_FILENAME,
        port=port,
        whats_new=whats_new,
        autostart=autostart,
    )
    time.sleep(1.5)  # let the console block above actually be visible for a moment first
    hide_console()

    # Supervise the agent rather than only reporting its death. The legacy
    # Docker path gets its stability from a human watching a console window
    # that stays open on a crash ("a window that stays open means the agent
    # crashed" -- CLAUDE.md); standalone hides that console a couple of seconds
    # in, so nobody sees it, and the deck simply goes half-dead with no
    # explanation. Respawning is what makes the two paths equally reliable.
    #
    # Only an unexpected exit is a crash worth restarting on sight: exit 0 is
    # the Close Agent tile and is left alone, and exit 3 (the singleton mutex)
    # is retried a bounded number of times -- see the constants for why that
    # one is not simply "deliberate".
    agent_restart_delay = AGENT_RESTART_MIN_DELAY
    agent_restart_due: Optional[float] = None
    agent_mutex_retries = 0
    try:
        while True:
            if quit_requested.is_set():
                print("Quit requested from the IT-Deck window -- stopping.")
                break
            if backend_proc.poll() is not None:
                print("Backend process exited -- stopping.")
                break

            if agent_start_requested.is_set():
                agent_start_requested.clear()
                if agent_proc.poll() is not None:
                    # A person asked for it: start now, with a clean slate --
                    # this is not a crash and must not inherit the backoff or
                    # the mutex budget of whatever stopped it.
                    print("Agent start requested from the IT-Deck window.")
                    agent_restart_due = time.time()
                    agent_restart_delay = AGENT_RESTART_MIN_DELAY
                    agent_mutex_retries = 0

            agent_status = agent_proc.poll()
            if agent_status is not None and agent_restart_due is None:
                if agent_status in AGENT_DELIBERATE_EXIT_CODES:
                    print(f"Agent stopped on request (code {agent_status}) -- not restarting it.")
                    # Nothing schedules a restart, and poll() keeps returning
                    # 0, so this prints once and then stays quiet.
                    agent_restart_due = float("inf")
                elif agent_status == AGENT_EXIT_ALREADY_RUNNING:
                    agent_mutex_retries += 1
                    if agent_mutex_retries > AGENT_MUTEX_RETRIES:
                        print(
                            "Another IT-Deck agent holds the singleton mutex after "
                            f"{AGENT_MUTEX_RETRIES} attempts -- giving up. Quit the other "
                            "IT-Deck (or end ITDeck.exe in Task Manager) and relaunch."
                        )
                        agent_restart_due = float("inf")
                    else:
                        print(
                            "Agent found another instance holding the mutex -- retrying in "
                            f"{agent_restart_delay:.0f}s "
                            f"({agent_mutex_retries}/{AGENT_MUTEX_RETRIES})."
                        )
                        agent_restart_due = time.time() + agent_restart_delay
                else:
                    print(
                        f"Agent exited unexpectedly (code {agent_status}) -- restarting in "
                        f"{agent_restart_delay:.0f}s. See {logs_dir / 'agent.log'}."
                    )
                    agent_restart_due = time.time() + agent_restart_delay

            if agent_restart_due is not None and time.time() >= agent_restart_due:
                agent_proc = spawn_agent()
                agent_handle["proc"] = agent_proc
                print("Agent restarted.")
                agent_restart_due = None
                # Backoff climbs across consecutive crashes so a genuinely
                # broken agent (a missing audio device, a bad config) doesn't
                # spin the CPU respawning itself several times a second, and
                # resets below once one has survived long enough to count as
                # working rather than crash-looping.
                agent_restart_delay = min(agent_restart_delay * 2, AGENT_RESTART_MAX_DELAY)
                agent_started_at = time.time()
            elif agent_restart_due is None and agent_proc.poll() is None:
                if time.time() - agent_started_at > AGENT_HEALTHY_AFTER:
                    agent_restart_delay = AGENT_RESTART_MIN_DELAY
                    # An agent that has run this long won the mutex, so the
                    # budget is for the *next* contended start, not this one.
                    agent_mutex_retries = 0

            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        for proc in (agent_proc, backend_proc):
            if proc.poll() is None:
                proc.terminate()
        for proc in (agent_proc, backend_proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        backend_log.close()
        agent_log.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="IT-Deck standalone launcher")
    parser.add_argument("--role", choices=["launcher", "backend", "agent"], default="launcher")
    args = parser.parse_args()

    if args.role == "backend":
        return run_backend()
    if args.role == "agent":
        return run_agent()

    # Only the launcher role redirects. The children already had their stdout
    # and stderr pointed at backend.log / agent.log by the parent's Popen, and
    # redirecting again here would reassign sys.stdout inside them and funnel
    # every uvicorn request line into launcher.log instead -- which is exactly
    # what happened the first time this was wired up at module level.
    _redirect_output_to_log()
    return run_launcher()


if __name__ == "__main__":
    sys.exit(main())
