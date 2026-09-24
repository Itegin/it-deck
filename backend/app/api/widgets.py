"""Data providers for widget tiles.

A widget tile (item.kind == "widget") is drawn entirely by the frontend
(frontend/js/widgets/), but some widgets need data the phone can't or
shouldn't fetch itself. Those providers live here, one endpoint each.

Weather comes from Open-Meteo: no API key, so nothing new goes into
config.env. The backend calls it rather than the phone, which gives every
connected deck one shared cache and keeps the phone's view of the outside
world down to this one origin.

Stdlib urllib, not httpx/requests: that means no new dependency for
requirements.txt or the PyInstaller build. The launcher's own update check
already makes HTTPS calls the same way from the frozen exe.
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.auth import check_agent_token

logger = logging.getLogger("controlhub.api")

router = APIRouter()

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

# The same 10s the launcher's update check uses, for the same measured
# reason: with a VPN up, the TLS handshake to a public API sometimes takes
# longer than 5s on the maintainer's machine. A short timeout doesn't fail
# loudly. It just switches the feature off on those networks.
UPSTREAM_TIMEOUT = 10

# Weather doesn't change faster than this, and Open-Meteo's own model
# updates are slower still.
FRESH_SECONDS = 10 * 60
# How long a cached reading may stand in for a failed refresh, flagged stale.
# Six hours of an old temperature beats a dash for the offline hour after a
# router reboot. After that the number is misleading, so it stops.
STALE_SECONDS = 6 * 60 * 60

# The weather endpoint is unauthenticated (the phone calls it and holds only
# CLIENT_TOKEN), so the cache has to be bounded. Coordinates are snapped to this
# grid *before* the upstream URL is built. That way repeated requests for one
# place cost at most one upstream fetch per grid cell per FRESH_SECONDS, and
# the entry cap bounds memory. It does not bound outbound traffic: every new
# cell is a miss and a fetch, so a LAN caller sweeping coordinates could keep
# the threadpool busy. Accepted for a single-user LAN app (see
# docs/DEVELOPMENT.md, Security). 0.01 deg is ~1 km, finer than the forecast
# model's own resolution.
COORD_DECIMALS = 2
MAX_CACHE_ENTRIES = 64

_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "IT-Deck"})
    with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_forecast(payload: dict) -> dict:
    """Reduce Open-Meteo's reply to the handful of fields the widget draws.

    The frontend reads only this shape, so a schema change upstream is fixed
    here and nowhere else.
    """
    current = payload["current"]
    daily = payload.get("daily") or {}

    def first(key: str):
        values = daily.get(key) or []
        return values[0] if values else None

    return {
        "temp": current["temperature_2m"],
        "code": current["weather_code"],
        "is_day": bool(current.get("is_day", 1)),
        "min": first("temperature_2m_min"),
        "max": first("temperature_2m_max"),
    }


def _store(key: tuple[float, float], fetched_at: float, data: dict) -> None:
    with _cache_lock:
        _cache[key] = (fetched_at, data)
        if len(_cache) > MAX_CACHE_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            del _cache[oldest]


def _get_weather(lat: float, lon: float) -> dict:
    key = (round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS))
    now = time.time()

    with _cache_lock:
        cached = _cache.get(key)
    if cached and now - cached[0] < FRESH_SECONDS:
        return {**cached[1], "updated_at": _iso(cached[0]), "stale": False}

    # Built from the snapped key and urlencode, never from the raw query
    # string: nothing the caller typed is interpolated into the upstream URL.
    query = urllib.parse.urlencode(
        {
            "latitude": key[0],
            "longitude": key[1],
            "current": "temperature_2m,weather_code,is_day",
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
            "forecast_days": 1,
        }
    )
    try:
        data = _normalize_forecast(_fetch_json(f"{FORECAST_URL}?{query}"))
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("weather fetch failed for %s: %s", key, exc)
        if cached and now - cached[0] < STALE_SECONDS:
            return {**cached[1], "updated_at": _iso(cached[0]), "stale": True}
        raise HTTPException(status_code=502, detail="weather service unreachable")

    # Stamped after the fetch, and the same stamp is cached, so a cache hit
    # reports exactly the time the fresh reply did.
    fetched_at = time.time()
    _store(key, fetched_at, data)
    return {**data, "updated_at": _iso(fetched_at), "stale": False}


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


@router.get("/api/widgets/weather")
async def weather(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict:
    # No token, deliberately: the phone calls this, and the phone holds only
    # CLIENT_TOKEN, which is a WebSocket handshake rather than a header. It is
    # read-only, like GET /api/workspaces, and the snapped cache above bounds
    # what an anonymous caller can make it do.
    return await run_in_threadpool(_get_weather, lat, lon)



def _geocode(q: str, lang: str) -> list[dict]:
    query = urllib.parse.urlencode({"name": q, "count": 8, "language": lang, "format": "json"})
    try:
        payload = _fetch_json(f"{GEOCODE_URL}?{query}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("geocode failed for %r: %s", q, exc)
        raise HTTPException(status_code=502, detail="geocoding service unreachable")

    return [
        {
            "name": result.get("name", ""),
            "admin1": result.get("admin1") or "",
            "country": result.get("country") or "",
            "lat": result["latitude"],
            "lon": result["longitude"],
        }
        for result in payload.get("results") or []
        if "latitude" in result and "longitude" in result
    ]


@router.get("/api/widgets/geocode")
async def geocode(
    q: str = Query(..., min_length=2, max_length=80),
    lang: str = Query("en", pattern=r"^[a-z]{2}$"),
    x_agent_token: str | None = Header(default=None),
) -> list[dict]:
    # Gated, unlike weather: geocoding is Studio-only, and Studio already
    # holds the agent token, so there is no reason to leave this one open.
    check_agent_token(x_agent_token)
    return await run_in_threadpool(_geocode, q.strip(), lang)
