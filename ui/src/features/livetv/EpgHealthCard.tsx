import { Activity, Copy } from "lucide-react";
import { toast } from "sonner";
import { asArray } from "@/lib/coerce";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelative } from "@/features/media-integrity/format";
import { useEpgHealth } from "./hooks";

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (
      typeof navigator !== "undefined" &&
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function"
    ) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through
  }
  return false;
}

interface EpgHealthLikeData {
  healthy?: number;
  unhealthy?: number;
  countries?: number;
  providers?: number;
  details?: Record<string, Record<string, boolean>>;
  // Legacy fields retained for forward-compat — not actually
  // emitted by current /api/epg-health.
  ok?: boolean;
  status?: string;
  last_run?: string;
  errors?: readonly string[] | number;
  missing_channels?: readonly string[];
}

/**
 * The live ``/api/epg-health`` payload reports per-(country,
 * provider) reachability:
 *   ``{healthy: 81, unhealthy: 25, countries: 34, providers: 5,
 *      details: {us: {iptv-epg: true, …}, …}}``
 * So "healthy" here means "majority of probes succeeded". The
 * card was previously coded against an aspirational
 * ``ok``/``status`` field that never landed — every render said
 * "failing" because every legacy field was undefined.
 */
function isHealthy(data: EpgHealthLikeData): boolean {
  if (typeof data.ok === "boolean") return data.ok;
  if (typeof data.status === "string") {
    const s = data.status.toLowerCase();
    if (s) return s === "ok" || s === "healthy" || s === "success";
  }
  // Live shape: pass when more probes succeeded than failed.
  if (typeof data.healthy === "number" && typeof data.unhealthy === "number") {
    return data.healthy > data.unhealthy;
  }
  return false;
}

/**
 * Count the per-(country, provider) probes that failed in the live
 * ``details`` map. Falls through to legacy ``errors`` when the
 * controller emits the older shape.
 */
function errorCount(data: EpgHealthLikeData): number {
  if (typeof data.errors === "number" && Number.isFinite(data.errors)) {
    return data.errors;
  }
  if (Array.isArray(data.errors)) return data.errors.length;
  if (typeof data.unhealthy === "number") return data.unhealthy;
  return 0;
}

/** Render the per-country breakdown as ``"<country>: <ok>/<total>"``
 *  rows, sorted by failure rate descending so the worst offenders
 *  surface first. Empty list when the controller emits no
 *  ``details`` map (older shape — caller hides the section). */
function summarizeDetails(
  details: Record<string, Record<string, boolean>> | undefined,
): { code: string; ok: number; total: number }[] {
  if (!details) return [];
  return Object.entries(details)
    .map(([code, probes]) => {
      const values = Object.values(probes ?? {});
      const ok = values.filter(Boolean).length;
      return { code, ok, total: values.length };
    })
    .filter((row) => row.total > 0)
    .sort((a, b) => (a.ok / a.total) - (b.ok / b.total));
}

/**
 * Compact EPG health chip — shows the last fetch time, success/fail
 * badge, error count, and a click-to-copy list of any channels missing
 * guide data.
 */
export function EpgHealthCard() {
  const health = useEpgHealth();

  const handleCopy = async (channel: string) => {
    const ok = await writeClipboard(channel);
    if (ok) {
      toast.success(`Copied ${channel}`);
    } else {
      toast.error("Clipboard unavailable");
    }
  };

  return (
    <Card data-testid="epg-health-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity aria-hidden className="size-4 text-fg-muted" />
          EPG health
        </CardTitle>
        <CardDescription>
          Status of the last guide fetch — any errors and channels
          missing programme data.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {health.isLoading ? (
          <div className="space-y-2" data-testid="epg-health-loading">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-48" />
          </div>
        ) : health.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="epg-health-error"
          >
            {health.error.message}
          </p>
        ) : !health.data ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="epg-health-empty"
          >
            No EPG runs reported yet.
          </p>
        ) : (
          <HealthBody data={health.data} onCopy={handleCopy} />
        )}
      </CardContent>
    </Card>
  );
}

interface HealthBodyProps {
  data: NonNullable<ReturnType<typeof useEpgHealth>["data"]>;
  onCopy: (channel: string) => void;
}

function HealthBody({ data, onCopy }: HealthBodyProps) {
  const healthy = isHealthy(data);
  const errors = errorCount(data);
  const missing = asArray<string>(data.missing_channels);
  const detailRows = summarizeDetails(data.details);
  const totalProbes = (data.healthy ?? 0) + (data.unhealthy ?? 0);

  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge
          variant={healthy ? "success" : "danger"}
          data-testid="epg-health-status"
        >
          {healthy ? "healthy" : "failing"}
        </Badge>
        {totalProbes > 0 ? (
          <span
            className="tabular-nums text-fg-muted"
            data-testid="epg-probe-summary"
          >
            {data.healthy ?? 0}/{totalProbes} probes ok
            {typeof data.countries === "number" && typeof data.providers === "number"
              ? ` · ${data.countries} countries · ${data.providers} providers`
              : ""}
          </span>
        ) : data.last_run ? (
          <span className="tabular-nums text-fg-muted">
            last run {formatRelative(data.last_run)}
          </span>
        ) : null}
        {errors > 0 ? (
          <Badge variant="danger" data-testid="epg-health-errors">
            {errors} failing
          </Badge>
        ) : null}
      </div>
      {detailRows.length > 0 ? (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-fg-muted">
            Worst-coverage countries (top 8)
          </p>
          <ul
            className="flex flex-col gap-1 rounded-md border border-border bg-bg-1 p-2 text-xs"
            data-testid="epg-country-details"
          >
            {detailRows.slice(0, 8).map((row) => (
              <li key={row.code} className="flex items-center justify-between gap-2">
                <span className="font-mono">{row.code}</span>
                <span className="tabular-nums text-fg-muted">
                  {row.ok}/{row.total} providers ok
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {missing.length > 0 ? (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-fg-muted">
            Missing channels ({missing.length})
          </p>
          <ul
            className="flex flex-col gap-1 rounded-md border border-border bg-bg-1 p-2 text-xs"
            data-testid="epg-missing-channels"
          >
            {missing.map((ch) => (
              <li key={ch} className="flex items-center justify-between gap-2">
                <span className="truncate font-mono">{ch}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => onCopy(ch)}
                  aria-label={`Copy ${ch}`}
                  data-testid={`epg-copy-${ch}`}
                >
                  <Copy aria-hidden /> copy
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
