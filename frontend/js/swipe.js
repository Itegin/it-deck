// A horizontal swipe on the deck moves to the next or previous deck.
//
// Touch and pen only: on a desktop a mouse drag across the tiles is more
// likely a slip than a gesture. It never starts on a slider (the volume tile
// owns horizontal drags) or inside a sheet, and it has to be clearly
// sideways and quick, so a sloppy tap or a vertical flick changes nothing.
// A tile the swipe starts on isn't pressed either -- js/longpress.js treats
// a pointer that moved as a drag, not a tap.

const MIN_DISTANCE_PX = 60;
const MAX_DURATION_MS = 800;

export function attachDeckSwipe(surface, onSwipe) {
  let start = null;

  surface.addEventListener("pointerdown", (event) => {
    start = null;
    if (!event.isPrimary || event.pointerType === "mouse") return;
    if (event.target.closest('[role="slider"], .context-overlay')) return;
    start = { x: event.clientX, y: event.clientY, at: performance.now() };
  });

  surface.addEventListener("pointerup", (event) => {
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    const quick = performance.now() - start.at <= MAX_DURATION_MS;
    start = null;
    if (quick && Math.abs(dx) >= MIN_DISTANCE_PX && Math.abs(dx) > 2 * Math.abs(dy)) {
      // Finger moving left brings in the deck on the right, as on any phone.
      onSwipe(dx < 0 ? 1 : -1);
    }
  });

  surface.addEventListener("pointercancel", () => {
    start = null;
  });
}
