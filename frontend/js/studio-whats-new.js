// Studio's "What's new" chip: a quiet button in the top bar with a dot while
// this version's notes are unread, and a small dialog listing them.
//
// Deliberately not a popup. The IT-Deck window on the PC already shows the
// same notes once after an update (standalone/launcher.py); Studio only
// offers them, so nobody is interrupted twice by the same news. A first-ever
// visit marks them read without a dot: the guide is a newcomer's welcome,
// release notes mean nothing yet.
//
// Content: frontend/whats-new.json, newest first, shared with the launcher.

import { el } from "./dom.js";
import { LANG, t } from "./studio-i18n.js";

const SEEN_KEY = "itdeck:whats-new-seen";
// Same cap as the launcher's card: a person who skipped updates gets the
// latest two, not a changelog.
const MAX_VERSIONS = 2;

function readSeen() {
  try {
    return localStorage.getItem(SEEN_KEY);
  } catch (e) {
    return null;
  }
}

function writeSeen(version) {
  try {
    localStorage.setItem(SEEN_KEY, version);
  } catch (e) {
    // Blocked storage: the dot just shows again next visit.
  }
}

export async function initWhatsNew(dialog, button, { firstVisit }) {
  let entries;
  try {
    const response = await fetch("/whats-new.json");
    if (!response.ok) return;
    entries = await response.json();
  } catch (e) {
    return; // No notes, no chip -- nothing here is worth an error message.
  }
  if (!Array.isArray(entries) || !entries.length || !entries[0].version) return;

  const newest = entries[0].version;
  if (firstVisit && !readSeen()) writeSeen(newest);

  const dot = el("span", { class: "news-dot", "aria-hidden": "true" });
  const hint = el("span", { class: "visually-hidden", text: t("news.unread") });
  function markUnread(unread) {
    dot.hidden = !unread;
    hint.hidden = !unread;
  }
  button.append(dot, hint);
  markUnread(readSeen() !== newest);
  button.hidden = false;

  const shown = entries.slice(0, MAX_VERSIONS);
  const blocks = shown.map((entry) =>
    el("section", { class: "news-version" }, [
      shown.length > 1 ? el("h3", { text: entry.version }) : null,
      el(
        "ul",
        { class: "news-list" },
        (entry[LANG] || entry.en || []).map((line) => el("li", { text: line })),
      ),
    ]),
  );
  dialog.replaceChildren(
    el("h2", { id: "news-title", text: t("news.title", { version: newest }) }),
    ...blocks,
    el("div", { class: "dialog-actions" }, [
      el("a", {
        class: "btn btn-ghost",
        href: "https://github.com/Itegin/it-deck/releases",
        target: "_blank",
        rel: "noopener noreferrer",
        text: t("news.all"),
      }),
      el("button", { type: "button", class: "btn btn-primary", text: t("news.ok"), onClick: () => dialog.close() }),
    ]),
  );
  dialog.setAttribute("aria-labelledby", "news-title");
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => button.focus());

  button.addEventListener("click", () => {
    writeSeen(newest);
    markUnread(false);
    dialog.showModal();
    dialog.querySelector(".btn-primary").focus();
  });
}
