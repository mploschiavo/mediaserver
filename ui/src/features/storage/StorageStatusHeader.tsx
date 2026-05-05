import { motion } from "framer-motion";
import { Clock4 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type {
  DiskGuardrailState,
  DiskGuardrailStatus,
} from "./hooks";

interface StorageStatusHeaderProps {
  status: DiskGuardrailStatus;
}

/** Map state to badge variant + tone token. Tests assert via the
 *  `data-tone` attribute (happy-dom drops `oklch()` colour values,
 *  see `test_infra_happydom_oklch` memory). */
function badgeMeta(state: DiskGuardrailState): {
  variant: "success" | "warning" | "danger" | "info" | "default";
  tone: "success" | "warning" | "critical" | "info" | "muted";
  label: string;
} {
  switch (state) {
    case "NORMAL":
      return { variant: "success", tone: "success", label: "NORMAL" };
    case "WATCH":
      return { variant: "info", tone: "info", label: "WATCH" };
    case "CLEANUP":
      return { variant: "warning", tone: "warning", label: "CLEANUP" };
    case "AUTO_LOCKDOWN":
      return { variant: "warning", tone: "warning", label: "AUTO LOCKDOWN" };
    case "MANUAL_LOCKDOWN":
      return { variant: "danger", tone: "critical", label: "MANUAL LOCKDOWN" };
    default:
      return { variant: "default", tone: "muted", label: String(state) };
  }
}

/** Pick the "worst" mount — the one with the highest used percent.
 *  Returns `{label, percent}` or null when the dictionary is empty. */
export function pickWorstMount(
  used: Record<string, number>,
): { label: string; percent: number } | null {
  let best: { label: string; percent: number } | null = null;
  for (const [label, raw] of Object.entries(used)) {
    const percent = typeof raw === "number" ? raw : Number(raw);
    if (!Number.isFinite(percent)) continue;
    if (!best || percent > best.percent) {
      best = { label, percent };
    }
  }
  return best;
}

/** Tone for the usage bar: green ≤ 50, amber ≤ 75, red > 75. */
export function usageTone(
  percent: number,
): "success" | "warning" | "critical" {
  if (percent > 75) return "critical";
  if (percent > 50) return "warning";
  return "success";
}

/** Format an "X minutes ago" / "X hours ago" string from an epoch
 *  seconds value. Returns "just now" for < 60 s. */
export function formatSince(epochSeconds: number, nowSeconds: number): string {
  const diff = Math.max(0, nowSeconds - epochSeconds);
  if (diff < 60) return "just now";
  if (diff < 3600) {
    const m = Math.floor(diff / 60);
    return `${m} minute${m === 1 ? "" : "s"} ago`;
  }
  if (diff < 86400) {
    const h = Math.floor(diff / 3600);
    return `${h} hour${h === 1 ? "" : "s"} ago`;
  }
  const d = Math.floor(diff / 86400);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

/** Format a "until X minutes from now" countdown for the pause-auto
 *  TTL chip. */
export function formatUntil(
  epochSeconds: number,
  nowSeconds: number,
): string {
  const diff = epochSeconds - nowSeconds;
  if (diff <= 0) return "expired";
  if (diff < 60) return `${diff}s left`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m left`;
  return `${Math.floor(diff / 3600)}h left`;
}

export function StorageStatusHeader({ status }: StorageStatusHeaderProps) {
  const meta = badgeMeta(status.state);
  const worst = pickWorstMount(status.used_percent_by_mount);
  const usagePercent = worst?.percent ?? 0;
  const usageToneToken = usageTone(usagePercent);
  // `Date.now()` is intentionally fresh per render — the parent's
  // 30-second poll triggers re-renders so the "since" chip stays
  // current. SSE events also re-render via cache invalidation.
  const nowSeconds = Math.floor(Date.now() / 1000);
  const engagedSince =
    status.engaged_at && status.state !== "NORMAL"
      ? formatSince(status.engaged_at, nowSeconds)
      : null;
  const pausedTtl =
    status.auto_check_paused_until && status.auto_check_paused_until > 0
      ? formatUntil(status.auto_check_paused_until, nowSeconds)
      : null;

  return (
    <div
      className="flex flex-col gap-3"
      data-testid="storage-status-header"
      data-state={status.state}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={meta.variant}
          data-tone={meta.tone}
          data-testid="storage-state-badge"
        >
          {meta.label}
        </Badge>
        {engagedSince ? (
          <span
            className="text-xs text-fg-muted"
            data-testid="storage-engaged-since"
          >
            Engaged {engagedSince}
            {status.engaged_by ? (
              <>
                {" "}
                by{" "}
                <span className="font-mono text-fg">{status.engaged_by}</span>
              </>
            ) : null}
          </span>
        ) : null}
        {pausedTtl ? (
          <Badge
            variant="info"
            data-tone="info"
            data-testid="storage-pause-chip"
          >
            <Clock4 aria-hidden className="size-3" />
            Auto paused — {pausedTtl}
          </Badge>
        ) : null}
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-fg-muted">
            Usage{" "}
            {worst ? (
              <span className="font-mono text-fg">
                ({worst.label})
              </span>
            ) : null}
          </span>
          <span
            className="font-mono tabular-nums text-fg"
            data-testid="storage-usage-percent"
          >
            {usagePercent.toFixed(1)}%
          </span>
        </div>
        <div
          className="relative h-2 w-full overflow-hidden rounded-full bg-bg-2"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(usagePercent)}
          data-testid="storage-usage-bar"
          data-tone={usageToneToken}
        >
          <motion.div
            className="absolute inset-y-0 left-0 rounded-full"
            style={{
              backgroundColor:
                usageToneToken === "critical"
                  ? "var(--color-danger)"
                  : usageToneToken === "warning"
                    ? "var(--color-warning)"
                    : "var(--color-success)",
            }}
            initial={false}
            animate={{ width: `${Math.min(100, Math.max(0, usagePercent))}%` }}
            transition={{ duration: 0.4, ease: "easeOut" }}
          />
        </div>
      </div>

      <div
        className="flex flex-wrap items-center gap-1.5"
        data-testid="storage-paused-clients"
      >
        <span className="text-xs text-fg-muted">Paused clients:</span>
        {status.paused_clients.length === 0 ? (
          <span
            className="text-xs text-fg-faint"
            data-testid="storage-paused-clients-empty"
          >
            none
          </span>
        ) : (
          status.paused_clients.map((c) => (
            <Badge
              key={c}
              variant="outline"
              data-testid={`storage-paused-client-${c}`}
            >
              {c}
            </Badge>
          ))
        )}
      </div>
    </div>
  );
}
