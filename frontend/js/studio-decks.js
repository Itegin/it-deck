// Studio's Decks dialog, where decks are managed: the list (reorder, rename,
// download, delete), adding one (empty, from a file, from a template) and the
// phone's "switch decks by swiping" setting.
//
// Every change goes through the backend and ends in reload(), which refetches
// Studio's deck list; the dialog then redraws its list from that. Phones hear
// the same workspace_update and follow on their own. A phone remembers its
// deck by id, so nothing here ever re-creates a deck under a new id: a rename
// or a move leaves every phone where it was, and one showing a deleted deck
// moves to the deck that took its place (app.js).
//
// Deleting is two steps in the row itself, naming the deck and its tile count,
// with Download beside it: there is no undo. The last deck can't be deleted.

import { el } from "./dom.js";
import { LANG, t } from "./studio-i18n.js";

const TEMPLATES = ["streamer", "work"];

const plural = new Intl.PluralRules(LANG);

function tileCount(count) {
  return t(`decks.tiles.${plural.select(count)}`, { count });
}

function fileName(name) {
  const safe = String(name || "deck").replace(/[\\/:*?"<>|]+/g, "_").trim() || "deck";
  return `${safe}.itdeck.json`;
}

export function initDecks(dialog, openButton, { request, workspaces, currentWorkspace, reload, selectWorkspace, showToast }) {
  const status = el("p", { class: "message", role: "status" });
  const list = el("ol", { class: "deck-list", "aria-labelledby": "decks-list-title" });
  const lastHint = el("p", { class: "field-hint", text: t("decks.lastDeck") });

  // One row at a time is being renamed or asked about; both are cleared by
  // any other action and by closing the dialog.
  let renamingId = null;
  let confirmingId = null;
  let busy = false;

  function setStatus(text, isError = false) {
    status.textContent = text;
    status.classList.toggle("error", isError);
  }

  // Runs one backend change with the dialog's buttons held, so a double click
  // can't send it twice. Returns the request's result.
  async function run(action) {
    if (busy) return { ok: false, skipped: true };
    busy = true;
    dialog.classList.add("is-busy");
    try {
      return await action();
    } finally {
      busy = false;
      dialog.classList.remove("is-busy");
    }
  }

  // Redraws the list, then puts focus back where the person was: on the same
  // control of the same deck if it still exists.
  function renderList(focus = null) {
    const decks = workspaces();
    const current = currentWorkspace();
    lastHint.hidden = decks.length > 1;
    list.replaceChildren(...decks.map((deck, index) => renderRow(deck, index, decks.length, current)));
    const naming = list.querySelector('[data-role="name"]');
    if (naming) {
      naming.focus();
      naming.select();
    } else if (focus) {
      const target = list.querySelector(`[data-deck="${focus.id}"] [data-role="${focus.role}"]`)
        || list.querySelector(`[data-deck="${focus.id}"] button`);
      if (target) target.focus();
    }
  }

  function renderRow(deck, index, total, current) {
    const row = el("li", { class: "deck-row", "data-deck": deck.id });
    const isCurrent = current && current.id === deck.id;
    const meta = el("span", { class: "deck-meta" }, [
      tileCount(deck.items.length),
      isCurrent ? el("span", { class: "deck-editing", text: t("decks.editing") }) : null,
    ]);

    if (renamingId === deck.id) {
      row.classList.add("is-editing");
      row.append(renderRename(deck, meta));
      return row;
    }

    const name = el("div", { class: "deck-name" }, [
      el("span", { class: "deck-index", text: String(index + 1), "aria-hidden": "true" }),
      el("div", { class: "deck-text" }, [el("strong", { text: deck.name }), meta]),
    ]);

    const actions = el("div", { class: "deck-actions" }, [
      el("button", {
        type: "button", class: "btn btn-sm btn-icon", text: "↑", "data-role": "up",
        "aria-label": t("decks.moveUp", { name: deck.name }), title: t("decks.moveUp", { name: deck.name }),
        disabled: index === 0 ? "" : null, onClick: () => move(deck, -1),
      }),
      el("button", {
        type: "button", class: "btn btn-sm btn-icon", text: "↓", "data-role": "down",
        "aria-label": t("decks.moveDown", { name: deck.name }), title: t("decks.moveDown", { name: deck.name }),
        disabled: index === total - 1 ? "" : null, onClick: () => move(deck, 1),
      }),
      el("button", {
        type: "button", class: "btn btn-sm", text: t("decks.rename"), "data-role": "rename",
        onClick: () => { renamingId = deck.id; confirmingId = null; setStatus(""); renderList(); },
      }),
      el("button", {
        type: "button", class: "btn btn-sm", text: t("decks.download"), "data-role": "download",
        "aria-label": t("decks.downloadLabel", { name: deck.name }), onClick: () => exportDeck(deck),
      }),
      el("button", {
        type: "button", class: "btn btn-sm btn-danger", text: t("decks.delete"), "data-role": "delete",
        disabled: total < 2 ? "" : null, title: total < 2 ? t("decks.lastDeck") : null,
        onClick: () => { confirmingId = deck.id; renamingId = null; setStatus(""); renderList({ id: deck.id, role: "cancel-delete" }); },
      }),
    ]);
    row.append(name, actions);

    if (confirmingId === deck.id) {
      row.classList.add("is-confirming");
      row.append(renderConfirm(deck));
    }
    return row;
  }

  function renderRename(deck, meta) {
    const input = el("input", {
      class: "input", type: "text", maxlength: "200", value: deck.name,
      "aria-label": t("decks.renameLabel", { name: deck.name }), "data-role": "name",
    });
    const save = el("button", { type: "submit", class: "btn btn-sm btn-primary", text: t("common.save") });
    const cancel = el("button", { type: "button", class: "btn btn-sm btn-ghost", text: t("common.cancel"), onClick: () => stopRenaming(deck) });
    input.addEventListener("input", () => { save.disabled = !input.value.trim(); });
    input.addEventListener("keydown", (event) => {
      // Escape would otherwise close the whole dialog.
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        stopRenaming(deck);
      }
    });
    const form = el("form", { class: "deck-rename" }, [input, save, cancel]);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      rename(deck, input.value);
    });
    return el("div", { class: "deck-rename-wrap" }, [form, meta]);
  }

  function stopRenaming(deck) {
    renamingId = null;
    renderList({ id: deck.id, role: "rename" });
  }

  function renderConfirm(deck) {
    const count = deck.items.length;
    return el("div", { class: "deck-confirm", role: "group", "aria-label": t("decks.delete") }, [
      el("p", {
        class: "deck-confirm-title",
        text: count ? t("decks.confirmDelete", { name: deck.name, count, tiles: tileCount(count) }) : t("decks.confirmDeleteEmpty", { name: deck.name }),
      }),
      el("p", { class: "field-hint", text: t("decks.confirmDeleteBody") }),
      el("div", { class: "deck-confirm-actions" }, [
        count ? el("button", { type: "button", class: "btn btn-sm", text: t("decks.download"), onClick: () => exportDeck(deck) }) : null,
        el("button", {
          type: "button", class: "btn btn-sm btn-ghost", text: t("common.cancel"), "data-role": "cancel-delete",
          onClick: () => { confirmingId = null; renderList({ id: deck.id, role: "delete" }); },
        }),
        el("button", { type: "button", class: "btn btn-sm btn-danger-solid", text: t("decks.deleteGo"), onClick: () => remove(deck) }),
      ]),
    ]);
  }

  // ── Changes ──

  async function move(deck, step) {
    const ids = workspaces().map((w) => w.id);
    const from = ids.indexOf(deck.id);
    const to = from + step;
    if (from < 0 || to < 0 || to >= ids.length) return;
    [ids[from], ids[to]] = [ids[to], ids[from]];
    const result = await run(() => request("/api/workspaces/order", { method: "PUT", body: { ids } }));
    if (result.skipped) return;
    confirmingId = null;
    if (!result.ok) {
      setStatus(t("decks.failed", { detail: result.detail }), true);
      await reload();
      renderList();
      return;
    }
    setStatus("");
    await reload();
    // Focus follows the deck, and the arrow pressed, unless that arrow is now
    // disabled at the end of the list.
    const edge = step < 0 ? to === 0 : to === ids.length - 1;
    renderList({ id: deck.id, role: edge ? (step < 0 ? "down" : "up") : (step < 0 ? "up" : "down") });
  }

  async function rename(deck, value) {
    const name = value.trim();
    if (!name) return;
    if (name === deck.name) {
      stopRenaming(deck);
      return;
    }
    const result = await run(() => request(`/api/workspaces/${deck.id}`, { method: "PATCH", body: { name } }));
    if (result.skipped) return;
    if (!result.ok) {
      setStatus(result.status === 409 ? t("decks.nameTaken", { name }) : t("decks.failed", { detail: result.detail }), true);
      return;
    }
    renamingId = null;
    await reload();
    setStatus(t("decks.renamed", { name }));
    renderList({ id: deck.id, role: "rename" });
  }

  async function remove(deck) {
    const decks = workspaces();
    const index = decks.findIndex((w) => w.id === deck.id);
    // The deck that takes its place in the list, which is also where a phone
    // that was showing it goes.
    const neighbour = decks[index + 1] || decks[index - 1];
    const result = await run(() => request(`/api/workspaces/${deck.id}`, { method: "DELETE" }));
    if (result.skipped) return;
    confirmingId = null;
    if (!result.ok) {
      setStatus(t("decks.failed", { detail: result.detail }), true);
      await reload();
      renderList();
      return;
    }
    const current = currentWorkspace();
    if (current && current.id === deck.id && neighbour) {
      await selectWorkspace(neighbour.id);
    }
    await reload();
    showToast(t("decks.deleted", { name: deck.name }));
    setStatus("");
    renderList(neighbour ? { id: neighbour.id, role: "delete" } : null);
  }

  async function createEmpty() {
    const current = currentWorkspace();
    const body = {
      name: t("decks.newName"),
      // The same grid as the deck on screen: most people have one phone.
      grid_cols: current ? current.grid_cols : 3,
      grid_rows: current ? current.grid_rows : 5,
    };
    const result = await run(() => request("/api/workspaces", { method: "POST", body }));
    if (result.skipped) return;
    if (!result.ok) {
      setStatus(t("decks.failed", { detail: result.detail }), true);
      return;
    }
    await reload();
    await selectWorkspace(result.data.id);
    // Straight into naming it: "New deck" is a placeholder, not a choice.
    renamingId = result.data.id;
    confirmingId = null;
    setStatus(t("decks.created"));
    renderList();
  }

  async function importDeck(deck) {
    setStatus(t("common.saving"));
    const result = await run(() => request("/api/workspaces/import", { method: "POST", body: deck }));
    if (result.skipped) return;
    if (!result.ok) {
      setStatus(t("decks.failed", { detail: result.detail }), true);
      return;
    }
    dialog.close();
    showToast(t("decks.imported", { name: result.data.name }));
    await reload();
    await selectWorkspace(result.data.id);
  }

  async function exportDeck(deck) {
    const result = await request(`/api/workspaces/${deck.id}/export`);
    if (!result.ok) {
      setStatus(t("decks.failed", { detail: result.detail }), true);
      return;
    }
    const blob = new Blob([JSON.stringify(result.data, null, 2) + "\n"], { type: "application/json" });
    const link = el("a", { href: URL.createObjectURL(blob), download: fileName(deck.name) });
    document.body.append(link);
    link.click();
    link.remove();
    // After the click has handed the blob to the download.
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    setStatus(t("decks.exported", { name: deck.name }));
  }

  const fileInput = el("input", { type: "file", accept: ".json,application/json", class: "visually-hidden", tabindex: "-1" });
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    if (!file) return;
    let deck;
    try {
      deck = JSON.parse(await file.text());
    } catch (e) {
      setStatus(t("decks.badFile"), true);
      return;
    }
    importDeck(deck);
  });

  async function useTemplate(id) {
    let deck;
    try {
      const response = await fetch(`/templates/${id}.json`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      deck = await response.json();
    } catch (err) {
      setStatus(t("decks.failed", { detail: err.message }), true);
      return;
    }
    // The file's name is English; the deck gets the name this person reads.
    importDeck({ ...deck, name: t(`template.${id}.name`) });
  }

  // ── Swipe setting ──
  // Read each time the dialog opens: another Studio tab may have changed it.

  const swipeBox = el("input", { type: "checkbox" });
  swipeBox.addEventListener("change", async () => {
    const enabled = swipeBox.checked;
    const result = await request("/api/settings/deck-swipe", { method: "PUT", body: { enabled } });
    if (!result.ok) {
      swipeBox.checked = !enabled;
      setStatus(t("decks.failed", { detail: result.detail }), true);
      return;
    }
    setStatus(t(enabled ? "decks.swipeOn" : "decks.swipeOff"));
  });

  async function loadSwipe() {
    swipeBox.disabled = true;
    try {
      const response = await fetch("/api/settings");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const settings = await response.json();
      swipeBox.checked = settings.deck_swipe !== false;
      swipeBox.disabled = false;
    } catch (err) {
      // Left disabled: a checkbox showing a guess would look like a choice.
      setStatus(t("decks.failed", { detail: err.message }), true);
    }
  }

  const closeButton = el("button", { type: "button", class: "btn btn-ghost", text: t("decks.done"), onClick: () => dialog.close() });
  dialog.replaceChildren(
    el("h2", { id: "decks-title", text: t("decks.title") }),
    el("section", { class: "decks-section" }, [
      el("h3", { id: "decks-list-title", text: t("decks.listTitle") }),
      el("p", { class: "field-hint", text: t("decks.listHint") }),
      list,
      lastHint,
    ]),
    el("section", { class: "decks-section" }, [
      el("h3", { text: t("decks.addTitle") }),
      el("p", { class: "field-hint", text: t("decks.addBody") }),
      el("div", { class: "decks-add" }, [
        el("button", { type: "button", class: "btn", text: t("decks.empty"), onClick: createEmpty }),
        el("button", { type: "button", class: "btn", text: t("decks.import"), onClick: () => fileInput.click() }),
      ]),
      el("p", { class: "field-hint", text: t("decks.importBody") }),
      fileInput,
      el("h4", { id: "decks-templates-title", class: "decks-subtitle", text: t("decks.templatesTitle") }),
      el(
        "ul",
        { class: "decks-templates", "aria-labelledby": "decks-templates-title" },
        TEMPLATES.map((id) =>
          el("li", {}, [
            el("div", {}, [
              el("strong", { text: t(`template.${id}.name`) }),
              el("p", { class: "field-hint", text: t(`template.${id}.desc`) }),
            ]),
            el("button", { type: "button", class: "btn btn-sm", text: t("decks.add"), onClick: () => useTemplate(id) }),
          ]),
        ),
      ),
    ]),
    el("section", { class: "decks-section" }, [
      el("h3", { text: t("decks.phoneTitle") }),
      el("label", { class: "check" }, [swipeBox, t("decks.swipe")]),
      el("p", { class: "field-hint", text: t("decks.swipeHint") }),
    ]),
    status,
    el("div", { class: "dialog-actions" }, [closeButton]),
  );
  dialog.setAttribute("aria-labelledby", "decks-title");
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    renamingId = null;
    confirmingId = null;
    openButton.focus();
  });

  openButton.addEventListener("click", () => {
    setStatus("");
    renamingId = null;
    confirmingId = null;
    renderList();
    loadSwipe();
    dialog.showModal();
    const current = currentWorkspace();
    const first = (current && list.querySelector(`[data-deck="${current.id}"] [data-role="rename"]`)) || list.querySelector("button");
    if (first) first.focus();
  });
}
