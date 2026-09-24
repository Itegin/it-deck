// Widget tiles: item.kind === "widget". An action tile is a button that
// sends a command. A widget tile is a live surface that draws itself (a
// clock, the weather, later a recorder or a calendar) and sends nothing.
//
// The contract is one function per widget type:
//
//   mount(tile, item, ctx) -> destroy()
//
// `ctx.onState(callback)` subscribes to the agent's live state (the same
// namespaced keys tiles light up from, e.g. "windows:pc.cpu"). The callback
// runs once at once with everything known so far, then with each change.
// The subscription ends with the widget; destroy() needn't undo it. A widget
// that draws from the phone alone (the clock) simply ignores ctx.
//
// `tile` is the already-appended .tile element, so it has a real size and
// the widget's container queries resolve. `item` is the row with params
// already parsed. The returned destroy() must cancel every timer, listener
// and in-flight request the widget started.
//
// That last part is not optional. Every workspace_update re-renders the whole
// grid with innerHTML = "", which removes the elements but not their
// setInterval/setTimeout callbacks. A widget that doesn't clean up keeps
// fetching for a tile that no longer exists, once more per Studio edit.
// render.js calls destroyWidgets() before each of its three grid wipes.

import { mountClockWeather } from "./clock-weather.js";
import { mountPcStats } from "./pc-stats.js";

// Adding a widget: write its module, register it here, and add a matching
// entry to js/tile-catalog.js so Studio can create and configure it.
const WIDGETS = {
  clock_weather: mountClockWeather,
  pc_stats: mountPcStats,
};

// State callbacks of the widgets on screen. notifyWidgets() feeds them.
const stateListeners = new Set();

// Not a Set of elements: a destroy() is all we ever need back.
const active = [];

export function isKnownWidget(type) {
  return Object.prototype.hasOwnProperty.call(WIDGETS, type);
}

// Returns false when nothing was mounted: an unknown type, or a mount that
// threw. The caller then keeps its label-only fallback, so one broken widget
// can't take the rest of the deck down with it.
//
// `state` is the snapshot a new subscription starts from (render.js passes
// its knownState; Studio's preview passes nothing, so a widget there shows
// its empty look).
export function mountWidget(tile, item, { state = {} } = {}) {
  if (!isKnownWidget(item.type)) {
    return false;
  }
  const mine = [];
  const ctx = {
    onState(callback) {
      mine.push(callback);
      stateListeners.add(callback);
      callback({ ...state });
    },
  };
  const unsubscribe = () => {
    for (const callback of mine) stateListeners.delete(callback);
  };
  try {
    const destroy = WIDGETS[item.type](tile, item, ctx);
    active.push(() => {
      unsubscribe();
      if (typeof destroy === "function") destroy();
    });
    return true;
  } catch (err) {
    unsubscribe();
    console.error(`widget ${item.type} (item ${item.id}) failed to mount`, err);
    return false;
  }
}

// Changed state keys from the agent, to every subscribed widget. One broken
// widget must not stop the others from updating.
export function notifyWidgets(changed) {
  for (const callback of [...stateListeners]) {
    try {
      callback(changed);
    } catch (err) {
      console.error("widget failed to handle a state update", err);
    }
  }
}

export function destroyWidgets() {
  while (active.length) {
    const destroy = active.pop();
    try {
      destroy();
    } catch (err) {
      console.error("widget failed to clean up", err);
    }
  }
}

// For verification only (see the tech reference): lets a DevTools session
// confirm that re-renders don't leak mounted widgets.
export function activeWidgetCount() {
  return active.length;
}
