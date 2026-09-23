// The "Clock & weather" widget. Params:
//
//   city          display name, written by Studio's city search
//   lat, lon      coordinates. No weather half is drawn without them
//   show_seconds  true ticks every second instead of every minute
//
// The time is the phone's own clock and locale. The weather comes from
// GET /api/widgets/weather, which the backend caches for 10 minutes, so
// polling that often here costs Open-Meteo nothing extra.

const WEATHER_REFRESH_MS = 10 * 60 * 1000;

// ── Seven-segment face ─────────────────────────────────────────────────────
// What the offline takeover shows instead of the text time (css/widgets.css
// swaps them): the digits of a bedside alarm clock, drawn as SVG so no font
// has to be downloaded to a phone on a LAN. Unlit segments stay faintly
// visible, the way they do on the real thing. Module-authored markup only.
//
// One digit is 60x100 units; segments are hexagons 10 units thick.
function hSeg(y) {
  return `9,${y} 14,${y - 5} 46,${y - 5} 51,${y} 46,${y + 5} 14,${y + 5}`;
}
function vSeg(x, y1, y2) {
  return `${x},${y1} ${x + 5},${y1 + 5} ${x + 5},${y2 - 5} ${x},${y2} ${x - 5},${y2 - 5} ${x - 5},${y1 + 5}`;
}
const SEGMENTS = {
  a: hSeg(8),
  b: vSeg(52, 10, 49),
  c: vSeg(52, 51, 90),
  d: hSeg(92),
  e: vSeg(8, 51, 90),
  f: vSeg(8, 10, 49),
  g: hSeg(50),
};
const DIGIT_SEGMENTS = {
  0: "abcdef", 1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc",
  5: "afgcd", 6: "afgedc", 7: "abc", 8: "abcdefg", 9: "abcdfg",
};
const DIGIT_SVG = {};
for (const [digit, lit] of Object.entries(DIGIT_SEGMENTS)) {
  const polys = Object.entries(SEGMENTS)
    .map(([name, points]) => `<polygon class="${lit.includes(name) ? "on" : "off"}" points="${points}"/>`)
    .join("");
  DIGIT_SVG[digit] = `<svg class="seg-digit" viewBox="0 0 60 100" aria-hidden="true" focusable="false"><g transform="skewX(-6) translate(6 0)">${polys}</g></svg>`;
}
const COLON_SVG =
  '<svg class="seg-colon" viewBox="0 0 20 100" aria-hidden="true" focusable="false"><rect class="on" x="5" y="28" width="10" height="10" rx="2"/><rect class="on" x="3" y="62" width="10" height="10" rx="2"/></svg>';

// "23:03", "23:03:09", "11:03 PM": digits and colons become segments, and
// whatever else the locale adds (AM/PM) stays text beside them.
function segmentMarkup(text) {
  let html = "";
  let rest = "";
  for (const ch of text) {
    if (DIGIT_SVG[ch]) html += DIGIT_SVG[ch];
    else if (ch === ":") html += COLON_SVG;
    else rest += ch;
  }
  return { html, rest: rest.trim() };
}

// Literal, module-authored SVG only, the same rule as render.js's ICONS.
// Same 24x24 stroke geometry, so a weather glyph sits beside a tile icon
// without looking like it came from another set.
const SVG_OPEN =
  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">';
const WEATHER_ICONS = {
  sun: `${SVG_OPEN}<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>`,
  moon: `${SVG_OPEN}<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>`,
  partly: `${SVG_OPEN}<path d="M12 2v2M4.93 4.93l1.41 1.41M20 12h2M19.07 4.93l-1.41 1.41"/><path d="M15.95 12.4A4 4 0 0 0 8.1 11"/><path d="M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z"/></svg>`,
  cloud: `${SVG_OPEN}<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/></svg>`,
  fog: `${SVG_OPEN}<path d="M4 14.9A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 2.5 8.2"/><path d="M16 17H7M17 21H9"/></svg>`,
  rain: `${SVG_OPEN}<path d="M4 14.9A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 2.5 8.2"/><path d="M16 14v6M8 14v6M12 16v6"/></svg>`,
  snow: `${SVG_OPEN}<path d="M4 14.9A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 2.5 8.2"/><path d="M8 15h.01M8 19h.01M12 17h.01M12 21h.01M16 15h.01M16 19h.01"/></svg>`,
  storm: `${SVG_OPEN}<path d="M6 16.3A7 7 0 1 1 15.7 8h1.8a4.5 4.5 0 0 1 .5 9"/><path d="m13 12-3 5h4l-3 5"/></svg>`,
};

// WMO weather interpretation codes (Open-Meteo's `weather_code`) mapped to a
// glyph. Clear and mostly-clear pick sun or moon from is_day. Everything
// else reads the same by day and by night.
function iconFor(code, isDay) {
  if (code <= 1) return isDay ? "sun" : "moon";
  if (code === 2) return isDay ? "partly" : "cloud";
  if (code === 3) return "cloud";
  if (code === 45 || code === 48) return "fog";
  if ((code >= 71 && code <= 77) || code === 85 || code === 86) return "snow";
  if (code >= 95) return "storm";
  if (code >= 51) return "rain";
  return "cloud";
}

