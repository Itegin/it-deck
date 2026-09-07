// One shared element for the whole dashboard, not one per tile. The tile's
// error ring says *which* tile failed; this says *why*. A tile-sized space
// can't hold a sentence, and two tiles failing at once would fight over the
// same strip -- a single element that the newest message wins is simpler and
// never overlaps itself.
const TOAST_MS = 3500;

let toastEl = null;
let hideTimer = null;

export function showToast(message) {
  if (!toastEl) {
    // Created on first use and then kept, the same way contextmenu.js builds
    // its overlay in JS rather than shipping empty markup in index.html.
    toastEl = document.createElement("div");
    toastEl.className = "toast";
    // Announced to a screen reader without moving focus -- this is a touch
    // surface, and yanking focus mid-tap would be worse than a missed
    // message. role=status is implicitly polite; aria-live is belt and braces
    // for older iOS VoiceOver builds.
    toastEl.setAttribute("role", "status");
    toastEl.setAttribute("aria-live", "polite");
    document.body.appendChild(toastEl);
  }

  toastEl.textContent = message;

  // Deferred a frame for the same reason showContextMenu defers: the element
  // has to paint once at opacity 0 before the transition to 1 can play,
  // otherwise the browser coalesces both into a single frame.
  requestAnimationFrame(() => {
    if (toastEl) {
      toastEl.classList.add("visible");
    }
  });

  // A second failure while one is still showing restarts the clock rather
  // than inheriting the remainder of the first one's.
  if (hideTimer !== null) {
    clearTimeout(hideTimer);
  }
  hideTimer = setTimeout(() => {
    hideTimer = null;
    toastEl.classList.remove("visible");
  }, TOAST_MS);
}
