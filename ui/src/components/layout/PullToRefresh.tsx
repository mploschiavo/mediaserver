import { type ReactNode, useEffect, useRef } from "react";
import { Loader2 } from "lucide-react";
import {
  usePullToRefresh,
  type PullToRefreshOptions,
} from "@/hooks/usePullToRefresh";
import { cn } from "@/lib/cn";

interface PullToRefreshProps {
  /** Called once a pull past the threshold is released. */
  onRefresh: () => Promise<void> | void;
  /** Distance in px before a refresh fires. */
  threshold?: PullToRefreshOptions["threshold"];
  /** Forces enable/disable; defaults to auto-detect. */
  enabled?: PullToRefreshOptions["enabled"];
  className?: string;
  children: ReactNode;
}

const INDICATOR_HEIGHT = 48;
const INDICATOR_MAX = 64;

/**
 * Wraps a scrollable region with a pull-to-refresh affordance. The
 * top spacer animates from 0 -> indicator height as the user drags
 * past the top of the content; while the supplied `onRefresh`
 * promise is pending the spinner stays parked at full height.
 *
 * The component does not own scrolling itself beyond `overflow-y-auto`
 * so the wrapped children can use normal flow-layout. Mount inside
 * `<main>` so the gesture is scoped to the route content.
 */
export function PullToRefresh({
  onRefresh,
  threshold,
  enabled,
  className,
  children,
}: PullToRefreshProps) {
  const { offset, refreshing, rootProps } = usePullToRefresh({
    onRefresh,
    threshold,
    enabled,
  });

  // Wire the touch listeners imperatively so we can mark them as
  // passive: false where we need to (currently we don't preventDefault,
  // but this avoids React's synthetic-event quirks on TouchEvent).
  const localRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = localRef.current;
    if (!el) return;
    const start = (e: TouchEvent) => rootProps.onTouchStart(e);
    const move = (e: TouchEvent) => rootProps.onTouchMove(e);
    const end = () => rootProps.onTouchEnd();
    el.addEventListener("touchstart", start, { passive: true });
    el.addEventListener("touchmove", move, { passive: true });
    el.addEventListener("touchend", end, { passive: true });
    el.addEventListener("touchcancel", end, { passive: true });
    return () => {
      el.removeEventListener("touchstart", start);
      el.removeEventListener("touchmove", move);
      el.removeEventListener("touchend", end);
      el.removeEventListener("touchcancel", end);
    };
  }, [rootProps]);

  const indicatorHeight = refreshing
    ? INDICATOR_HEIGHT
    : Math.min(offset, INDICATOR_MAX);

  return (
    <div
      ref={(el) => {
        localRef.current = el;
        rootProps.ref(el);
      }}
      className={cn("min-h-full overflow-y-auto", className)}
      data-testid="pull-to-refresh"
    >
      <div
        className="flex items-center justify-center transition-[height] duration-100 ease-out"
        style={{ height: indicatorHeight }}
        aria-hidden={!refreshing && offset === 0}
        data-testid="pull-to-refresh-indicator"
        data-refreshing={refreshing ? "true" : "false"}
      >
        {refreshing ? (
          <Loader2 className="size-5 animate-spin text-accent" aria-hidden />
        ) : null}
      </div>
      {children}
    </div>
  );
}
