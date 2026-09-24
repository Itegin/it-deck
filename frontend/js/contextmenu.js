let currentOverlay = null;
// Where focus was when the menu opened, so dismissing can put it back. A
// keyboard user who opens the menu from a tile and cancels must land on that
// tile again, not at the top of the document.
let previouslyFocused = null;

// The sheet every phone-side choice slides up in: the long-press menu, the
// "Run?" confirmation and the Send-text box. One overlay, one set of rules --
// modal, Escape and the backdrop dismiss it, Tab stays inside, and focus goes
// back to the tile afterwards.
function openSheet(item, fill) {
  dismissContextMenu();

  previouslyFocused = document.activeElement;

  const overlay = document.createElement("div");
  overlay.className = "context-overlay";
  // It covers the deck and takes every press, so it has to say so: without
  // these a screen reader treats it as an ordinary div and keeps reading the
  // tiles behind it as if they were still available.
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", `Actions for ${item.label}`);
  // Only the dimmed backdrop itself dismisses -- row clicks already
  // dismiss+act in their own handler, and would double-fire here too since
  // click events bubble up from a row to the overlay.
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) {
      dismissContextMenu();
    }
  });

  const panel = document.createElement("div");
  panel.className = "context-panel";

  const header = document.createElement("div");
  header.className = "context-header";
  header.textContent = item.label;
  panel.appendChild(header);

  fill(panel);

  // Escape is the keyboard equivalent of tapping the backdrop. Bound on the
  // overlay rather than on document, so it is removed with the element and
  // there is no listener to leak or to fire after the sheet is gone.
  overlay.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      dismissContextMenu();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    // Keep Tab inside the sheet: with aria-modal set, focus escaping to the
    // tiles behind would put the user somewhere the sheet claims is inert.
    const focusable = panel.querySelectorAll("button:not(:disabled), textarea");
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  currentOverlay = overlay;

  // Into the sheet, so the first thing a keyboard or screen-reader user meets
  // is what they just opened: the first action, or the text box.
  const firstFocus = panel.querySelector("textarea, button");
  if (firstFocus) {
    firstFocus.focus();
  }

  // Deferred a frame so the initial opacity:0 (set in CSS) paints before
  // switching to opacity:1 -- otherwise the browser can coalesce both
  // into a single frame and the transition never plays.
  requestAnimationFrame(() => {
    overlay.classList.add("visible");
  });
}

function sheetRow(option) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "context-row";
  if (option.destructive) {
    row.classList.add("destructive");
  }
  row.textContent = option.label;
  row.addEventListener("click", () => {
    dismissContextMenu();
    option.action();
  });
  return row;
}

// options: {label, action, destructive?}[]. destructive is what
// contextmenu.css's "red text for destructive rows" rule keys off.
export function showContextMenu(item, options) {
  openSheet(item, (panel) => {
    for (const option of options) {
      panel.appendChild(sheetRow(option));
    }
  });
}

// A text box and Send / Cancel, for the Send-text tile. onSubmit gets the
// text as typed; Send stays disabled until there is something to send.
export function showTextSheet(item, { placeholder, sendLabel, cancelLabel, maxLength, onSubmit }) {
  openSheet(item, (panel) => {
    const input = document.createElement("textarea");
    input.className = "context-textarea";
    input.rows = 4;
    input.maxLength = maxLength;
    input.placeholder = placeholder;
    input.setAttribute("aria-label", placeholder);
    panel.appendChild(input);

    const send = sheetRow({ label: sendLabel, action: () => onSubmit(input.value) });
    send.disabled = true;
    input.addEventListener("input", () => {
      send.disabled = !input.value.trim();
    });
    panel.appendChild(send);
    panel.appendChild(sheetRow({ label: cancelLabel, action: () => {} }));
  });
}

export function dismissContextMenu() {
  if (currentOverlay) {
    currentOverlay.remove();
    currentOverlay = null;
    // Removing the focused element drops focus to <body>; hand it back to
    // whatever had it. Guarded on isConnected because a workspace re-render
    // can retire that tile while the menu is open.
    if (previouslyFocused && previouslyFocused.isConnected) {
      previouslyFocused.focus();
    }
    previouslyFocused = null;
  }
}
