import { useCallback, useEffect, useRef, useState } from "react";

export interface PullToRefreshOptions {
  /** Called once a pull past the threshold is released. */
  onRefresh: () => Promise<void> | void;
  /** Distance in px the user must drag before a release fires. */
  threshold?: number;
  /** 0..1 rubber-band factor; lower = stiffer pull. */
  resistance?: number;
  /** Forces enable/disable; when omitted, derives from matchMedia. */
  enabled?: boolean;
}

export interface PullToRefreshRootProps {
  ref: (el: HTMLElement | null) => void;
  onTouchStart: (event: TouchEvent) => void;
  onTouchMove: (event: TouchEvent) => void;
  onTouchEnd: () => void;
}

export interface PullToRefreshState {
  /** True while the user is actively dragging past scrollTop=0. */
  pulling: boolean;
  /** Current dampened pull distance in px (>= 0). */
  offset: number;
  /** True while `onRefresh` is still in flight. */
  refreshing: boolean;
  /** Spread onto the scrollable root element. */
  rootProps: PullToRefreshRootProps;
}

const DEFAULT_THRESHOLD = 80;
const DEFAULT_RESISTANCE = 0.5;
const MAX_OFFSET = 160;

/**
 * Touch-driven pull-to-refresh primitive. The hook tracks a single
 * touch starting at scrollTop === 0 and emits a dampened `offset`
 * the consumer renders as a top spacer. When the user releases past
 * the threshold the supplied `onRefresh` fires; `refreshing` stays
 * true until the returned promise settles.
 *
 * Disabled by default on devices that report `(hover: hover)` (i.e.
 * desktop pointer environments) since pull-to-refresh is a touch
 * gesture; pass `enabled` explicitly to override.
 */
export function usePullToRefresh(
  options: PullToRefreshOptions,
): PullToRefreshState {
  const {
    onRefresh,
    threshold = DEFAULT_THRESHOLD,
    resistance = DEFAULT_RESISTANCE,
    enabled,
  } = options;

  const [pulling, setPulling] = useState(false);
  const [offset, setOffset] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [autoEnabled, setAutoEnabled] = useState<boolean>(() => {
    if (typeof window === "undefined") return true;
    if (typeof window.matchMedia !== "function") return true;
    return !window.matchMedia("(hover: hover)").matches;
  });

  const elRef = useRef<HTMLElement | null>(null);
  const startYRef = useRef<number | null>(null);
  const activeRef = useRef<boolean>(false);
  const refreshingRef = useRef<boolean>(false);
  // Mirror `offset` into a ref so the touchend handler reads the
  // latest dampened distance even when several touch events fire
  // inside a single render tick (React batches the setOffset call,
  // so the closure's `offset` would otherwise still be zero).
  const offsetRef = useRef<number>(0);

  // Refresh handler can change identity between renders without
  // tearing the gesture down; we always invoke the latest version.
  const onRefreshRef = useRef(onRefresh);
  useEffect(() => {
    onRefreshRef.current = onRefresh;
  }, [onRefresh]);

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
    // Older Safari fallback.
    mql.addListener(handler);
    return () => mql.removeListener(handler);
  }, []);

  const isEnabled = enabled ?? autoEnabled;

  const setRef = useCallback((el: HTMLElement | null) => {
    elRef.current = el;
  }, []);

  const onTouchStart = useCallback(
    (event: TouchEvent) => {
      if (!isEnabled) return;
      if (refreshingRef.current) return;
      const el = elRef.current;
      if (!el) return;
      if (el.scrollTop > 0) return;
      const touch = event.touches[0];
      if (!touch) return;
      startYRef.current = touch.clientY;
      activeRef.current = true;
    },
    [isEnabled],
  );

  const onTouchMove = useCallback(
    (event: TouchEvent) => {
      if (!isEnabled) return;
      if (!activeRef.current) return;
      const start = startYRef.current;
      if (start == null) return;
      const touch = event.touches[0];
      if (!touch) return;
      const delta = touch.clientY - start;
      if (delta <= 0) {
        if (offset !== 0) setOffset(0);
        if (pulling) setPulling(false);
        return;
      }
      const dampened = Math.min(delta * resistance, MAX_OFFSET);
      offsetRef.current = dampened;
      setPulling(true);
      setOffset(dampened);
    },
    [isEnabled, offset, pulling, resistance],
  );

  const onTouchEnd = useCallback(() => {
    if (!activeRef.current) return;
    activeRef.current = false;
    startYRef.current = null;
    // Read the dampened distance from the ref (always current within
    // the same tick) rather than the closure-captured state.
    const reached = offsetRef.current >= threshold;
    offsetRef.current = 0;
    setPulling(false);
    setOffset(0);
    if (!reached) return;
    refreshingRef.current = true;
    setRefreshing(true);
    Promise.resolve()
      .then(() => onRefreshRef.current())
      .finally(() => {
        refreshingRef.current = false;
        setRefreshing(false);
      });
  }, [threshold]);

  return {
    pulling,
    offset,
    refreshing,
    rootProps: {
      ref: setRef,
      onTouchStart,
      onTouchMove,
      onTouchEnd,
    },
  };
}
