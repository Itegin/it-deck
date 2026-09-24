// Studio's Decks dialog: download the current deck as a file, add a deck from
// a file, or start one from a template (frontend/templates/*.json).
//
// Everything goes through the backend's deck-file endpoints
// (GET /api/workspaces/{id}/export, POST /api/workspaces/import). An import
// always creates a NEW deck -- it never changes the one on screen -- and is
// validated by the same rules as a tile saved here, all-or-nothing.

import { el } from "./dom.js";
import { t } from "./studio-i18n.js";

const TEMPLATES = ["streamer", "work"];

function fileName(name) {
  const safe = String(name || "deck").replace(/[\\/:*?"<>|]+/g, "_").trim() || "deck";
  return `${safe}.itdeck.json`;
}

export function initDecks(dialog, openButton, { request, currentWorkspace, onImported, showToast }) {
  const status = el("p", { class: "message", role: "status" });

  function setStatus(text, isError = false) {
    status.textContent = text;
    status.classList.toggle("error", isError);
  }

  async function importDeck(deck) {
    setStatus(t("common.saving"));
    const result = await request("/api/workspaces/import", { method: "POST", body: deck });
    if (!result.ok) {
      setStatus(t("decks.failed", { detail: result.detail }), true);
      return;
    }
    dialog.close();
    showToast(t("decks.imported", { name: result.data.name }));
    onImported(result.data.id);
  }

  async function exportDeck() {
    const workspace = currentWorkspace();
    if (!workspace) return;
    const result = await request(`/api/workspaces/${workspace.id}/export`);
    if (!result.ok) {
      setStatus(t("decks.failed", { detail: result.detail }), true);
      return;
    }
    const blob = new Blob([JSON.stringify(result.data, null, 2) + "\n"], { type: "application/json" });
    const link = el("a", { href: URL.createObjectURL(blob), download: fileName(workspace.name) });
    document.body.append(link);
    link.click();
    link.remove();
    // After the click has handed the blob to the download.
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    setStatus(t("decks.exported"));
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

  const closeButton = el("button", { type: "button", class: "btn btn-ghost", text: t("news.ok"), onClick: () => dialog.close() });
  dialog.replaceChildren(
    el("h2", { id: "decks-title", text: t("decks.title") }),
    el("section", { class: "decks-section" }, [
      el("h3", { text: t("decks.exportTitle") }),
      el("p", { class: "field-hint", text: t("decks.exportBody") }),
      el("button", { type: "button", class: "btn", text: t("decks.export"), onClick: exportDeck }),
    ]),
    el("section", { class: "decks-section" }, [
      el("h3", { text: t("decks.importTitle") }),
      el("p", { class: "field-hint", text: t("decks.importBody") }),
      el("button", { type: "button", class: "btn", text: t("decks.import"), onClick: () => fileInput.click() }),
      fileInput,
    ]),
    el("section", { class: "decks-section" }, [
      el("h3", { text: t("decks.templatesTitle") }),
      el(
        "ul",
        { class: "decks-templates" },
        TEMPLATES.map((id) =>
          el("li", {}, [
            el("div", {}, [
              el("strong", { text: t(`template.${id}.name`) }),
              el("p", { class: "field-hint", text: t(`template.${id}.desc`) }),
            ]),
            el("button", { type: "button", class: "btn", text: t("decks.add"), onClick: () => useTemplate(id) }),
          ]),
        ),
      ),
    ]),
    status,
    el("div", { class: "dialog-actions" }, [closeButton]),
  );
  dialog.setAttribute("aria-labelledby", "decks-title");
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => openButton.focus());

  openButton.addEventListener("click", () => {
    setStatus("");
    dialog.showModal();
    dialog.querySelector(".decks-section .btn").focus();
  });
}
