// Studio: the desktop editor for the deck. This module owns loading, the
// agent token, the top bar and the VPN setup card. The deck preview is in
// studio-preview.js and the editor panel in studio-inspector.js, and both
// are built from js/tile-catalog.js.
//
// Studio is always Liquid Glass (studio.html pins data-theme) and follows
// the shared light/dark "deck background" setting, which it also edits.

import { MODES, applyMode, setMode } from "./theme.js";
import { fetchSettings } from "./api.js";
import { showToast } from "./toast.js";
import { applyStaticStrings, t } from "./studio-i18n.js";
import { renderPreview, markSelection, DOCK_MAX } from "./studio-preview.js";
import { createInspector } from "./studio-inspector.js";
import { cleanPath, detectEntry, vpnNeedsPath } from "./tile-catalog.js";
import { ICONS } from "./render.js";
import { initGuide } from "./studio-guide.js";

const grid = document.getElementById("preview-grid");
const previewDock = document.getElementById("preview-dock");
// markSelection looks in here, so a selected bar button is found as well.
const phone = grid.closest(".phone");
const workspaceSelect = document.getElementById("workspace-select");
const modeSelect = document.getElementById("mode-select");
const modeMessage = document.getElementById("mode-message");
const setupCard = document.getElementById("setup-card");
const tokenDialog = document.getElementById("token-dialog");
const tokenInput = document.getElementById("token-input");

document.getElementById("token-cancel").addEventListener("click", () => tokenDialog.close("cancel"));
// Explicit, rather than trusting implicit form submission inside a modal
// <dialog>, which didn't reliably fire in testing.
tokenInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    if (tokenInput.value.trim()) tokenDialog.close("ok");
  }
});

// ── Agent token ──────────────────────────────────────────────────────────
// Persisted in localStorage, so it is entered once and then forgotten about.
// A token re-asked on every visit is one people stop reading and start
// dismissing. A different key from the Dashboard's client token: two secrets.
// Dropped on any 401, so a wrong value saved once isn't re-sent forever.
const AGENT_TOKEN_STORAGE_KEY = "itdeck.agent_token";

let agentToken = null;
let tokenPrompt = null;

function readStoredToken() {
  try {
    return localStorage.getItem(AGENT_TOKEN_STORAGE_KEY);
  } catch (e) {
    // Private mode or blocked site data. The dialog still works for this
    // page load, it just can't be remembered.
    return null;
  }
}

function askForToken() {
  // One dialog even if several requests need a token at once.
  if (tokenPrompt) return tokenPrompt;
  tokenPrompt = new Promise((resolve) => {
    tokenInput.value = "";
    // Escape closes without touching returnValue, so a stale "ok" from the
    // last time would accept whatever is typed now.
    tokenDialog.returnValue = "";
    tokenDialog.addEventListener(
      "close",
      () => {
        tokenPrompt = null;
        const value = tokenInput.value.trim();
        resolve(tokenDialog.returnValue === "ok" && value ? value : null);
      },
      { once: true },
    );
    tokenDialog.showModal();
    tokenInput.focus();
  });
  return tokenPrompt;
}

async function getAgentToken() {
  if (agentToken === null) {
    agentToken = readStoredToken();
  }
  if (agentToken === null) {
    const entered = await askForToken();
    if (entered === null) return null;
    agentToken = entered;
    try {
      localStorage.setItem(AGENT_TOKEN_STORAGE_KEY, entered);
    } catch (e) {
      // Not fatal. The in-memory value still serves this page load.
    }
  }
  return agentToken;
}

function forgetAgentToken() {
  agentToken = null;
  try {
    localStorage.removeItem(AGENT_TOKEN_STORAGE_KEY);
  } catch (e) {
    // The in-memory reset above is already enough to ask again.
  }
}

// Every token-bearing request goes through here, which is how every one of
// them drops a rejected token on 401. The old studio.js had to remember that
// at four separate call sites.
async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (auth) {
    const token = await getAgentToken();
    if (token === null) {
      return { ok: false, status: 0, detail: t("error.noToken") };
    }
    headers["X-Agent-Token"] = token;
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let response;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (err) {
    return { ok: false, status: 0, detail: t("error.network", { detail: err.message }) };
  }

  if (response.status === 401 && auth) {
    forgetAgentToken();
  }
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    if (response.status === 401) {
      detail = t("error.badToken");
    } else if (data && typeof data.detail === "string") {
      detail = data.detail;
    } else if (data && Array.isArray(data.detail) && data.detail[0] && data.detail[0].msg) {
      detail = data.detail[0].msg;
    }
    return { ok: false, status: response.status, detail, data };
  }
  return { ok: true, status: response.status, data };
}

