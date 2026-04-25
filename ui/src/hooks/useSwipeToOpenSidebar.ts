import { useCallback, useEffect, useRef, useState } from "react";

export interface SwipeToOpenSidebarOptions {
  /** Fired exactly once per qualifying swipe gesture. */
  onOpen: () => void;
  /** Px from the left edge a touch must start within. */
  edgeWidth?: number;
  /** Min horizontal travel before opening. */
  triggerDistance?: number;
  /** Min horizontal velocity (px/ms) required to qualify. */
  minVelocity?: number;
  /** Max vertical drift relative to horizontal before we abort. */
  verticalTolerance?: number;
  /** Forces enable/disable; defaults to detect via matchMedia. */
  enabled?: boolean;
}

export interface SwipeToOpenSidebarHandlers {
  onTouchStart: (event: TouchEvent) => void;
  onTouchMove: (event: TouchEvent) => void;
  onTouchEnd: (event: TouchEvent) => void;
}

const DEFAULT_EDGE_WIDTH = 24;
const DEFAULT_TRIGGER_DISTANCE = 40;
const DEFAULT_MIN_VELOCITY = 0.3;
const DEFAULT_VERTICAL_TOLERANCE = 0.75;

/**
 * Detects a left-edge horizontal swipe and fires `onOpen`. The
 * gesture qualifies when:
 *  - the touch starts within `edgeWidth` of the viewport's left edge,
 *  - the horizontal distance travelled exceeds `triggerDistance`,
 *  - the average horizontal velocity beats `minVelocity`,
 *  - and the gesture stays mostly horizontal (vertical drift below
 *    `verticalTolerance` * horizontal travel).
 *
 * Returns explicit handlers the consumer can wire onto the document
 * (default) or any element. The hook also auto-binds to `window` for
 * convenience so the AppShell can drop it in without plumbing.
 */
export function useSwipeToOpenSidebar(
  options: SwipeToOpenSidebarOptions,
): SwipeToOpenSidebarHandlers {
  const {
    onOpen,
    edgeWidth = DEFAULT_EDGE_WIDTH,
    triggerDistance = DEFAULT_TRIGGER_DISTANCE,
    minVelocity = DEFAULT_MIN_VELOCITY,
    verticalTolerance = DEFAULT_VERTICAL_TOLERANCE,
    enabled,
  } = options;

  const startRef = useRef<{ x: number; y: number; t: number } | null>(null);
  const onOpenRef = useRef(onOpen);
  useEffect(() => {
    onOpenRef.current = onOpen;
  }, [onOpen]);

  const [autoEnabled, setAutoEnabled] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    if (typeof window.matchMedia !== "function") return true;
    return !window.matchMedia("(hover: hover)").matches;
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia("(hover: hover)");
    const handler = (event: MediaQueryListEvent) =>
      setAutoEnabled(!event.matches);
    setAutoEnabled(!mql.matches);
    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", handler);
      return () => mql.removeEventListener("change", handler);
    }
    mql.addListener(handler);
    return () => mql.removeListener(handler);
  }, []);

  const isEnabled = enabled ?? autoEnabled;

  const onTouchStart = useCallback(
    (event: TouchEvent) => {
      if (!isEnabled) return;
      const touch = event.touches[0];
      if (!touch) return;
      if (touch.clientX > edgeWidth) {
        startRef.current = null;
        return;
      }
      startRef.current = {
        x: touch.clientX,
        y: touch.clientY,
        t: Date.now(),
      };
    },
    [edgeWidth, isEnabled],
  );

  const onTouchMove = useCallback(
    (event: TouchEvent) => {
      if (!isEnabled) return;
      const start = startRef.current;
      if (!start) return;
      const touch = event.touches[0];
      if (!touch) return;
      const dx = touch.clientX - start.x;
      const dy = Math.abs(touch.clientY - start.y);
      // Bail early if the user starts dragging mostly vertically; we
      // do not want to fight a normal scroll.
      if (dx > 0 && dy > dx * verticalTolerance + 12) {
        startRef.current = null;
      }
    },
    [isEnabled, verticalTolerance],
  );

  const onTouchEnd = useCallback(
    (event: TouchEvent) => {
      if (!isEnabled) return;
      const start = startRef.current;
      startRef.current = null;
      if (!start) return;
      const touch = event.changedTouches[0];
      if (!touch) return;
      const dx = touch.clientX - start.x;
      const dy = Math.abs(touch.clientY - start.y);
      const dt = Math.max(1, Date.now() - start.t);
      const velocity = dx / dt;
      if (dx < triggerDistance) return;
      if (velocity < minVelocity) return;
      if (dy > dx * verticalTolerance + 12) return;
      onOpenRef.current();
    },
    [isEnabled, minVelocity, triggerDistance, verticalTolerance],
  );

  // Auto-bind to window so AppShell can simply call the hook; the
  // explicit handlers are still returned for tests / opt-out.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!isEnabled) return;
    const start = (e: TouchEvent) => onTouchStart(e);
    const move = (e: TouchEvent) => onTouchMove(e);
    const end = (e: TouchEvent) => onTouchEnd(e);
    window.addEventListener("touchstart", start, { passive: true });
    window.addEventListener("touchmove", move, { passive: true });
    window.addEventListener("touchend", end, { passive: true });
    window.addEventListener("touchcancel", end, { passive: true });
    return () => {
      window.removeEventListener("touchstart", start);
      window.removeEventListener("touchmove", move);
      window.removeEventListener("touchend", end);
      window.removeEventListener("touchcancel", end);
    };
  }, [isEnabled, onTouchStart, onTouchMove, onTouchEnd]);

  return { onTouchStart, onTouchMove, onTouchEnd };
}
