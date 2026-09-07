// The Dashboard's visual theme. Server-stored (one choice shared by every
// panel pointed at this backend), cached in localStorage only so the first
// paint has something to go on -- see applyTheme.

import { fetchTheme, putTheme } from "./api.js";

// Must stay in step with backend/app/api/settings.py's THEMES, and with the
// [data-theme=...] blocks in css/themes.css. Order is the cycle order.
export const THEMES = ["flat", "pastel", "glossy"];

const DEFAULT_THEME = "flat";

// Deliberately a different key from app.js's workspace one: which deck this
// device shows is a per-device choice, the theme is not. This holds a *cache*
// of the server's value, never the source of truth.
const STORAGE_KEY = "itdeck:theme";

// Every value that reaches applyTheme has come from somewhere that can lie:
// the server (validated, but a different process), localStorage (writable by
// anything else served from this origin), or a settings_update frame off the
// WebSocket (which on a plain-http LAN is not an authenticated channel). The
// value is written into a DOM attribute, so it gets checked here too rather
// than trusted because it was checked once already on the way out.
function normalizeTheme(value) {
  return THEMES.includes(value) ? value : DEFAULT_THEME;
}

// Human-readable, and it names the theme the button will switch *to* -- the
// control cycles, so what a person needs from it is where the next press
// lands, not a restatement of what they can already see.
function describeNext(current) {
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
  return `Theme: ${current}. Switch to ${next}.`;
}

export function applyTheme(theme) {
  const safe = normalizeTheme(theme);

  document.documentElement.dataset.theme = safe;

  // Only so the *next* load can paint the right theme before the network
  // answers. A stale or absent entry costs nothing but a flash of Flat, which
  // is why the failure below is swallowed rather than surfaced: Safari throws
  // on localStorage access in some private-browsing configurations, and a
  // deck that cannot cache its theme should still run.
  try {
    localStorage.setItem(STORAGE_KEY, safe);
  } catch (err) {
    /* no cache available; the server value still applies on load */
  }

  const button = document.getElementById("theme-cycle");
  if (button) {
    const label = describeNext(safe);
    button.setAttribute("aria-label", label);
    button.title = label;
  }

  return safe;
}

// Read by index.html's inline boot script, which cannot import a module and so
// keeps its own copy of this logic -- the two are small and both commented to
// point at each other.
export function cachedTheme() {
  try {
    return normalizeTheme(localStorage.getItem(STORAGE_KEY));
  } catch (err) {
    return DEFAULT_THEME;
  }
}

// One press advances one step and immediately repaints, before the PUT is
// even sent. The alternative -- wait for the server, then apply -- makes a
// tap on a phone feel broken for the length of a round trip, and the deck
// already treats the server as the thing that *confirms* an action rather
// than the thing that performs it (see setTileCommandState). If the write
// fails, the catch below puts the old theme back, so the optimistic paint
// can never outlive the failure.
export async function cycleTheme() {
  const current = normalizeTheme(document.documentElement.dataset.theme);
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];

  applyTheme(next);

  try {
    await putTheme(next);
  } catch (err) {
    applyTheme(current);
    throw err;
  }
}

// Applies the cached theme, wires the button, then reconciles against the
// server. The fetch is not awaited before the button works: a backend that is
// slow or down should still leave the control usable against the cached
// value, and the reconcile simply corrects it when it lands.
export function initTheme(onError) {
  applyTheme(cachedTheme());

  const button = document.getElementById("theme-cycle");
  if (button) {
    button.addEventListener("click", () => {
      cycleTheme().catch((err) => {
        if (onError) {
          onError(err);
        }
      });
    });
  }

  fetchTheme()
    .then((theme) => applyTheme(theme))
    .catch(() => {
      // Nothing to say to the person here. The deck is already showing a
      // theme, the only cost is that it may be one press out of date, and
      // app.js has its own, louder error path for a backend that is actually
      // unreachable (the whole grid fails to load).
    });
}