// ── Workspaces ───────────────────────────────────────────────────────────

let workspaces = [];
let selection = null; // { itemId } | { cell: { row, col } } | null

// Params arrive as a JSON string. One row that doesn't parse must not take
// Studio down: it is kept raw and flagged, and the editor opens it with the
// raw text so it can be fixed.
function normalizeItem(item) {
  try {
    const parsed = JSON.parse(item.params);
    return { ...item, params: parsed && typeof parsed === "object" ? parsed : {} };
  } catch {
    return { ...item, params: {}, paramsRaw: item.params, paramsInvalid: true };
  }
}

function currentWorkspace() {
  return workspaces.find((w) => String(w.id) === workspaceSelect.value) || workspaces[0] || null;
}

function allItems() {
  return workspaces.flatMap((w) => w.items);
}

// Keeps the current pick across a reload. Every save ends in loadItems(), and
// without this each one would bounce the picker back to the first deck.
function populateWorkspaceSelect() {
  const previous = workspaceSelect.value;
  workspaceSelect.replaceChildren(
    ...workspaces.map((w) => {
      const option = document.createElement("option");
      option.value = w.id;
      option.textContent = w.name;
      return option;
    }),
  );
  if (previous && workspaces.some((w) => String(w.id) === previous)) {
    workspaceSelect.value = previous;
  }
}

// Only the newest loadItems() may draw: a save followed quickly by a move or a
// compact starts overlapping fetches that can answer out of order. A call
// that has been overtaken resolves with the newest load instead of on its
// own, so a caller that awaits it (to focus the tile it just saved, say)
// still continues against the deck as finally drawn.
let loadSeq = 0;
let latestLoad = Promise.resolve();

function loadItems() {
  const seq = ++loadSeq;
  latestLoad = fetchAndDraw(seq);
  return latestLoad;
}

async function fetchAndDraw(seq) {
  let raw;
  try {
    const response = await fetch("/api/workspaces");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    raw = await response.json();
  } catch (err) {
    if (seq !== loadSeq) return latestLoad;
    showToast(t("error.load", { detail: err.message }));
    return;
  }
  if (seq !== loadSeq) return latestLoad;
  workspaces = raw.map((w) => ({ ...w, items: w.items.map(normalizeItem) }));
  populateWorkspaceSelect();
  renderSetupCard();
  renderDeck();
}

function renderDeck() {
  renderPreview(grid, currentWorkspace(), {
    onSelectItem: (item) => {
      selection = { itemId: item.id };
      markSelection(phone, selection);
      inspector.open({ item });
    },
    onSelectEmpty: (row, col) => {
      selection = { cell: { row, col } };
      markSelection(phone, selection);
      inspector.open({ item: null, cell: { row, col }, origin: "cell" });
    },
    onSelectDockEmpty: (position) => {
      selection = { dockSlot: position };
      markSelection(phone, selection);
      inspector.open({ item: null, cell: firstFreeCell(currentWorkspace()) || { row: 0, col: 0 }, origin: "dock" });
    },
    onMove: moveItem,
  }, previewDock);
  markSelection(phone, selection);
}

// Same rectangle test as the backend's _validate_placement, in reading order.
function firstFreeCell(workspace) {
  const taken = new Set();
  for (const item of workspace.items.filter((candidate) => !candidate.dock)) {
    for (let r = item.row; r < item.row + (item.height || 1); r++) {
      for (let c = item.col; c < item.col + (item.width || 1); c++) {
        taken.add(`${r},${c}`);
      }
    }
  }
  for (let row = 0; row < workspace.grid_rows; row++) {
    for (let col = 0; col < workspace.grid_cols; col++) {
      if (!taken.has(`${row},${col}`)) return { row, col };
    }
  }
  return null;
}

// The next free place in the quick-launch bar, or -1 when it is full.
function firstFreeDockSlot(workspace) {
  const used = new Set(workspace.items.filter((item) => item.dock).map((item) => item.col));
  for (let pos = 0; pos < DOCK_MAX; pos++) {
    if (!used.has(pos)) return pos;
  }
  return -1;
}

