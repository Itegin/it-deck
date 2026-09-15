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
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

# Python fully-buffers stdout when it isn't a real console (piped, redirected,
# or -- the case that bit this in testing -- launched under a process
# supervisor). Without this, the connection URL below can sit in a buffer
# and never reach the user at all.
try:
    sys.stdout.reconfigure(line_buffering=True)
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


def load_or_create_config(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / CONFIG_FILENAME
    values = parse_config(config_path)
    changed = False
    if not values.get("AGENT_TOKEN"):
        values["AGENT_TOKEN"] = secrets.token_hex(16)
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
        values["SERVER_PORT"] = str(find_free_port(8000))
        changed = True
    if changed:
        write_config(config_path, values)
    return values


# --- LAN discovery / reachability ------------------------------------------


def detect_primary_and_other_ips() -> tuple[Optional[str], list[str]]:
    # The UDP-connect trick (no packet actually sent) asks the OS which
    # source interface it would use to route to an external address --
    # this is the best available proxy for "the real, phone-reachable LAN
    # adapter", and it's *not* something a same-machine reachability check
    # can substitute for: a socket bound to 0.0.0.0 accepts a local connect
    # to ANY of its own interfaces, including virtual ones (Hyper-V vSwitch,
    # a VPN's virtual adapter) that a phone on the real Wi-Fi can never
    # reach -- confirmed the hard way when this used to rank candidates by
    # local reachability and it happily promoted a 172.16.x.x Hyper-V
    # address over the real 192.168.x.x one.
    primary = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            primary = s.getsockname()[0]
    except OSError:
        pass

    # Every other adapter, for the secondary "other addresses" line -- this
    # app has a first-class VPN toggle, so a VPN becoming the default route
    # (and thus `primary` above) is a real scenario; listing the rest gives
    # the user something to try by hand if the primary guess is wrong.
    others: set[str] = set()
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    if addr.address != primary:
                        others.add(addr.address)
    except Exception:
        pass
    return primary, sorted(others)


def wait_for_health(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def check_reachable(ip: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# --- role: backend -----------------------------------------------------


def run_backend() -> int:
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
}

_STRINGS = {
    "en": {
        "title": "IT-Deck",
        "running": "IT-Deck is running",
        "phone_hint": "Open this on your phone once (same Wi-Fi as this PC):",
        "copy": "Copy link",
        "copied": "Copied",
        "studio_hint": "Studio (edit tiles, this PC only):",
        "open_studio": "Open Studio",
        "agent_token_hint": "If Studio asks for an agent token:",
        "logs_hint": "Logs (for troubleshooting):",
        "close": "Close",
    },
    "ru": {
        "title": "IT-Deck",
        "running": "IT-Deck запущен",
        "phone_hint": "Открой на телефоне один раз (та же сеть Wi-Fi):",
        "copy": "Скопировать",
        "copied": "Скопировано",
        "studio_hint": "Studio (редактирование плиток, только на этом ПК):",
        "open_studio": "Открыть Studio",
        "agent_token_hint": "Если Studio спросит токен агента:",
        "logs_hint": "Логи (для отладки):",
        "close": "Закрыть",
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


def _apply_windows11_chrome(root) -> None:
    # Best-effort only: a dark title bar and, on Windows 11, the same Mica
    # material File Explorer uses -- real OS-composited translucency,
    # rather than trying to fake glass with tkinter drawing primitives that
    # can't do backdrop blur at all. Both calls are silently harmless on
    # Windows 10 or anything older (DwmSetWindowAttribute just returns a
    # failure HRESULT this code doesn't check) -- ctypes never raises on a
    # merely-unsupported attribute, only on a genuinely broken call, which
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
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMSBT_MAINWINDOW = 2  # Mica
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
        )
        dwm.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(ctypes.c_int(DWMSBT_MAINWINDOW)), ctypes.sizeof(ctypes.c_int)
        )
    except Exception:
        pass


def show_info_window(dashboard_url: str, studio_url: str, agent_token: str, logs_dir: Path) -> None:
    # A real GUI window, not another thing to read off the console: the
    # console fills with backend/agent noise (that's why it's redirected to
    # log files below), and a URL a person has to scroll to find is a URL
    # they'll give up on -- which is exactly what happened on a real install.
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
        root.attributes("-topmost", True)
        root.resizable(False, False)
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
        style.configure("Glass.TEntry", fieldbackground=g["surface"], foreground=g["text"],
                         insertcolor=g["text"], borderwidth=1, relief="flat")
        style.configure(
            "Glass.TButton",
            background=g["surface"],
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

        def label(text: str, muted: bool = False, bold: bool = False, size: int = 9) -> tk.Label:
            return tk.Label(
                root,
                text=text,
                font=("Segoe UI", size, "bold" if bold else "normal"),
                bg=g["bg"],
                fg=g["text_muted"] if muted else g["text"],
                anchor="w",
                justify="left",
            )

        def url_row(url: str) -> None:
            entry = ttk.Entry(root, width=52, font=("Consolas", 10), style="Glass.TEntry")
            entry.insert(0, url)
            entry.configure(state="readonly")
            entry.pack(padx=16, pady=(0, 8), fill="x")

        def copy_link(url: str, feedback: tk.Label) -> None:
            root.clipboard_clear()
            root.clipboard_append(url)
            feedback.configure(text=s["copied"])
            root.after(1500, lambda: feedback.configure(text=""))

        label(s["running"], bold=True, size=12).pack(anchor="w", padx=16, pady=(16, 10))

        label(s["phone_hint"]).pack(anchor="w", padx=16, pady=(0, 4))
        url_row(dashboard_url)
        dash_row = tk.Frame(root, bg=g["bg"])
        dash_row.pack(anchor="w", padx=16, pady=(0, 14), fill="x")
        dash_feedback = label("", muted=True)
        ttk.Button(
            dash_row, text=s["copy"], style="Accent.TButton", command=lambda: copy_link(dashboard_url, dash_feedback)
        ).pack(side="left")
        dash_feedback.pack(in_=dash_row, side="left", padx=(10, 0))

        label(s["studio_hint"]).pack(anchor="w", padx=16, pady=(0, 4))
        url_row(studio_url)
        ttk.Button(root, text=s["open_studio"], style="Glass.TButton", command=lambda: webbrowser.open(studio_url)).pack(
            anchor="w", padx=16, pady=(0, 14)
        )

        label(f"{s['agent_token_hint']} {agent_token}", muted=True, size=8).pack(anchor="w", padx=16)
        label(f"{s['logs_hint']} {logs_dir}", muted=True, size=8).pack(anchor="w", padx=16, pady=(0, 10))

        ttk.Button(root, text=s["close"], style="Glass.TButton", command=root.destroy).pack(padx=16, pady=(0, 16))

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
    data_dir = default_data_dir()
    config = load_or_create_config(data_dir)
    port = int(config["SERVER_PORT"])
    agent_token = config["AGENT_TOKEN"]
    client_token = config["CLIENT_TOKEN"]

    backend_env = {
        **os.environ,
        "AGENT_TOKEN": agent_token,
        "CLIENT_TOKEN": client_token,
        "SERVER_PORT": str(port),
        "ITDECK_DATA_DIR": str(data_dir),
    }
    agent_env = {
        **os.environ,
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

    print("IT-Deck starting...")
    print(f"Data/config: {data_dir}")
    backend_proc = subprocess.Popen(
        self_invocation("backend"), env=backend_env, stdout=backend_log, stderr=subprocess.STDOUT
    )

    if not wait_for_health(port):
        print(f"Backend did not come up in time -- check {logs_dir / 'backend.log'} for errors.")
        backend_proc.terminate()
        return 1

    agent_proc = subprocess.Popen(
        self_invocation("agent"), env=agent_env, stdout=agent_log, stderr=subprocess.STDOUT
    )

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
    print("A window with these links (and a copy button) should also have opened.")
    print("Press Ctrl+C to stop IT-Deck.")
    print()

    show_info_window(dashboard_url, studio_url, agent_token, logs_dir)

    agent_exit_reported = False
    try:
        while True:
            if backend_proc.poll() is not None:
                print("Backend process exited -- stopping.")
                break
            if agent_proc.poll() is not None and not agent_exit_reported:
                print(
                    "Agent process exited -- tiles that need the PC (audio, apps, "
                    "VPN) won't respond until it's restarted. Backend keeps running."
                )
                agent_exit_reported = True
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
    return run_launcher()


if __name__ == "__main__":
    sys.exit(main())
