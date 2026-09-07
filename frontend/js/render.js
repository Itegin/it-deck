import { attachLongPress } from "./longpress.js";

const SLIDER_THROTTLE_MS = 100;

// How long a tap may be outstanding before the pending dot appears. A local
// mute toggle resolves in tens of milliseconds; flashing an indicator for
// 30ms reads as a glitch rather than as feedback, so the dot is reserved for
// commands slow enough to actually leave someone waiting.
const PENDING_DELAY_MS = 150;

// A flash, not a state -- and only stateless tiles get one at all. See
// setTileCommandState.
const OK_LINGER_MS = 600;

// Outlives the toast slightly, so "which tile" is still on screen for a
// moment after "why" has gone. It has to be a timer: stateless tiles
// (Screenshot, launch_app) never receive a state update that could clear it,
// so a purely state-driven clear would strand them showing red forever.
const ERROR_LINGER_MS = 4000;

// item_id -> the one outstanding timeout handle for that tile. One slot per
// item because the three phases are mutually exclusive: entering any phase
// cancels whatever the previous one had scheduled.
const commandTimers = new Map();

// Literal, module-authored SVG markup only -- never build a key from
// item.icon and interpolate untrusted content into innerHTML. Missing keys
// render no icon rather than falling back to any kind of text/placeholder.
const ICONS = {
  lightbulb: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2.3h6c0-1.1.4-1.8 1-2.3A7 7 0 0 0 12 2Z"/></svg>`,
  music: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`,
  moon: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>`,
  terminal: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>`,
  mic: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`,
  speaker: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 5.5a9 9 0 0 1 0 13"/></svg>`,
  headphones: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 18v-6a9 9 0 0 1 18 0v6"/><path d="M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3Z"/><path d="M3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3Z"/></svg>`,
  "audio-switch": `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>`,
  camera: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2Z"/><circle cx="12" cy="13" r="4"/></svg>`,
};

export function renderWorkspace(workspace, onTileClick, onSliderChange, onTileLongPress) {
  const grid = document.getElementById("grid");

  grid.style.setProperty("--cols", workspace.grid_cols);
  grid.style.setProperty("--rows", workspace.grid_rows);

  grid.innerHTML = "";

  for (const item of workspace.items) {
    const isSlider = item.kind === "action" && item.width >= 2 && item.type === "audio_volume_set";
    // A real <button> for anything you can actually press, so focusability,
    // the button role and Enter/Space activation come from the browser rather
    // than being reimplemented with tabindex + role + keydown. Sliders stay
    // divs (a drag surface is not a button, and role=slider with arrow-key
    // stepping is a separate task), and so do widget tiles, which have no
    // listener at all -- making them focusable would put empty stops in the
    // tab order.
    const isButton = item.kind === "action" && !isSlider;
    const tile = document.createElement(isButton ? "button" : "div");
    if (isButton) {
      // Without this a <button> inside a <form> would default to submit.
      // There is no form here, but the default is never what a tile wants.
      tile.type = "button";
    }
    tile.className = "tile";
    tile.dataset.itemId = item.id;
    tile.dataset.kind = item.kind;
    // Which agent owns this tile -- read by setAgentOffline() so one agent
    // going offline only greys out its own tiles.
    tile.dataset.target = item.target;
    if (item.state_key) {
      // Namespaced with the owning agent's name to match the keys the
      // backend broadcasts (see backend/app/ws/agent.py) -- multiple agents
      // report the same generic state keys, so the bare key is ambiguous.
      tile.dataset.stateKey = `${item.target}:${item.state_key}`;
    }
    // Explicit per-item styling choice (teal "active" vs red "alert"),
    // read from the DB row instead of guessed from the state key's name.
    tile.dataset.activeStyle = (item.params && item.params.active_style) || "normal";
    // Optional "off" color for the false side of a boolean state -- left
    // unset (not assigned "null") when absent: dataset values are always
    // coerced to strings, so `tile.dataset.falseColor = null` would
    // actually store the truthy string "null", not nothing, and later
    // falsy-checks on it would misfire.
    if (item.params && item.params.false_color) {
      tile.dataset.falseColor = item.params.false_color;
    } else {
      delete tile.dataset.falseColor;
    }

    tile.style.gridColumn = `${item.col + 1} / span ${item.width}`;
    tile.style.gridRow = `${item.row + 1} / span ${item.height}`;
    tile.style.setProperty("--tile-color", item.color);
    // Per-item active/alert theming -- optional params keys, falling back
    // to the original hardcoded teal/red so untouched items render
    // identically to before this existed. Set on every tile (not just
    // ones that toggle state) so the Volume slider's fill-bar can read
    // --active-color too, per button.css.
    tile.style.setProperty("--active-color", (item.params && item.params.active_color) || "#0d9488");
    tile.style.setProperty("--alert-color", (item.params && item.params.alert_color) || "#dc2626");

    if (isSlider) {
      tile.classList.add("tile-slider");
      const fillBar = document.createElement("div");
      fillBar.className = "fill-bar";
      tile.appendChild(fillBar);
    }

    if (ICONS[item.icon]) {
      const icon = document.createElement("div");
      icon.className = "icon";
      icon.innerHTML = ICONS[item.icon];
      tile.appendChild(icon);
    }

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = item.label;
    tile.appendChild(label);

    if (isSlider) {
      // Sliders own their pointerdown/move/up stream via
      // attachSliderHandlers above; attaching long-press's independent
      // pointer listeners to the same element would double-handle every
      // event, so only non-slider action tiles get long-press.
      attachSliderHandlers(tile, item, onSliderChange);
    } else if (item.kind === "action") {
      attachLongPress(tile, () => onTileLongPress(item), () => onTileClick(item.id));
      // Keyboard activation, riding the button's own Enter/Space -> click
      // behaviour so there is no keydown handling here at all. The guard is
      // load-bearing: longpress already fires the tap from pointerup, and a
      // mouse click fires pointerup *and* click, so an unguarded listener
      // would execute the command twice per press. A click synthesised from
      // the keyboard carries detail === 0; a real mouse click carries 1.
      tile.addEventListener("click", (event) => {
        if (event.detail === 0) {
          onTileClick(item.id);
        }
      });
    }

    grid.appendChild(tile);
  }
}

