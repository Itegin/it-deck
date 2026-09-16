// Studio's deck preview: the workspace drawn with the deck's own .tile markup
// and styles (and live widgets), plus a "+" in every free cell. Clicking picks
// a tile to edit or a cell to fill, and dragging a tile onto a free cell moves
// it.
//
// It is a picture of the deck, not a second implementation of render.js: no
// command wiring, no state polling, no slider handlers. Tiles here are
// buttons that select, nothing more.

import { ICONS } from "./render.js";
import { mountWidget, destroyWidgets } from "./widgets/index.js";
import { vpnNeedsPath } from "./tile-catalog.js";
import { t } from "./studio-i18n.js";

const DRAG_TYPE = "application/x-itdeck-item";

export function renderPreview(grid, workspace, handlers) {
  // Same rule as the deck: a widget's timers outlive innerHTML = "".
  destroyWidgets();
  grid.replaceChildren();
  if (!workspace) {
    return;
  }

  grid.style.setProperty("--cols", workspace.grid_cols);

  // Out-of-bounds rows can exist (startup fixups write to SQLite directly and
  // bypass placement validation), so draw whatever is actually there.
  const rows = workspace.items.reduce(
    (max, item) => Math.max(max, item.row + (item.height || 1)),
    workspace.grid_rows,
  );

  const covered = new Set();
  const widgets = [];

  for (const item of workspace.items) {
    for (let r = item.row; r < item.row + (item.height || 1); r++) {
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

    if (ICONS[item.icon] && item.kind !== "widget") {
      const icon = document.createElement("div");
      icon.className = "icon";
      icon.innerHTML = ICONS[item.icon];
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

      cell.addEventListener("dragover", (event) => {
        if (!event.dataTransfer.types.includes(DRAG_TYPE)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        cell.classList.add("is-drop-target");
      });
      cell.addEventListener("dragleave", () => cell.classList.remove("is-drop-target"));
      cell.addEventListener("drop", (event) => {
        event.preventDefault();
        cell.classList.remove("is-drop-target");
        const id = Number(event.dataTransfer.getData(DRAG_TYPE));
        const item = workspace.items.find((candidate) => candidate.id === id);
        if (item) handlers.onMove(item, row, col);
      });
      grid.appendChild(cell);
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
  } else if (selection && selection.cell) {
    target = grid.querySelector(`[data-row="${selection.cell.row}"][data-col="${selection.cell.col}"]`);
  }
  if (target) {
    target.classList.add("is-selected");
    target.setAttribute("aria-pressed", "true");
  }
}
