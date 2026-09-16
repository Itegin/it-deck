// Widget tiles: item.kind === "widget". An action tile is a button that
// sends a command. A widget tile is a live surface that draws itself (a
// clock, the weather, later a recorder or a calendar) and sends nothing.
//
// The contract is one function per widget type:
//
//   mount(tile, item) -> destroy()
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

// Adding a widget: write its module, register it here, and add a matching
// entry to js/tile-catalog.js so Studio can create and configure it.
const WIDGETS = {
  clock_weather: mountClockWeather,
};

// Not a Set of elements: a destroy() is all we ever need back.
const active = [];

export function isKnownWidget(type) {
  return Object.prototype.hasOwnProperty.call(WIDGETS, type);
}

// Returns false when nothing was mounted: an unknown type, or a mount that
// threw. The caller then keeps its label-only fallback, so one broken widget
// can't take the rest of the deck down with it.
export function mountWidget(tile, item) {
  if (!isKnownWidget(item.type)) {
    return false;
  }
  try {
    const destroy = WIDGETS[item.type](tile, item);
    if (typeof destroy === "function") {
      active.push(destroy);
    }
    return true;
  } catch (err) {
    console.error(`widget ${item.type} (item ${item.id}) failed to mount`, err);
    return false;
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