// `to` is { row, col, dock }: a grid cell, or a place in the bar.
async function moveItem(item, to) {
  // The bar only takes 1x1 actions; a 2-wide slider or a widget dropped on
  // its "+" is refused here with a reason, not by the API with a code.
  if (to.dock && (item.kind !== "action" || (item.width || 1) > 1 || (item.height || 1) > 1)) {
    showToast(t("dock.notAllowed", { label: item.label }));
    return;
  }
  const body = { row: to.row, col: to.col, dock: to.dock };
  if (to.dock) {
    body.width = 1;
    body.height = 1;
  }
  const result = await api(`/api/items/${item.id}`, { method: "PUT", body });
  if (!result.ok) {
    // Usually an overlap: a 2-wide tile dropped where its second cell is taken.
    showToast(t("error.move", { label: item.label, detail: result.detail }));
    return;
  }
  // The open editor holds the old row/col. Saving it afterwards would move the
  // tile straight back, so it is closed if it's this tile.
  if (inspector.currentItemId() === item.id) {
    inspector.close({ restoreFocus: false });
  }
  selection = { itemId: item.id };
  await loadItems();
}

const inspector = createInspector(document.getElementById("inspector"), {
  api,
  getWorkspace: currentWorkspace,
  firstFreeCell: () => firstFreeCell(currentWorkspace()),
  firstFreeDockSlot: () => firstFreeDockSlot(currentWorkspace()),
  targets: () => ["windows", "backend", ...allItems().map((item) => item.target)],
  onSaved: async (saved) => {
    selection = { itemId: saved.id };
    await loadItems();
    showToast(t("toast.saved", { label: saved.label }));
    const tile = phone.querySelector(`[data-item-id="${saved.id}"]`);
    if (tile) tile.focus();
  },
  onDeleted: async (item) => {
    selection = null;
    await loadItems();
    showToast(t("toast.deleted", { label: item.label }));
    document.getElementById("new-item-btn").focus();
  },
  onClose: () => {
    selection = null;
    markSelection(phone, null);
  },
});

document.getElementById("new-item-btn").addEventListener("click", () => {
  const workspace = currentWorkspace();
  if (!workspace) return;
  const cell = firstFreeCell(workspace);
  if (!cell) {
    showToast(t("error.gridFull"));
    return;
  }
  selection = { cell };
  markSelection(phone, selection);
  inspector.open({ item: null, cell, origin: "button" });
});

workspaceSelect.addEventListener("change", () => {
  // An editor open on another deck's tile doesn't belong to what's on screen.
  inspector.close({ restoreFocus: false });
  renderDeck();
});

document.getElementById("compact-btn").addEventListener("click", async () => {
  const workspace = currentWorkspace();
  if (!workspace) return;
  if (!confirm(t("confirm.compact", { name: workspace.name }))) return;
  const result = await api(`/api/workspaces/${workspace.id}/compact`, { method: "POST" });
  if (!result.ok) {
    showToast(t("error.compact", { detail: result.detail }));
    return;
  }
  // Close BEFORE reloading, and that order matters. An open editor still
  // holds the pre-compact row/col, so a Save afterwards would push the tile
  // back out of the packed layout.
  inspector.close({ restoreFocus: false });
  await loadItems();
});

// ── VPN setup card ───────────────────────────────────────────────────────
// The one thing a fresh install has to configure, so it sits above
// everything. Shown only if a VPN tile exists.

let setupEditing = false;
let setupJustSaved = false;

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") node.value = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

