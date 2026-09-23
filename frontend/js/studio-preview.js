// Studio's deck preview: the workspace drawn with the deck's own .tile markup
// and styles (and live widgets), plus a "+" in every free cell. Clicking picks
// a tile to edit or a cell to fill, and dragging a tile onto a free cell moves
// it.
//
// It is a picture of the deck, not a second implementation of render.js: no
// command wiring, no state polling, no slider handlers. Tiles here are
// buttons that select, nothing more.

import { tileIconFor } from "./render.js";
import { mountWidget, destroyWidgets } from "./widgets/index.js";
import { vpnNeedsPath } from "./tile-catalog.js";
import { t } from "./studio-i18n.js";

const DRAG_TYPE = "application/x-itdeck-item";

// Same as the backend's DOCK_MAX (backend/app/api/items.py).
export const DOCK_MAX = 7;

// A drop target that accepts a dragged tile. Shared by the grid's empty
// cells and the bar's "+" slot.
function acceptDrops(node, workspace, onDrop) {
  node.addEventListener("dragover", (event) => {
    if (!event.dataTransfer.types.includes(DRAG_TYPE)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    node.classList.add("is-drop-target");
  });
  node.addEventListener("dragleave", () => node.classList.remove("is-drop-target"));
  node.addEventListener("drop", (event) => {
    event.preventDefault();
    node.classList.remove("is-drop-target");
    const id = Number(event.dataTransfer.getData(DRAG_TYPE));
    const item = workspace.items.find((candidate) => candidate.id === id);
    if (item) onDrop(item);
  });
}

export function renderPreview(grid, workspace, handlers, dock) {
  // Same rule as the deck: a widget's timers outlive innerHTML = "".
  destroyWidgets();
  grid.replaceChildren();
  if (dock) dock.replaceChildren();
  if (!workspace) {
    return;
  }
  const gridItems = workspace.items.filter((item) => !item.dock);
  const dockItems = workspace.items.filter((item) => item.dock).sort((a, b) => a.col - b.col);

  grid.style.setProperty("--cols", workspace.grid_cols);

  // Out-of-bounds rows can exist (startup fixups write to SQLite directly and
  // bypass placement validation), so draw whatever is actually there.
  const rows = gridItems.reduce(
    (max, item) => Math.max(max, item.row + (item.height || 1)),
    workspace.grid_rows,
  );

  const covered = new Set();
  const widgets = [];

  for (const item of [...gridItems, ...dockItems]) {
    for (let r = item.dock ? Infinity : item.row; r < item.row + (item.height || 1); r++) {
      for (let c = item.col; c < item.col + (item.width || 1); c++) {
        covered.add(`${r},${c}`);
      }
    }

    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "tile";
    tile.dataset.itemId = item.id;
    tile.style.gridColumn = `${item.col + 1} / span ${item.width || 1}`;
    tile.style.gridRow = `${item.row + 1} / span ${item.height || 1}`;
    tile.style.setProperty("--tile-color", item.color);
    if (item.params.active_color) {
      tile.style.setProperty("--active-color", item.params.active_color);
    }
    tile.setAttribute("aria-label", t("preview.editTile", { label: item.label }));
    tile.draggable = true;

    const icon = item.kind !== "widget" ? tileIconFor(item) : null;
    if (icon) {
      tile.appendChild(icon);
    }
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = item.label;
    tile.appendChild(label);

    if (vpnNeedsPath(item) || item.paramsInvalid) {
      const badge = document.createElement("span");
      badge.className = "preview-badge";
      badge.textContent = "!";
      badge.title = item.paramsInvalid ? t("preview.badParams") : t("preview.needsPath");
      tile.appendChild(badge);
      tile.setAttribute(
        "aria-label",
        `${t("preview.editTile", { label: item.label })}. ${badge.title}`,
      );
    }

    tile.addEventListener("click", () => handlers.onSelectItem(item));
    tile.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData(DRAG_TYPE, String(item.id));
      event.dataTransfer.effectAllowed = "move";
      tile.classList.add("is-dragging");
    });
    tile.addEventListener("dragend", () => tile.classList.remove("is-dragging"));

    if (item.dock && dock) {
      tile.classList.add("dock-tile");
      tile.title = item.label;
      dock.appendChild(tile);
      continue;
    }
    grid.appendChild(tile);
    if (item.kind === "widget") {
      widgets.push([tile, item]);
    }
  }

  for (let row = 0; row < rows; row++) {
    for (let col = 0; col < workspace.grid_cols; col++) {
      if (covered.has(`${row},${col}`)) continue;
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "preview-empty";
      cell.dataset.row = row;
      cell.dataset.col = col;
      cell.style.gridColumn = `${col + 1}`;
      cell.style.gridRow = `${row + 1}`;
      cell.textContent = "+";
      cell.setAttribute("aria-label", t("preview.addAt", { row: row + 1, col: col + 1 }));
      cell.addEventListener("click", () => handlers.onSelectEmpty(row, col));
      acceptDrops(cell, workspace, (item) => handlers.onMove(item, { row, col, dock: false }));
      grid.appendChild(cell);
    }
  }

  // The quick-launch bar, under the grid as the phone shows it upright: its
  // buttons, then one "+" for the next free place while there is one.
  if (dock) {
    const used = new Set(dockItems.map((item) => item.col));
    let free = -1;
    for (let pos = 0; pos < DOCK_MAX; pos++) {
      if (!used.has(pos)) {
        free = pos;
        break;
      }
    }
    if (free >= 0) {
      const slot = document.createElement("button");
      slot.type = "button";
      slot.className = "preview-empty dock-slot";
      slot.dataset.dockSlot = String(free);
      slot.textContent = "+";
      slot.title = t("dock.add");
      slot.setAttribute("aria-label", t("dock.add"));
      slot.addEventListener("click", () => handlers.onSelectDockEmpty(free));
      acceptDrops(slot, workspace, (item) => handlers.onMove(item, { row: 0, col: free, dock: true }));
      dock.appendChild(slot);
    }
  }

  for (const [tile, item] of widgets) {
    mountWidget(tile, item);
  }
}

// Moves the selection ring without rebuilding the grid, so selecting a tile
// doesn't restart every widget on the preview.
export function markSelection(grid, selection) {
  for (const node of grid.querySelectorAll(".is-selected")) {
    node.classList.remove("is-selected");
    node.removeAttribute("aria-pressed");
  }
  let target = null;
  if (selection && selection.itemId !== undefined) {
    target = grid.querySelector(`[data-item-id="${CSS.escape(String(selection.itemId))}"]`);
  } else if (selection && selection.dockSlot !== undefined) {
    target = grid.querySelector(`[data-dock-slot="${selection.dockSlot}"]`);
  } else if (selection && selection.cell) {
    target = grid.querySelector(`[data-row="${selection.cell.row}"][data-col="${selection.cell.col}"]`);
  }
  if (target) {
    target.classList.add("is-selected");
    target.setAttribute("aria-pressed", "true");
  }
}
