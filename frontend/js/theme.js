// The Dashboard's visual theme. Server-stored (one choice shared by every
// panel pointed at this backend), cached in localStorage only so the first
// paint has something to go on -- see applyTheme.

import { fetchTheme, putTheme } from "./api.js";

// Must stay in step with backend/app/api/settings.py's THEMES, with the
// inline boot allowlist in index.html's <head>, and with the [data-theme=...]
// blocks in css/themes.css. Order is the cycle order, so a new theme appended
// here lands at the end of the cycle rather than in the middle of it.
export const THEMES = ["flat", "pastel", "glossy", "liquid-glass"];

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

// Slug -> display name, derived rather than looked up in a table. "flat" ->
// "Flat", "liquid-glass" -> "Liquid Glass". Deliberately not a sixth list to
// keep in step with the five THEMES copies CLAUDE.md enumerates: every slug in
// this project is lowercase kebab-case, so the name falls straight out of the
// slug and a new theme gets a correct label for free.
function themeLabel(theme) {
  return theme
    .split("-")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

// The accessible name. It still says where the next press lands -- that is
// what a person actually needs from a cycling control -- but it now leads with
// the current theme, and both halves use the same display names the button
// shows. That last part is not cosmetic: WCAG 2.5.3 (Label in Name) wants the
// accessible name to contain the visible label, and the visible label is now
// themeLabel(current).
function describeTheme(current) {
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
  return `Theme: ${themeLabel(current)}. Switch to ${themeLabel(next)}.`;
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
    const description = describeTheme(safe);
    button.setAttribute("aria-label", description);
    button.title = description;

    // The visible half. Which theme is *on* was previously only in the title
    // -- which iOS Safari never shows, and the phone is the whole point of
    // this deck -- so the name is painted into the pill instead. Guarded
    // because index.html is not the only possible host for this button id.
    const name = button.querySelector(".theme-name");
    if (name) {
      name.textContent = themeLabel(safe);
    }
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