// Pointer Events (not an overlaid <input type="range">) because the fill
// itself is a plain styled div driven by --fill-percent, not a native
// track/thumb -- an <input> would still need JS glue to sync its value
// into that custom property on every input event, plus fighting
// -webkit-appearance to make it invisible, for no less code than this.
function attachSliderHandlers(tile, item, onSliderChange) {
  let lastSent = 0;

  function valueFromEvent(event) {
    const rect = tile.getBoundingClientRect();
    const ratio = (event.clientX - rect.left) / rect.width;
    return Math.round(Math.min(1, Math.max(0, ratio)) * 100);
  }

  function applyValue(value, { force = false } = {}) {
    tile.style.setProperty("--fill-percent", `${value}%`);
    const now = Date.now();
    if (force || now - lastSent >= SLIDER_THROTTLE_MS) {
      lastSent = now;
      onSliderChange(item.id, value);
    }
  }

  tile.addEventListener("pointerdown", (event) => {
    tile.setPointerCapture(event.pointerId);
    applyValue(valueFromEvent(event));
  });

  tile.addEventListener("pointermove", (event) => {
    if (!tile.hasPointerCapture(event.pointerId)) return;
    applyValue(valueFromEvent(event));
  });

  function release(event) {
    if (!tile.hasPointerCapture(event.pointerId)) return;
    // Force: the drag's final value must always reach onSliderChange even
    // if it lands inside the throttle window of the last move.
    applyValue(valueFromEvent(event), { force: true });
    tile.releasePointerCapture(event.pointerId);
  }

  tile.addEventListener("pointerup", release);
  tile.addEventListener("pointercancel", release);
}

export function updateTileState(stateData) {
  for (const [key, value] of Object.entries(stateData)) {
    const tiles = document.querySelectorAll(`.tile[data-state-key="${CSS.escape(key)}"]`);

    for (const tile of tiles) {
      // A real reported value is more authoritative than anything the press
      // feedback is still showing: the device just told us where it actually
      // is, so drop any pending dot or error ring rather than leaving a stale
      // one sitting under the confirmed state.
      clearCommandState(tile);

      if (typeof value === "number") {
        tile.style.setProperty("--fill-percent", `${value}%`);
        continue;
      }
      if (typeof value === "string") {
        // e.g. speaker.device_name -- a live device/mode name, not a
        // boolean to toggle a class for or a number to fill a bar with.
        // Lazily created since not every tile with a string state_key
        // starts out with one in its initial markup.
        let subtitle = tile.querySelector(".subtitle");
        if (!subtitle) {
          subtitle = document.createElement("div");
          subtitle.className = "subtitle";
          tile.appendChild(subtitle);
        }
        subtitle.textContent = value;
        continue;
      }
      const activeClass = tile.dataset.activeStyle === "alert" ? "state-alert" : "state-active";
      const isTrue = Boolean(value);
      tile.classList.toggle(activeClass, isTrue);

      // false_color is an inline style, which beats any stylesheet rule
      // (including .state-active/.state-alert's class-based
      // --active-color/--alert-color) regardless of specificity -- so on
      // the true side it must be cleared back to "", or a stale false-state
      // color would permanently win over the class-based one from here on.
      if (isTrue) {
        tile.style.backgroundColor = "";
      } else if (tile.dataset.falseColor) {
        tile.style.backgroundColor = tile.dataset.falseColor;
      } else {
        tile.style.backgroundColor = ""; // falls back to item.color via --tile-color
      }
    }
  }
}

