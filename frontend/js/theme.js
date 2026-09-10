// The Dashboard's visual look: two independent, server-stored settings (one
// choice shared by every panel pointed at this backend), each cached in
// localStorage only so the first paint has something to go on.
//
//   theme  the material -- flat, pastel, glossy, liquid-glass. Cycled from the
//          header pill.
//   mode   the ground -- light or dark, or "auto" for whichever one the
//          chosen theme shipped with. Set in Studio; there is no Dashboard
//          control, because it is a set-once preference rather than something
//          you flip mid-use, and the header has no room for a fourth pill.
//
// Both live here because they are one mechanism: the resolved mode depends on
// the theme (see NATIVE_MODE), so a theme change has to restamp the mode.

import { fetchSettings, putTheme, putMode } from "./api.js";

// Must stay in step with backend/app/api/settings.py's THEMES, with the
// inline boot allowlist in index.html's <head>, and with the [data-theme=...]
// blocks in css/themes.css. Order is the cycle order, so a new theme appended
// here lands at the end of the cycle rather than in the middle of it.
export const THEMES = ["flat", "pastel", "glossy", "liquid-glass"];

const DEFAULT_THEME = "flat";

// Must stay in step with backend/app/api/settings.py's MODES and with the
// inline boot allowlist in index.html's <head>. Not an ordered cycle -- there
// is no cycling control for this one -- but "auto" is deliberately first, so
// it reads as the default in Studio's picker.
export const MODES = ["auto", "light", "dark"];

const DEFAULT_MODE = "auto";

// What "auto" resolves to, per theme: the ground each theme shipped with,
// before the mode setting existed. This table is the whole reason "auto" is a
// value at all -- with only light/dark, no default could leave every existing
// deck looking the way it does today (dark would flip Pastel, light would flip
// the other three).
//
// It is duplicated twice more, both times unavoidably and both times pointed
// at this comment: in index.html's inline boot script (which cannot import a
// module) and, in selector form, in css/themes.css's
// :not([data-mode="light"]) / :not([data-mode="dark"]) guards (which are what
// the pre-JS paint uses). Adding a theme means answering "which ground does
// it ship with" in all three.
const NATIVE_MODE = {
  flat: "dark",
  pastel: "light",
  glossy: "dark",
  "liquid-glass": "dark",
};

// Deliberately a different key from app.js's workspace one: which deck this
// device shows is a per-device choice, the theme is not. This holds a *cache*
// of the server's value, never the source of truth.
const STORAGE_KEY = "itdeck:theme";
const MODE_STORAGE_KEY = "itdeck:mode";

// The mode *preference* ("auto" | "light" | "dark"), not the resolved ground.
// Module state because two things read it -- applyMode, and applyTheme, which
// has to re-resolve "auto" whenever the theme changes underneath it.
let modePreference = DEFAULT_MODE;

// Every value that reaches applyTheme has come from somewhere that can lie:
// the server (validated, but a different process), localStorage (writable by
// anything else served from this origin), or a settings_update frame off the
// WebSocket (which on a plain-http LAN is not an authenticated channel). The
// value is written into a DOM attribute, so it gets checked here too rather
// than trusted because it was checked once already on the way out.
function normalizeTheme(value) {
  return THEMES.includes(value) ? value : DEFAULT_THEME;
}

// Same reasoning as normalizeTheme, and the same three lying sources: this
// value also ends up in a DOM attribute.
function normalizeMode(value) {
  return MODES.includes(value) ? value : DEFAULT_MODE;
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

// Slug -> display name for the mode picker. "auto" gets a written-out label
// rather than the word itself: "Auto" would read as "follow the phone's dark
// mode", which is exactly what this value does not do.
export function modeLabel(mode) {
  return mode === "auto" ? "Theme default" : mode.charAt(0).toUpperCase() + mode.slice(1);
}

// Writes the *resolved* ground onto <html>, from the current preference and
// the current theme. Stamps the concrete value even for "auto" rather than
// removing the attribute: css/themes.css handles an absent attribute too (it
// has to -- that is the pre-JS paint), but a deck whose JS has run is easier
// to reason about when the attribute says what you are actually looking at.
function stampMode() {
  const theme = normalizeTheme(document.documentElement.dataset.theme);
  // The `|| "dark"` is for a theme added to THEMES but not to NATIVE_MODE:
  // three of the four are dark, so that is the better guess, and it fails as
  // "wrong ground until someone picks a mode" rather than as data-mode
  // ="undefined" (which would still read as dark to the CSS, just opaquely).
  const resolved =
    modePreference === "auto" ? NATIVE_MODE[theme] || "dark" : modePreference;
  document.documentElement.dataset.mode = resolved;
  return resolved;
}

export function applyTheme(theme) {
  const safe = normalizeTheme(theme);

  document.documentElement.dataset.theme = safe;
  // After the theme, because "auto" means "this theme's own ground" -- so
  // cycling Pastel -> Glossy on an auto deck has to move the ground with it.
  stampMode();

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

export function cachedMode() {
  try {
    return normalizeMode(localStorage.getItem(MODE_STORAGE_KEY));
  } catch (err) {
    return DEFAULT_MODE;
  }
}

// Applies a mode *preference* and returns it. The same shape as applyTheme:
// validate, write the DOM, cache for the next first paint, swallow a
// localStorage that throws (private-browsing Safari) because a deck that
// cannot cache its mode should still run.
export function applyMode(mode) {
  const safe = normalizeMode(mode);
  modePreference = safe;
  stampMode();

  try {
    localStorage.setItem(MODE_STORAGE_KEY, safe);
  } catch (err) {
    /* no cache available; the server value still applies on load */
  }

  return safe;
}

// The Studio-side write. Optimistic in the same way cycleTheme is -- paint
// first, then persist, and put the old value back if the write fails -- so the
// control never sits waiting on a round trip. Studio has no deck of its own to
// repaint (css/themes.css is Dashboard-only), but the optimistic apply still
// matters there: it is what makes the <select> and the cached value agree
// before the network answers, and the connected phones repaint off the
// settings_update broadcast the PUT triggers.
export async function setMode(mode) {
  const previous = modePreference;
  const safe = applyMode(mode);

  try {
    await putMode(safe);
  } catch (err) {
    applyMode(previous);
    throw err;
  }

  return safe;
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
  // Mode first, so applyTheme's own stampMode() call resolves against the
  // cached preference rather than against DEFAULT_MODE for one frame.
  applyMode(cachedMode());
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

  fetchSettings()
    .then(({ theme, mode }) => {
      // Mode before theme again, and both unconditionally: this is the whole
      // settings object from the server, so an absent key here really does
      // mean "the server has no opinion" and the normalizers' defaults are
      // the right answer. (A settings_update *frame* is the opposite case --
      // it carries only what changed. See app.js.)
      applyMode(mode);
      applyTheme(theme);
    })
    .catch(() => {
      // Nothing to say to the person here. The deck is already showing a
      // theme, the only cost is that it may be one press out of date, and
      // app.js has its own, louder error path for a backend that is actually
      // unreachable (the whole grid fails to load).
    });
}
