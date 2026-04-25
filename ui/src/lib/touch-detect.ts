/* Detect touch capability ONCE on first interaction. Adds
   data-touch="true" on <html> so CSS can branch beyond just
   hover-media-query. Belt-and-suspenders for stubborn devices. */

function markTouchCapable(): void {
  document.documentElement.dataset.touch = "true";
  removeListeners();
}

function onPointer(e: PointerEvent): void {
  if (e.pointerType === "touch") markTouchCapable();
  else removeListeners();
}

function removeListeners(): void {
  window.removeEventListener("touchstart", markTouchCapable);
  window.removeEventListener("pointerdown", onPointer);
}

export function initTouchDetect(): void {
  if (typeof window === "undefined") return;
  window.addEventListener("touchstart", markTouchCapable, { once: true, passive: true });
  window.addEventListener("pointerdown", onPointer, { passive: true });
}
