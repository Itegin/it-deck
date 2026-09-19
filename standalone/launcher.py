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
ITDECK_VERSION = "0.4.5"

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
        stream = open(logs / "launcher.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    except Exception:
        # Last resort: swallow writes rather than let a logging failure stop
        # IT-Deck from starting. os.devnull always opens.
        try:
            sys.stdout = sys.stderr = open(os.devnull, "w")
        except Exception:
            pass


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
    if is_frozen():
        os.environ.setdefault("ITDECK_FRONTEND_DIR", str(Path(sys._MEIPASS) / "frontend"))
    else:
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        os.environ.setdefault("ITDECK_FRONTEND_DIR", str(REPO_ROOT / "frontend"))

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
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut_path}'); "
            f"$s.TargetPath = '{exe_path}'; "
            f"$s.WorkingDirectory = '{Path(exe_path).parent}'; "
            f"$s.IconLocation = '{exe_path}'; "
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

# Same palette as the Dashboard's own "Liquid Glass" theme (see
# frontend/css/themes.css's dark [data-theme="liquid-glass"] block) --
# tkinter can't do that theme's actual backdrop-filter frost (no blur
# compositing), so this borrows its *colors* (deep blue-black ground,
# lifted surface, purple/teal accents) rather than trying to fake glass
# with gradients tkinter can't draw either. The one piece of real
# translucency available on this platform -- Windows 11's Mica material --
# is applied separately, best-effort, in _apply_windows11_chrome() below.
_GLASS = {
    "bg": "#070a11",
    "surface": "#151a23",
    "border": "#2f3644",
    "text": "#F1F5F9",
    "text_muted": "#9aa3b2",
    "accent": "#a78bfa",
    "accent_active": "#8e5ff5",
    # Added for the step cards: "surface" is the card fill, so buttons
    # sitting on a card need to be a shade above it to read as raised.
    # "ok" is the running dot in the header -- the one non-purple accent,
    # because green means running in every other status UI a person has
    # ever used.
    "surface_raised": "#1d2330",
    "ok": "#4ade80",
    "warn": "#f59e0b",
    # The deck's own alert red (themes.css's --state-alert), reused here so
    # "red means something is about to be destroyed" is one decision across
    # both surfaces rather than two similar-looking ones.
    "danger": "#dc2626",
    "danger_active": "#b91c1c",
}

