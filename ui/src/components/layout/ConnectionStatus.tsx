import { useQuery } from "@tanstack/react-query";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

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
export function ConnectionStatus() {
  const lastSuccessRef = useRef<PingResult | null>(null);
  const query = useQuery({
    queryKey: ["healthz"],
    queryFn: pingHealthz,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
    retry: false,
    staleTime: 0,
  });

  if (query.data?.ok) {
    lastSuccessRef.current = query.data;
  }

  // Tick once a second so age-based status (degraded/dead) updates
  // even without a fresh poll. We only rerender for the tooltip to
  // stay accurate; the dot color still derives from these tracks.
  const [, setTick] = useState(0);
  useEffect(() => {
    const handle = window.setInterval(() => setTick((n) => n + 1), 1_000);
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

  const tooltip = (() => {
    if (status === "live" && last) {
      return `Controller healthy · ${last.latencyMs}ms`;
    }
    if (status === "degraded" && last) {
      return `Controller degraded · last response ${Math.round(ageMs / 1000)}s ago`;
    }
    if (last) {
      return `Controller unreachable · last response ${Math.round(ageMs / 1000)}s ago`;
    }
    return "Controller unreachable";
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
            key={status}
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className={cn(
              "relative inline-flex size-2.5 rounded-full",
              status === "live" && "bg-success",
              status === "degraded" && "bg-warning",
              status === "dead" && "bg-danger",
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
