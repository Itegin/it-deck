const LONG_PRESS_MS = 500;
const MOVE_CANCEL_PX = 10;

// A stationary pointerdown held past LONG_PRESS_MS fires onLongPress instead
// of onTap. Movement past MOVE_CANCEL_PX makes it neither: the pointer is
// dragging, not pressing -- a swipe to the next deck that happens to start on
// a tile must not also run that tile.
export function attachLongPress(element, onLongPress, onTap) {
  let timer = null;
  let startX = 0;
  let startY = 0;
  let firedLongPress = false;
  let moved = false;

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  element.addEventListener("pointerdown", (event) => {
    firedLongPress = false;
    moved = false;
    startX = event.clientX;
    startY = event.clientY;
    clearTimer();
    timer = setTimeout(() => {
      firedLongPress = true;
      timer = null;
      onLongPress();
    }, LONG_PRESS_MS);
  });

  element.addEventListener("pointermove", (event) => {
    if (moved || firedLongPress) {
      return;
    }
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (Math.hypot(dx, dy) > MOVE_CANCEL_PX) {
      moved = true;
      clearTimer();
    }
  });

  element.addEventListener("pointerup", () => {
    const wasPress = !firedLongPress && !moved;
    clearTimer();
    if (wasPress) {
      onTap();
    }
  });

  element.addEventListener("pointercancel", clearTimer);
  element.addEventListener("pointerleave", clearTimer);
}