_STRINGS = {
    "en": {
        "title": "IT-Deck",
        "running": "IT-Deck is running",
        "agent_down": "the agent is not running -- tiles that control this PC won't work",
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
            "   •  its Windows Firewall rules"
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
            "   •  правила брандмауэра Windows для него"
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
        f"Where-Object {{ $_.Program -eq '{exe}' }} | Measure-Object).Count"
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

    inner = (
        "Get-NetFirewallApplicationFilter | "
        f"Where-Object {{ $_.Program -eq '{exe}' }} | "
        "Get-NetFirewallRule | Remove-NetFirewallRule"
    )
    # Two levels: the outer powershell asks for elevation and waits for the
    # inner one, so the UAC prompt is resolved before this returns.
    outer = (
        "Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait -ArgumentList "
        f"'-NoProfile','-ExecutionPolicy','Bypass','-Command',\"{inner}\""
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", outer],
            capture_output=True,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass  # declined, timed out, or no such rules -- the rest still goes


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
        targets=", ".join(f"'{t}'" for t in wanted),
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

    The inventory, and it is the whole inventory -- IT-Deck writes nothing to
    the registry, installs no service and registers no scheduled task:

    - `%LOCALAPPDATA%\\IT-Deck\\` — config.env with both tokens, the tile
      database, the logs
    - the Desktop shortcut the first launch created
    - the Windows Firewall rules that name this exe (see above)
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

        s = _STRINGS[_detect_ui_lang()]
        g = _GLASS

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
        style.configure(
            "Glass.TEntry",
            fieldbackground=g["surface"],
            foreground=g["text"],
            insertcolor=g["text"],
            borderwidth=1,
            relief="flat",
        )
        style.configure(
            "Glass.TButton",
            background=g["surface_raised"],
            foreground=g["text"],
            borderwidth=1,
            relief="flat",
            padding=(10, 6),
        )
        style.map("Glass.TButton", background=[("active", g["border"])])
        style.configure(
            "Accent.TButton",
            background=g["accent_active"],
            foreground=g["bg"],
            borderwidth=0,
            relief="flat",
            padding=(10, 6),
        )
        style.map("Accent.TButton", background=[("active", g["accent"])])
        style.configure(
            "Mini.TButton",
            background=g["surface_raised"],
            foreground=g["text_muted"],
            borderwidth=1,
            relief="flat",
            padding=(6, 2),
            font=("Segoe UI", 8),
        )
        style.map("Mini.TButton", background=[("active", g["border"])])
        # The only red in this window, and it is spent on the one button that
        # destroys something. Same alert red the deck uses for a muted mic, so
        # the two surfaces agree about what red means.
        style.configure(
            "Danger.TButton",
            background=g["danger"],
            foreground="#ffffff",
            borderwidth=0,
            relief="flat",
            padding=(10, 6),
        )
        style.map(
            "Danger.TButton",
            background=[("active", g["danger_active"]), ("disabled", g["surface_raised"])],
            foreground=[("disabled", g["text_muted"])],
        )
        # clam draws a light focus/border ring on an Entry, which on a
        # read-only field that exists only to be copied reads as "this is
        # selected, type here". Pin every border colour to the card edge.
        style.map(
            "Glass.TEntry",
            bordercolor=[("focus", g["border"]), ("!focus", g["border"])],
            lightcolor=[("focus", g["border"]), ("!focus", g["border"])],
            darkcolor=[("focus", g["border"]), ("!focus", g["border"])],
            fieldbackground=[("readonly", g["surface"])],
            foreground=[("readonly", g["text"])],
        )

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

        def card(number: str, title: str) -> tk.Frame:
            """One numbered step: a flat panel with a badge, a title, a body.

            tkinter has no rounded corners, no shadow and no blur, so the
            separation between a step and the background is carried entirely
            by a lighter fill and generous padding. That is the whole visual
            vocabulary available here; anything else would be a lie.
            """
            outer = tk.Frame(root, bg=g["surface"])
            outer.pack(fill="x", padx=PAD, pady=(0, 8))
            head = tk.Frame(outer, bg=g["surface"])
            head.pack(fill="x", padx=12, pady=(10, 6))
            tk.Label(
                head,
                text=f" {number} ",
                font=("Segoe UI", 9, "bold"),
                bg=g["accent"],
                fg=g["bg"],
            ).pack(side="left")
            tk.Label(
                head,
                text=title,
                font=("Segoe UI", 10, "bold"),
                bg=g["surface"],
                fg=g["text"],
            ).pack(side="left", padx=(8, 0))
            body = tk.Frame(outer, bg=g["surface"])
            body.pack(fill="x", padx=12, pady=(0, 12))
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

        header = tk.Frame(root, bg=g["bg"])
        header.pack(fill="x", padx=PAD, pady=(14, 8))
        status_dot = tk.Label(header, text="●", font=("Segoe UI", 9), bg=g["bg"], fg=g["ok"])
        status_dot.pack(side="left", padx=(0, 6))
        label(header, s["running"], bold=True, size=13).pack(side="left")
        label(header, f"v{ITDECK_VERSION}", muted=True, size=9).pack(side="right")

        # A dot that is always green is decoration pretending to be status.
        # The launcher knows whether the agent process is alive -- it
        # supervises it -- so the dot reports that, and a line appears saying
        # what it means for the user. Without this, an agent that died (or one
        # that lost the singleton mutex five times and gave up) leaves the
        # window showing three confident steps and a healthy green dot.
        agent_warning = label(root, s["agent_down"], muted=True, size=8, wrap=460)

        def poll_agent() -> None:
            try:
                alive = bool(agent_alive())
            except Exception:
                alive = True  # never let a broken probe raise an alarm
            status_dot.configure(fg=g["ok"] if alive else g["warn"])
            if alive:
                if agent_warning.winfo_ismapped():
                    # Shrink back too, not just hide: fit_window() is the only
                    # thing that resizes an explicitly-sized window, so
                    # without this the window keeps the taller geometry after
                    # the agent recovers.
                    agent_warning.pack_forget()
                    fit_window()
            elif not agent_warning.winfo_ismapped():
                agent_warning.pack(anchor="w", padx=PAD, pady=(0, 6), after=header)
                fit_window()
            root.after(AGENT_STATUS_POLL_MS, poll_agent)

        if agent_alive is not None:
            root.after(AGENT_STATUS_POLL_MS, poll_agent)

        separator = tk.Frame(root, bg=g["border"], height=1)
        separator.pack(fill="x", padx=PAD, pady=(0, 10))

        # --- update notice (hidden until the check says otherwise) -----------

        update_bar = tk.Frame(root, bg=g["surface"])
        update_text = label(update_bar, "", size=9)
        update_text.pack(side="left", padx=(12, 0), pady=8)
        ttk.Button(
            update_bar,
            text=s["update_download"],
            style="Accent.TButton",
            command=lambda: webbrowser.open(RELEASES_PAGE_URL),
        ).pack(side="right", padx=12, pady=8)

        def show_update(version: str) -> None:
            update_text.configure(text=s["update_available"].format(version=version))
            update_bar.pack(fill="x", padx=PAD, pady=(0, 10), after=separator)
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
            qr.pack(side="left", padx=(0, 12))

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
        ttk.Button(
            dash_row,
            text=s["copy"],
            style="Accent.TButton",
            command=lambda: copy_text(dashboard_url, dash_feedback),
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

        # --- step 2: Studio --------------------------------------------------

        step2 = card("2", s["step2_title"])
        label(step2, s["step2_body"], muted=True, wrap=460).pack(anchor="w", pady=(0, 8))

        studio_row = tk.Frame(step2, bg=g["surface"])
        studio_row.pack(fill="x")
        ttk.Button(
            studio_row,
            text=s["open_studio"],
            style="Glass.TButton",
            command=lambda: webbrowser.open(studio_url),
        ).pack(side="left")

        # The agent token used to sit on screen as a bare 32-character string
        # with no explanation, which is exactly the "what is this" the rework
        # is about. It is needed once, by Studio, and only sometimes -- so it
        # is behind a button until it is actually wanted.
        token_holder = tk.Frame(step2, bg=g["surface"])
        token_holder.pack(fill="x", pady=(8, 0))

        def reveal_token() -> None:
            for child in token_holder.winfo_children():
                child.destroy()
            label(token_holder, s["token_hint"], muted=True, size=8).pack(anchor="w")
            row = tk.Frame(token_holder, bg=g["surface"])
            row.pack(fill="x", pady=(2, 0))
            entry = ttk.Entry(
                row, width=20, font=("Consolas", 9), style="Glass.TEntry", takefocus=False
            )
            entry.insert(0, agent_token)
            entry.configure(state="readonly")
            entry.pack(side="left")
            feedback = label(row, "", muted=True, size=8)
            ttk.Button(
                row,
                text=s["copy_token"],
                style="Mini.TButton",
                command=lambda: copy_text(agent_token, feedback),
            ).pack(side="left", padx=(8, 0))
            feedback.pack(side="left", padx=(6, 0))
            # Same reason as the update notice: the window has an explicit
            # geometry by now, so replacing the button with this taller row
            # clips the footer instead of growing the window.
            fit_window()

        ttk.Button(
            token_holder, text=s["show_token"], style="Mini.TButton", command=reveal_token
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

            cancel_button = ttk.Button(
                buttons, text=s["cancel"], style="Glass.TButton", command=close
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

            go_button = ttk.Button(
                buttons, text=s["uninstall_go"], style="Danger.TButton", command=run_uninstall
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
        if on_uninstall is not None and is_frozen():
            ttk.Button(
                step3, text=s["uninstall"], style="Mini.TButton", command=confirm_uninstall
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

        ttk.Button(buttons, text=s["close"], style="Glass.TButton", command=root.iconify).pack(
            side="left"
        )
        ttk.Button(buttons, text=s["quit"], style="Glass.TButton", command=quit_itdeck).pack(
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
    }
    agent_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "AGENT_TOKEN": agent_token,
        "SERVER_IP": "127.0.0.1",
        "SERVER_PORT": str(port),
        "AGENT_NAME": "windows",
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
    backend_log = open(logs_dir / "backend.log", "a", encoding="utf-8")
    agent_log = open(logs_dir / "agent.log", "a", encoding="utf-8")

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
    backend_proc = subprocess.Popen(
        self_invocation("backend"), env=backend_env, stdout=backend_log,
        stderr=subprocess.STDOUT, creationflags=no_window,
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
