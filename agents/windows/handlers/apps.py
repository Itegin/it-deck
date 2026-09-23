"""Studio helpers for quick-launch tiles: installed apps and site icons.

Both are queries, not tile actions: Studio asks through
POST /api/agents/{name}/list_apps and /fetch_icon (backend/app/api/agents.py)
and writes whatever comes back into the tile's params.

Both run on the receive loop's own thread, like every other handler, and
keep inside the 5s command budget with deadlines of their own (a child
process timeout; a deadline checked between network reads). They used to run
on a worker thread, and that crashed the frozen agent with an access
violation (exit 0xC0000005): the allocations there triggered garbage
collection, which released pycaw's comtypes pointers -- created on the
agent's main thread -- from the wrong thread. Keep them off worker threads.
"""

import base64
import gc
import io
import ipaddress
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from PIL import Image

_QUERY_BUDGET_SECONDS = 4.0


# --- list_apps --------------------------------------------------------------

# Shortcut names that are never what a person wants on a deck.
_SKIP_WORDS = ("uninstall", "unins", "удалить", "удаление", "деинсталл", "readme", "help", "справка", "website", "documentation")


def _start_menu_dirs() -> list[str]:
    dirs = []
    for root in (os.environ.get("ProgramData"), os.environ.get("APPDATA")):
        if root:
            dirs.append(os.path.join(root, "Microsoft", "Windows", "Start Menu", "Programs"))
    return dirs


