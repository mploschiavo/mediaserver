import { useQuery } from "@tanstack/react-query";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { motion } from "framer-motion";
import { useEffect, useRef, useState, type JSX } from "react";
import { cn } from "@/lib/cn";
import { useEventStreamStatus } from "@/lib/events/EventStreamProvider";

type Status = "live" | "degraded" | "dead";

interface PingResult {
  ok: boolean;
  latencyMs: number;
  fetchedAt: number;
}

async function pingHealthz(): Promise<PingResult> {
  const start = performance.now();
  const res = await fetch("/api/health", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal: AbortSignal.timeout(8_000),
  });
  return {
    ok: res.ok,
    latencyMs: Math.round(performance.now() - start),
    fetchedAt: Date.now(),
  };
}

/**
 * Connection-status indicator. Polls `/api/health` every 10s via
 * Tanstack Query, then derives a tri-state from the result:
 *   - live      -> 200 within 10s, dot stays green
 *   - degraded  -> last response > 15s and < 30s ago, dot yellow
 *   - dead      -> error, or no response in last 30s, dot red
 *
 * The wall-clock age is recomputed every second locally so the
 * tooltip reads truthfully even between polls.
 */
export function ConnectionStatus(): JSX.Element {
  const lastSuccessRef = useRef<PingResult | null>(null);
  const sseStatus = useEventStreamStatus();
  const query = useQuery({
    queryKey: ["healthz"],
    queryFn: pingHealthz,
    refetchInterval: 15_000,
    // Don't burn cycles polling when the tab is hidden; the next
    // foreground tick will refresh on its own. The previous
    // ``refetchIntervalInBackground: true`` was contributing to
    // browser-tab lag with many media-stack tabs open.
    refetchIntervalInBackground: false,
    retry: false,
    staleTime: 0,
  });

  if (query.data?.ok) {
    lastSuccessRef.current = query.data;
  }

  // Tick every 5s so the tooltip's age-based label stays roughly
  // truthful between polls. The previous 1Hz ticker was the
  // layout's most aggressive re-render trigger (every page hosts
  // this header), contributing to perceptible UI lag with many
  // panels mounted.
  const [, setTick] = useState(0);
  useEffect(() => {
    const handle = window.setInterval(() => setTick((n) => n + 1), 5_000);
    return () => window.clearInterval(handle);
  }, []);

  const last = lastSuccessRef.current;
  const ageMs = last ? Date.now() - last.fetchedAt : Number.POSITIVE_INFINITY;
  const status: Status = (() => {
    if (query.isError && !last) return "dead";
    if (!last) return "degraded";
    if (ageMs > 30_000) return "dead";
    if (ageMs > 15_000 || query.isError) return "degraded";
    return "live";
  })();

  const sseLabel = sseStatus.isOpen
    ? "SSE live (events streaming)"
    : "SSE polling (REST refetch)";
  const tooltip = (() => {
    let head: string;
    if (status === "live" && last) {
      head = `Controller healthy · ${last.latencyMs}ms`;
    } else if (status === "degraded" && last) {
      head = `Controller degraded · last response ${Math.round(ageMs / 1000)}s ago`;
    } else if (last) {
      head = `Controller unreachable · last response ${Math.round(ageMs / 1000)}s ago`;
    } else {
      head = "Controller unreachable";
    }
    return `${head} · ${sseLabel}`;
  })();

  return (
    <TooltipPrimitive.Root>
      <TooltipPrimitive.Trigger asChild>
        <button
          type="button"
          aria-label={tooltip}
          className="flex size-7 items-center justify-center rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <motion.span
            key={`${status}-${sseStatus.isOpen ? "sse" : "poll"}`}
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            data-sse={sseStatus.isOpen ? "live" : "polling"}
            className={cn(
              "relative inline-flex size-2.5 rounded-full",
              status === "live" && "bg-success",
              status === "degraded" && "bg-warning",
              status === "dead" && "bg-danger",
              // Thin accent ring when SSE is live — visually
              // distinct from controller health, conveys the
              // "events are streaming" mode to the operator at a
              // glance.
              sseStatus.isOpen && "ring-2 ring-info/60",
            )}
          >
            {status === "live" ? (
              <span className="absolute inset-0 animate-ping rounded-full bg-success/60" />
            ) : null}
          </motion.span>
        </button>
      </TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side="bottom"
          sideOffset={6}
          className="z-50 rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs text-popover-fg shadow-md"
        >
          {tooltip}
          <TooltipPrimitive.Arrow className="fill-popover" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
