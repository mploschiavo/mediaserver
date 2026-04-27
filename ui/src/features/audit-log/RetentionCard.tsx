import { Archive } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuditLogStats } from "./hooks";

/**
 * Operator-visible retention banner. Answers: "how many entries are
 * we keeping, how much disk does that take, and when does the
 * oldest entry roll off?" The values come from
 * ``GET /api/audit-log/stats``, which reads the live audit file +
 * counts rotated archives next to it.
 *
 * Today the rotation policy is size-based (max_size_bytes per file
 * × keep_archives + 1), not time-based — so the banner reports the
 * configured cap and the current footprint, plus the oldest entry's
 * timestamp so operators can see roughly how far back queries reach.
 */
export function RetentionCard() {
  const stats = useAuditLogStats();

  if (stats.isLoading) {
    return (
      <Card data-testid="audit-retention-loading">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Archive aria-hidden className="size-4" />
            Retention
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-8 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (stats.error || !stats.data) {
    return (
      <Card data-testid="audit-retention-error" role="alert">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Archive aria-hidden className="size-4" />
            Retention
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't load audit-log retention stats:{" "}
            {stats.error ? (stats.error as Error).message : "no data"}
          </p>
        </CardContent>
      </Card>
    );
  }

  const data = stats.data;
  const usedPct = data.max_disk_bytes
    ? Math.min(100, Math.round((data.disk_bytes / data.max_disk_bytes) * 100))
    : 0;
  const usageTone =
    usedPct >= 90 ? "danger" : usedPct >= 70 ? "warning" : "success";

  return (
    <Card data-testid="audit-retention-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Archive aria-hidden className="size-4" />
          Retention
        </CardTitle>
        <CardDescription>
          Size-based rotation — every audit-log file caps at{" "}
          <strong>{formatBytes(data.max_size_bytes)}</strong> with{" "}
          <strong>{data.keep_archives}</strong> archives kept (max
          ~{formatBytes(data.max_disk_bytes)} total). Older entries roll
          off as the chain grows.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm md:grid-cols-4">
          <Stat label="Entries" value={data.entry_count.toLocaleString()} />
          <Stat label="Disk used" value={formatBytes(data.disk_bytes)} />
          <Stat
            label="Cap"
            value={formatBytes(data.max_disk_bytes)}
            hint={
              <Badge
                variant="outline"
                data-tone={usageTone}
                className="text-[10px]"
              >
                {usedPct}%
              </Badge>
            }
          />
          <Stat
            label="Archives"
            value={data.archive_count.toLocaleString()}
          />
          <Stat
            label="Oldest"
            value={data.oldest_ts ? formatTs(data.oldest_ts) : "—"}
            wide
          />
          <Stat
            label="Newest"
            value={data.newest_ts ? formatTs(data.newest_ts) : "—"}
            wide
          />
        </dl>
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  hint,
  wide = false,
}: {
  label: string;
  value: string;
  hint?: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className={wide ? "col-span-2 md:col-span-2" : ""}>
      <dt className="text-[11px] uppercase tracking-wide text-fg-faint">
        {label}
      </dt>
      <dd className="flex items-center gap-2 font-medium text-fg">
        <span className="tabular-nums">{value}</span>
        {hint}
      </dd>
    </div>
  );
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KiB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MiB`;
  return `${(n / 1024 ** 3).toFixed(2)} GiB`;
}

function formatTs(iso: string): string {
  // Audit log timestamps are ISO-8601 UTC. Render as the user's
  // locale for legibility but keep a tooltip-friendly raw form.
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}