// Reuses .tile/.label styling (colors, radius, :active feedback) rather
// than inventing selector-specific markup -- one workspace per row, full
// width, stacked via --cols:1/--rows:<count> on the same #grid the
// dashboard uses.
export function renderWorkspaceSelector(workspaces, onSelect) {
  const grid = document.getElementById("grid");

  grid.style.setProperty("--cols", 1);
  grid.style.setProperty("--rows", workspaces.length);

  grid.innerHTML = "";

  workspaces.forEach((workspace, index) => {
    // Every selector tile is actionable, and this screen was already wired on
    // "click" rather than pointerup -- so a <button> gets Enter/Space for free
    // with no extra handler and no double-fire to guard against.
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "tile";
    tile.style.gridColumn = "1 / span 1";
    tile.style.gridRow = `${index + 1} / span 1`;

    const label = document.createElement("div");
    label.className = "label";
    label.textContent = workspace.name;
    tile.appendChild(label);

    tile.addEventListener("click", () => onSelect(workspace));

    grid.appendChild(tile);
  });
}

// Same lookup idiom as updateTileState's data-state-key and setAgentOffline's
// data-target queries. data-item-id is already set on every tile by
// renderWorkspace; nothing read it until now.
function tilesForItem(itemId) {
  return document.querySelectorAll(`.tile[data-item-id="${CSS.escape(String(itemId))}"]`);
}

function cancelCommandTimer(key) {
  const timer = commandTimers.get(key);
  if (timer !== undefined) {
    clearTimeout(timer);
    commandTimers.delete(key);
  }
}

// Removes every command-feedback class from one tile and cancels its timer.
// Used by the two "something more authoritative just happened" paths below.
function clearCommandState(tile) {
  cancelCommandTimer(tile.dataset.itemId);
  tile.classList.remove("tile-pending", "tile-ok", "tile-error");
}

export function setTileCommandState(itemId, phase) {
  const key = String(itemId);
  cancelCommandTimer(key);

  const tiles = tilesForItem(key);
  // A result routinely outlives its tile: onWorkspaceUpdate re-renders the
  // whole grid (grid.innerHTML = "" above), so the element a command started
  // on may simply be gone by the time it settles. Nothing to draw on is not
  // an error.
  if (!tiles.length) {
    return;
  }

  for (const tile of tiles) {
    tile.classList.remove("tile-pending", "tile-ok", "tile-error");
  }

  if (phase === "pending") {
    commandTimers.set(
      key,
      setTimeout(() => {
        commandTimers.delete(key);
        // Re-queried rather than reusing `tiles`: a re-render during the
        // delay would otherwise put the class on a detached element.
        for (const tile of tilesForItem(key)) {
          tile.classList.add("tile-pending");
        }
      }, PENDING_DELAY_MS)
    );
    return;
  }

  if (phase === "ok") {
    // A tile that reports real state confirms itself: poller.py's next tick
    // (1s) lands, updateTileState recolours it, and that colour change *is*
    // the success. Adding a second signal there would be the button colouring
    // itself from its own click. Only tiles that will never get such an
    // update -- Screenshot, launch_app -- need telling that the command
    // landed, and data-state-key is exactly that distinction.
    const stateless = [...tiles].filter((tile) => !tile.dataset.stateKey);
    if (!stateless.length) {
      return;
    }
    for (const tile of stateless) {
      tile.classList.add("tile-ok");
    }
    commandTimers.set(
      key,
      setTimeout(() => {
        commandTimers.delete(key);
        for (const tile of tilesForItem(key)) {
          tile.classList.remove("tile-ok");
        }
      }, OK_LINGER_MS)
    );
    return;
  }

  if (phase === "error") {
    for (const tile of tiles) {
      tile.classList.add("tile-error");
    }
    commandTimers.set(
      key,
      setTimeout(() => {
        commandTimers.delete(key);
        for (const tile of tilesForItem(key)) {
          tile.classList.remove("tile-error");
        }
      }, ERROR_LINGER_MS)
    );
  }
}

// Label and owning agent for one item, read back off the tile rather than
// held in a second copy of the workspace -- the DOM is already the live
// record, and it survives re-renders that a cached object wouldn't.
export function getTileMeta(itemId) {
  const tile = tilesForItem(String(itemId))[0];
  if (!tile) {
    return null;
  }
  const label = tile.querySelector(".label");
  return {
    label: label ? label.textContent : "",
    target: tile.dataset.target || "",
  };
}

export function setAgentOffline(agent, isOffline) {
  // Scoped to the agent named in the agent_status message: with more than
  // one agent connected, one disconnecting must not grey out the other's
  // tiles. data-target is set from item.target when the tile is rendered.
  const tiles = document.querySelectorAll(
    `.tile[data-kind="action"][data-target="${CSS.escape(agent)}"]`
  );
  for (const tile of tiles) {
    // Going offline answers every command still outstanding on this agent:
    // the tile is about to be greyed and made inert, and the server's own
    // timeout for those req_ids is still up to 5s away. Leaving a pending dot
    // pulsing underneath a greyed-out tile would claim work is in progress
    // that provably cannot be.
    if (isOffline) {
      clearCommandState(tile);
    }
    tile.classList.toggle("tile-offline", isOffline);
  }
}

export function renderError(message) {
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  const errorEl = document.createElement("div");
  errorEl.className = "error-message";
  errorEl.textContent = message;
  grid.appendChild(errorEl);
}