function renderSetupCard() {
  const vpn = allItems().find((item) => detectEntry(item).id === "vpn");
  if (!vpn) {
    setupCard.hidden = true;
    return;
  }
  setupCard.hidden = false;

  const icon = el("div", { class: "setup-icon", html: ICONS.shield });
  icon.setAttribute("aria-hidden", "true");
  const needsPath = vpnNeedsPath(vpn);

  if (!needsPath && !setupEditing) {
    setupCard.classList.add("is-done");
    setupCard.replaceChildren(
      icon,
      el("div", { class: "setup-done-text" }, [
        el("strong", { text: t("setup.doneTitle") }),
        el("code", { text: vpn.params.path }),
        setupJustSaved ? el("span", { class: "field-hint", text: t("setup.restartNote") }) : "",
        el("button", {
          type: "button",
          class: "btn",
          text: t("setup.change"),
          onClick: () => {
            setupEditing = true;
            renderSetupCard();
            setupCard.querySelector("input").focus();
          },
        }),
      ]),
    );
    return;
  }

  setupCard.classList.remove("is-done");
  const inputId = "setup-vpn-path";
  const input = el("input", {
    id: inputId,
    class: "input",
    value: vpn.params.path || "",
    placeholder: t("placeholder.vpn.path"),
    spellcheck: "false",
    autocomplete: "off",
    "aria-describedby": "setup-vpn-hint",
  });
  const message = el("p", { class: "message", role: "alert" });

  async function savePath(event) {
    event.preventDefault();
    const path = cleanPath(input.value);
    if (!path) {
      message.textContent = t("error.path", { field: t("field.path") });
      message.classList.add("error");
      input.focus();
      return;
    }
    if (vpn.paramsInvalid) {
      message.textContent = t("preview.badParams");
      message.classList.add("error");
      return;
    }
    message.textContent = t("common.saving");
    message.classList.remove("error");
    const params = { ...vpn.params, path };
    const result = await api(`/api/items/${vpn.id}`, { method: "PUT", body: { params: JSON.stringify(params) } });
    if (!result.ok) {
      message.textContent = t("error.save", { detail: result.detail });
      message.classList.add("error");
      return;
    }
    setupEditing = false;
    setupJustSaved = true;
    if (inspector.currentItemId() === vpn.id) {
      inspector.close({ restoreFocus: false });
    }
    await loadItems();
    showToast(t("toast.vpnSaved"));
  }

  const actions = [el("button", { type: "submit", class: "btn btn-primary", text: t("common.save") })];
  if (setupEditing) {
    actions.push(
      el("button", {
        type: "button",
        class: "btn",
        text: t("common.cancel"),
        onClick: () => {
          setupEditing = false;
          renderSetupCard();
        },
      }),
    );
  }

  setupCard.replaceChildren(
    icon,
    el("h2", { id: "setup-title", text: t("setup.title") }),
    el("p", { id: "setup-vpn-hint", text: t("setup.body") }),
    el("form", { class: "setup-form", onSubmit: savePath }, [
      el("label", { class: "visually-hidden", for: inputId, text: t("field.path") }),
      el("div", { class: "setup-row" }, [input, ...actions]),
      el("p", { class: "field-hint", text: t("hint.copyPath") }),
      el("p", { class: "field-hint", text: t("hint.vpn.admin") }),
      message,
    ]),
  );
}

// ── Deck background (light/dark) ─────────────────────────────────────────
// One shared, server-stored setting. Every connected deck repaints off the
// settings_update broadcast, and so does this page, since Studio follows
// the same ground.

function setModeMessage(text, isError) {
  modeMessage.textContent = text;
  modeMessage.classList.toggle("error", Boolean(isError));
}

function populateModeSelect(selected) {
  modeSelect.replaceChildren(
    ...MODES.map((mode) => {
      const option = document.createElement("option");
      option.value = mode;
      option.textContent = t(`mode.${mode}`);
      return option;
    }),
  );
  modeSelect.value = selected;
}

async function loadMode() {
  try {
    const { mode } = await fetchSettings();
    const safe = MODES.includes(mode) ? mode : MODES[0];
    populateModeSelect(safe);
    applyMode(safe);
  } catch (err) {
    // Setting it blind would show a choice nobody made, so the picker is
    // disabled rather than left looking authoritative.
    populateModeSelect(MODES[0]);
    modeSelect.disabled = true;
    setModeMessage(t("mode.readFailed", { detail: err.message }), true);
  }
}

modeSelect.addEventListener("change", async () => {
  const chosen = modeSelect.value;
  setModeMessage(t("common.saving"), false);
  try {
    await setMode(chosen);
    setModeMessage(t("mode.saved", { mode: t(`mode.${chosen}`) }), false);
  } catch (err) {
    setModeMessage(t("error.save", { detail: err.message }), true);
    loadMode();
  }
});

applyStaticStrings();
initGuide(document.getElementById("guide-dialog"), document.getElementById("guide-btn"));
loadItems();
// Independent of loadItems: a settings read must not take the deck down with
// it, or the other way round.
loadMode();