# Resolving a .lnk needs COM (IShellLink). Done in a short-lived PowerShell
# child, never in this process: the agent already holds comtypes pointers for
# the audio stack, and initialising and tearing down COM on a worker thread
# here got those released on the wrong thread -- the frozen agent crashed
# with access violations on the first list_apps. A child process shares none
# of that. The script prints one JSON array; paths may be Cyrillic, hence
# UTF-8 output.
_LIST_APPS_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$shell = New-Object -ComObject WScript.Shell
$roots = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs", "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
$out = foreach ($root in $roots) {
  if (Test-Path -LiteralPath $root) {
    Get-ChildItem -LiteralPath $root -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object {
      try {
        $link = $shell.CreateShortcut($_.FullName)
        [pscustomobject]@{ name = $_.BaseName; path = $link.TargetPath; args = $link.Arguments }
      } catch {}
    }
  }
}
# Store (MSIX) apps have no .lnk and no exe of their own to start -- Claude,
# WhatsApp and the like. Windows starts them through Explorer by AppID.
$store = Get-StartApps | Where-Object { $_.AppID -like '*!*' } | ForEach-Object {
  [pscustomobject]@{ name = $_.Name; path = '%SystemRoot%\explorer.exe'; args = "shell:AppsFolder\$($_.AppID)" }
}
ConvertTo-Json -InputObject @(@($out) + @($store)) -Compress
"""


def _scan_apps() -> dict:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", _LIST_APPS_SCRIPT],
        capture_output=True,
        timeout=_QUERY_BUDGET_SECONDS - 0.3,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError("could not read the Start Menu")
    raw = json.loads(result.stdout.decode("utf-8-sig") or "[]")
    apps: dict[str, dict] = {}
    for entry in raw if isinstance(raw, list) else [raw]:
        name = str(entry.get("name") or "")
        target = str(entry.get("path") or "")
        if not name or any(word in name.lower() or word in os.path.basename(target).lower() for word in _SKIP_WORDS):
            continue
        # Only real programs: a shortcut to a folder, a document or a Store
        # app has no exe to hand launch_app.
        expanded = os.path.expandvars(target)
        if not expanded.lower().endswith(".exe") or not os.path.isfile(expanded):
            continue
        apps.setdefault(name.lower(), {"name": name, "path": target, "args": str(entry.get("args") or "")})
    return {"status": "ok", "apps": sorted(apps.values(), key=lambda a: a["name"].lower())}


# The scan takes 1-3 s (a PowerShell start plus Get-StartApps), which is most
# of the 5 s budget, so a result is reused for a minute: Studio asks again
# every time the picker opens, and installed programs don't change that fast.
_APPS_CACHE_SECONDS = 60.0
_apps_cache = {"at": 0.0, "result": None}


def handle_list_apps(params: dict) -> dict:
    if _apps_cache["result"] is not None and time.monotonic() - _apps_cache["at"] < _APPS_CACHE_SECONDS:
        return _apps_cache["result"]
    try:
        result = _scan_apps()
        _apps_cache.update(at=time.monotonic(), result=result)
        return result
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "took too long -- try again"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# --- fetch_icon -------------------------------------------------------------

_PAGE_LIMIT = 512 * 1024
_ICON_LIMIT = 256 * 1024
_ICON_SIZE = 64
# Icons are small. A "favicon" declaring thousands of pixels a side is either
# a mistake or an attempt to make the agent allocate hundreds of megabytes.
_ICON_MAX_SIDE = 1024
# Only what sites actually serve icons as. Pillow would otherwise try every
# decoder it has (TIFF, PSD, ...) on bytes a remote site chose.
_ICON_FORMATS = ["PNG", "ICO", "JPEG", "GIF", "WEBP", "BMP"]
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IT-Deck icon fetch"


# The host the person typed. A page (or a redirect) may point the fetch at
# any other public host, but not into the LAN: otherwise a site could make
# the agent send GETs to the router or a local service. The typed host itself
# is exempt -- asking for the icon of your own NAS is a fair request.
_allowed_private_host = threading.local()


# 198.18.0.0/15 is the benchmarking range, which ipaddress counts as private
# -- and it is exactly where VPN clients in "fake-IP" mode (v2Ray/Clash TUN)
# resolve *every public site*. It is never a real LAN, so it is let through,
# or site icons would stop working whenever such a VPN is on.
_FAKE_IP_RANGE = ipaddress.ip_network("198.18.0.0/15")


_DNS_TIMEOUT = 2.0
_dns_cache: dict = {}
# The whole fetch's deadline (time.monotonic()), so no single lookup can
# spend time the request no longer has.
_fetch_deadline = {"at": None}


def _resolve(host: str):
    """getaddrinfo with a time limit; None when it fails or takes too long.

    getaddrinfo has no timeout of its own -- a name the local DNS won't
    answer took 11 s here -- so it runs on a throwaway thread. Garbage
    collection is switched off while it does: that thread must never be the
    one a collection runs on (see the module docstring for what that does to
    pycaw's COM pointers). It does nothing but the one C call.
    """
    if host in _dns_cache:
        return _dns_cache[host]
    box = []
    worker = threading.Thread(target=lambda: box.append(_try_getaddrinfo(host)), daemon=True)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        worker.start()
        limit = _DNS_TIMEOUT
        if _fetch_deadline["at"] is not None:
            limit = max(0.1, min(limit, _fetch_deadline["at"] - time.monotonic()))
        worker.join(limit)
    finally:
        if was_enabled:
            gc.enable()
    result = box[0] if box else None
    _dns_cache[host] = result
    return result


def _try_getaddrinfo(host: str):
    try:
        return socket.getaddrinfo(host, None)
    except OSError:
        return None


def _is_private_host(host: str) -> bool:
    infos = _resolve(host)
    if not infos:
        return False  # unresolvable: _get refuses it before connecting
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if address in _FAKE_IP_RANGE:
            continue
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return True
    return False


def _permitted(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    if parts.hostname == getattr(_allowed_private_host, "host", None):
        return True
    return not _is_private_host(parts.hostname)


class _WebOnlyRedirects(urllib.request.HTTPRedirectHandler):
    # urllib would follow a redirect to ftp:, or into the LAN; the icon fetch
    # stays on the public web. Three hops is plenty for a favicon.
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _permitted(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Built by hand rather than build_opener() so file: and ftp: handlers are
# never installed -- a site's <link rel=icon href="file:..."> must not read
# the PC's disk.
_opener = urllib.request.OpenerDirector()
for _handler in (
    # The system proxy, as build_opener() would have added: a VPN client in
    # system-proxy mode must see these requests like any other.
    urllib.request.ProxyHandler(),
    urllib.request.HTTPHandler(),
    urllib.request.HTTPSHandler(),
    _WebOnlyRedirects(),
    urllib.request.HTTPErrorProcessor(),
    urllib.request.HTTPDefaultErrorHandler(),
):
    _opener.add_handler(_handler)


def _get(url: str, limit: int, deadline: float) -> tuple[bytes, str, str]:
    """GET `url`; return (body, final_url, content_type). Refuses oversize bodies."""
    remaining = deadline - time.monotonic()
    if remaining < 0.3:
        raise TimeoutError("out of time")
    if not _permitted(url):
        raise ValueError("address not allowed")
    if not _resolve(urlsplit(url).hostname):
        raise ValueError("can't reach this site")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with _opener.open(request, timeout=min(1.5, remaining)) as response:
        # In chunks against the deadline: the socket timeout is per read, so a
        # server dripping a byte a second would otherwise hold this for minutes.
        chunks = []
        size = 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("out of time")
            chunk = response.read(16384)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError("response too large")
            chunks.append(chunk)
        return b"".join(chunks), response.geturl(), response.headers.get("Content-Type", "")


class _IconLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.candidates: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        rel = attr.get("rel", "").lower().split()
        href = attr.get("href", "")
        if not href or href.lower().endswith(".svg") or "svg" in attr.get("type", ""):
            return  # Pillow cannot rasterise SVG
        if "apple-touch-icon" in rel:
            self.candidates.append((180, href))
        elif "icon" in rel:
            size = 16
            for token in attr.get("sizes", "").lower().split():
                w = token.split("x")[0]
                if w.isdigit():
                    size = max(size, int(w))
            self.candidates.append((size, href))


def _icon_candidates(site: str, deadline: float) -> list[str]:
    urls: list[str] = []
    try:
        page, final_url, content_type = _get(site, _PAGE_LIMIT, deadline)
        if "html" in content_type.lower():
            parser = _IconLinks()
            parser.feed(page.decode("utf-8", errors="replace"))
            # Nearest to 64px from above is the sharpest downscale; tiny ones last.
            ranked = sorted(parser.candidates, key=lambda c: (c[0] < _ICON_SIZE, abs(c[0] - 128)))
            urls += [urljoin(final_url, href) for _, href in ranked]
        base = final_url
    except Exception:
        base = site
    parts = urlsplit(base)
    # Google's favicon service next: plenty of big sites (ChatGPT, for one) turn
    # away a non-browser client, and it answers for them at 64px. It learns
    # the site's domain, nothing else -- and only when a person presses
    # "site icon" in Studio. Then the conventional locations: many sites serve
    # a 180px touch icon without linking it, which beats a 16px favicon.ico.
    urls.append(f"https://www.google.com/s2/favicons?sz=64&domain={parts.netloc}")
    urls.append(f"{parts.scheme}://{parts.netloc}/apple-touch-icon.png")
    urls.append(f"{parts.scheme}://{parts.netloc}/favicon.ico")
    return [u for u in dict.fromkeys(urls) if urlsplit(u).scheme in ("http", "https")]


def _decode(raw: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(raw), formats=_ICON_FORMATS)
    # Checked from the header, before a single pixel is decoded.
    if max(image.size) > _ICON_MAX_SIDE:
        raise ValueError("image too large")
    image.load()
    return image.convert("RGBA")


def _to_data_uri(image: Image.Image) -> str:
    image.thumbnail((_ICON_SIZE, _ICON_SIZE), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


def _fetch_icon(url: str) -> dict:
    # Set first: the lookup just below must not run against the previous
    # call's (long expired) deadline.
    _fetch_deadline["at"] = time.monotonic() + _QUERY_BUDGET_SECONDS - 0.3
    _allowed_private_host.host = urlsplit(url).hostname
    _dns_cache.clear()
    if not _resolve(urlsplit(url).hostname):
        return {"status": "error", "message": "This PC can't reach that site. Check the address, or pick a logo instead."}
    deadline = _fetch_deadline["at"]
    best = None
    for candidate in _icon_candidates(url, deadline)[:6]:
        try:
            raw, _final, _type = _get(candidate, _ICON_LIMIT, deadline)
            image = _decode(raw)
        except Exception:
            continue
        if best is None or image.width > best.width:
            best = image
        if best.width >= 32:
            break  # sharp enough; a 16px favicon only if nothing better exists
    if best is not None:
        return {"status": "ok", "icon": _to_data_uri(best)}
    return {"status": "error", "message": "This site has no icon we could use -- pick a logo or text instead."}


def handle_fetch_icon(params: dict) -> dict:
    url = (params.get("url") or "").strip()
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        return {"status": "error", "message": "Only a web address (http or https) has a site icon."}
    try:
        return _fetch_icon(url)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