function formatTemp(value) {
  return typeof value === "number" ? `${Math.round(value)}°` : "—";
}

function el(tag, className, parent) {
  const node = document.createElement(tag);
  node.className = className;
  parent.appendChild(node);
  return node;
}

export function mountClockWeather(tile, item) {
  const params = item.params || {};
  const showSeconds = Boolean(params.show_seconds);
  const lat = Number(params.lat);
  const lon = Number(params.lon);
  const hasPlace =
    params.lat !== undefined && params.lon !== undefined && Number.isFinite(lat) && Number.isFinite(lon);

  tile.classList.add("tile-widget", "widget-clock-weather");
  // "23:03:09" is three characters wider than "23:03", and the time is
  // sized to fill the tile -- so a size that fits one clips the other.
  // The count is known only here, so it is published as a class rather
  // than guessed at in CSS.
  tile.classList.toggle("wc-seconds", showSeconds);
  // render.js appended an icon and a label for every tile. This widget draws
  // its own content, but the label stays as the accessible name. It is
  // visually hidden rather than removed, so a screen reader still hears
  // "Clock" before the time.
  for (const child of tile.querySelectorAll(":scope > .icon")) {
    child.remove();
  }
  const label = tile.querySelector(":scope > .label");
  if (label) {
    label.classList.add("widget-a11y-label");
  }

  const body = el("div", "widget-body", tile);

  const clock = el("div", "wc-clock", body);
  const time = el("div", "wc-time", clock);
  // Hidden except in the offline takeover. The text time above stays the
  // accessible one.
  const segs = el("div", "wc-seg", clock);
  segs.setAttribute("aria-hidden", "true");
  const segDigits = el("div", "wc-seg-digits", segs);
  const segSuffix = el("span", "wc-seg-suffix", segs);
  let segText = "";
  const date = el("div", "wc-date", clock);

  const weather = el("div", "wc-weather", body);
  weather.hidden = !hasPlace;
  const now = el("div", "wc-now", weather);
  const icon = el("div", "wc-icon", now);
  const temp = el("div", "wc-temp", now);
  const place = el("div", "wc-place", weather);
  const range = el("div", "wc-range", weather);

  place.textContent = params.city || "";
  temp.textContent = "—";

  // Undefined locale = the phone's own language and 12/24h preference.
  const timeFormat = new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    ...(showSeconds ? { second: "2-digit" } : {}),
  });
  const dateFormat = new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  });

  let tickTimer = null;
  let weatherTimer = null;
  let destroyed = false;
  let controller = null;

  function tick() {
    const current = new Date();
    time.textContent = timeFormat.format(current);
    date.textContent = dateFormat.format(current);
    if (time.textContent !== segText) {
      segText = time.textContent;
      const { html, rest } = segmentMarkup(segText);
      segDigits.innerHTML = html;
      segSuffix.textContent = rest;
    }
    // Aligned to the next boundary rather than a fixed interval. Otherwise a
    // widget mounted at :59 would show the old minute for a full minute, and
    // setInterval drift adds up over a day on a deck that never reloads.
    const period = showSeconds ? 1000 : 60000;
    const delay = period - (current.getTime() % period) + 20;
    tickTimer = setTimeout(tick, delay);
  }

  async function loadWeather() {
    if (!hasPlace || destroyed) {
      return;
    }
    if (controller) {
      controller.abort();
    }
    controller = new AbortController();
    try {
      const response = await fetch(
        `/api/widgets/weather?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`,
        { signal: controller.signal },
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      if (destroyed) {
        return;
      }
      const key = iconFor(Number(data.code), data.is_day !== false);
      icon.innerHTML = WEATHER_ICONS[key];
      temp.textContent = formatTemp(data.temp);
      range.textContent =
        typeof data.min === "number" && typeof data.max === "number"
          ? `${formatTemp(data.min)} / ${formatTemp(data.max)}`
          : "";
      weather.classList.toggle("wc-stale", Boolean(data.stale));
    } catch (err) {
      if (err.name === "AbortError" || destroyed) {
        return;
      }
      // No toast. The deck has one toast and it is for things the user just
      // did. An unreachable weather service is ambient, so the widget shows
      // a dash and keeps the last glyph, and the clock keeps running.
      temp.textContent = "—";
      weather.classList.add("wc-stale");
    }
  }

  function onVisibility() {
    // iOS suspends timers on a locked phone, so a deck that has been in a
    // pocket all afternoon wakes with morning weather. Coming back into view
    // is when a refresh is wanted. The backend cache keeps this cheap.
    if (document.visibilityState === "visible") {
      clearTimeout(tickTimer);
      tick();
      loadWeather();
    }
  }

  tick();
  loadWeather();
  if (hasPlace) {
    weatherTimer = setInterval(loadWeather, WEATHER_REFRESH_MS);
  }
  document.addEventListener("visibilitychange", onVisibility);

  return function destroy() {
    destroyed = true;
    clearTimeout(tickTimer);
    clearInterval(weatherTimer);
    document.removeEventListener("visibilitychange", onVisibility);
    if (controller) {
      controller.abort();
    }
  };
}
