// Studio's built-in guide: the README's how-to, cut down to what a person
// does, one short section per task, each with a picture. Opened from the
// "Guide" button in the top bar, and once by itself on the first visit.
//
// Text lives in studio-i18n.js as guide.<section>.title and
// guide.<section>.s1..sN (both languages, same keys). Pictures are in
// frontend/img/guide/ -- not docs/, which the exe does not bundle. A picture
// of Studio itself exists per language (img/guide/<lang>/); one that shows no
// Studio text (the PC window, the themes) is shared.

import { el } from "./dom.js";
import { LANG, t } from "./studio-i18n.js";

const SEEN_KEY = "itdeck:studio-guide-seen";

const SECTIONS = [
  { id: "connect", steps: 3, img: "window.webp", shared: true },
  { id: "tiles", steps: 4, img: "add-tile.webp" },
  { id: "launch", steps: 4, img: ["website.webp", "dock.webp"] },
  { id: "icons", steps: 4, img: "icons.webp" },
  { id: "vpn", steps: 3, img: "vpn.webp" },
  { id: "look", steps: 2, img: "themes.webp", shared: true },
  { id: "trouble", steps: 4, img: null },
];

function imagePaths(section) {
  return [].concat(section.img || []).map((name) =>
    section.shared ? `/img/guide/${name}` : `/img/guide/${LANG}/${name}`,
  );
}

// Whether this browser has opened Studio before (the guide opens by itself
// on the first visit and marks it seen). Read before initGuide() runs, by
// anything that wants to stay quiet for a newcomer -- the What's new chip.
export function isFirstVisit() {
  try {
    return localStorage.getItem(SEEN_KEY) !== "1";
  } catch (e) {
    return false;
  }
}

export function initGuide(dialog, openButton) {
  let current = SECTIONS[0].id;
  const nav = el("nav", { class: "guide-nav", "aria-label": t("guide.title") });
  const content = el("div", { class: "guide-content", tabindex: "-1" });

  function renderNav() {
    nav.replaceChildren(
      ...SECTIONS.map((section, index) =>
        el("button", {
          type: "button",
          class: `guide-tab${section.id === current ? " is-active" : ""}`,
          "aria-current": section.id === current ? "true" : null,
          onClick: () => show(section.id),
        }, [
          el("span", { class: "guide-tab-num", text: String(index + 1) }),
          el("span", { text: t(`guide.${section.id}.title`) }),
        ]),
      ),
    );
  }

  function show(id) {
    current = id;
    const index = SECTIONS.findIndex((section) => section.id === id);
    const section = SECTIONS[index];
    const steps = [];
    for (let n = 1; n <= section.steps; n += 1) {
      steps.push(el("li", { text: t(`guide.${section.id}.s${n}`) }));
    }
    const figures = imagePaths(section).map((src) => {
      const img = el("img", { src, alt: "", decoding: "async" });
      const figure = el("figure", { class: "guide-figure" }, [img]);
      // A missing picture is not worth a broken-image icon: the steps stand on their own.
      img.addEventListener("error", () => figure.remove());
      return figure;
    });
    const next = SECTIONS[index + 1];
    content.replaceChildren(
      el("h3", { class: "guide-heading", text: t(`guide.${section.id}.title`) }),
      el("ol", { class: "guide-steps" }, steps),
      ...figures,
      next
        ? el("button", { type: "button", class: "btn guide-next", text: `${t("guide.next")}: ${t(`guide.${next.id}.title`)} →`, onClick: () => show(next.id) })
        : el("button", { type: "button", class: "btn btn-primary guide-next", text: t("guide.done"), onClick: () => dialog.close() }),
    );
    content.scrollTop = 0;
    renderNav();
  }

  const closeButton = el("button", { type: "button", class: "btn btn-ghost", "aria-label": t("common.close"), text: "✕", onClick: () => dialog.close() });
  dialog.replaceChildren(
    el("div", { class: "guide-head" }, [el("h2", { id: "guide-title", text: t("guide.title") }), closeButton]),
    el("div", { class: "guide-body" }, [nav, content]),
  );
  // A click on the backdrop (the dialog element itself, outside the box) closes it.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    try {
      localStorage.setItem(SEEN_KEY, "1");
    } catch (e) {
      // Blocked storage: it just opens again next visit.
    }
    openButton.focus();
  });

  function open(id = SECTIONS[0].id) {
    show(id);
    if (!dialog.open) dialog.showModal();
    content.focus({ preventScroll: true });
  }

  openButton.addEventListener("click", () => open(current));

  let seen = true;
  try {
    seen = localStorage.getItem(SEEN_KEY) === "1";
  } catch (e) {
    // Can't remember having shown it, so don't risk showing it every time.
  }
  // First visit: open by itself, but never on top of the token dialog.
  if (!seen && !document.querySelector("dialog[open]")) {
    open();
  }
}
