import { useMemo } from "react";
import { HardDrive } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { asArray } from "@/lib/coerce";
import { useMounts, type MountEntry } from "./hooks";

interface MountRow {
  id: string;
  path: string;
  fstype: string;
  size: number;
  used: number;
  available: number;
  hasUsage: boolean;
}

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "—";
  const i = Math.min(
    Math.floor(Math.log(n) / Math.log(1024)),
    UNITS.length - 1,
  );
  const v = n / 1024 ** i;
  const formatted = v < 10 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : Math.round(v);
  return `${formatted} ${UNITS[i]}`;
}

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function toRow(m: MountEntry, idx: number): MountRow {
  const path = m.path ?? m.mountpoint ?? m.device ?? `mount-${idx}`;
  const size = num(m.size);
  const used = num(m.used);
  // Compute available if it wasn't supplied (size - used).
  const available =
    typeof m.available === "number" && Number.isFinite(m.available)
      ? m.available
      : size > 0
        ? Math.max(0, size - used)
        : 0;
  return {
    id: `${path}-${idx}`,
    path,
    fstype: m.fstype ?? "",
    size,
    used,
    available,
    hasUsage: size > 0,
  };
}

function UsageBar({ row }: { row: MountRow }) {
  if (!row.hasUsage) return <span className="text-fg-faint">—</span>;
  const pct = Math.max(0, Math.min(100, (row.used / row.size) * 100));
  const tone =
    pct >= 90
      ? "bg-danger"
      : pct >= 75
        ? "bg-warning"
        : "bg-success";
  return (
    <div
      className="flex flex-col gap-1"
      data-testid={`mount-usage-${row.id}`}
    >
      <div
        className="relative h-1.5 w-full overflow-hidden rounded-full bg-bg-2"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={`absolute inset-y-0 left-0 ${tone}`}
          style={{ width: `${pct.toFixed(1)}%` }}
        />
      </div>
      <span className="font-mono text-xs tabular-nums text-fg-muted">
        {pct.toFixed(0)}%
      </span>
    </div>
  );
}

export function MountsCard() {
  const query = useMounts();

  const rows = useMemo<MountRow[]>(() => {
    const list = asArray<MountEntry>(query.data?.mounts);
    return list.map((m, i) => toRow(m, i));
  }, [query.data]);

  const columns: ResponsiveTableColumn<MountRow>[] = [
    {
      id: "path",
      header: "Path",
      cell: (row) => (
        <span className="font-mono text-xs text-fg">{row.path}</span>
      ),
    },
    {
      id: "fstype",
      header: "FS",
      cell: (row) => (
        <span className="font-mono text-xs text-fg-muted">
          {row.fstype || "—"}
        </span>
      ),
    },
    {
      id: "size",
      header: "Size",
      cell: (row) => (
        <span className="font-mono text-xs tabular-nums">
          {formatBytes(row.size)}
        </span>
      ),
    },
    {
      id: "used",
      header: "Used",
      cell: (row) => <UsageBar row={row} />,
    },
    {
      id: "available",
      header: "Available",
      cell: (row) => (
        <span className="font-mono text-xs tabular-nums">
          {formatBytes(row.available)}
        </span>
      ),
    },
  ];

  return (
    <Card data-testid="mounts-card">
      <CardHeader>
        <CardTitle>Mounts</CardTitle>
        <CardDescription>
          Filesystem mounts visible to the controller
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div className="flex flex-col gap-2" data-testid="mounts-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="mounts-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={HardDrive}
            title="No mounts detected"
            description="The controller didn't find any media-relevant filesystem mounts."
          />
        ) : (
          <ResponsiveTable
            rows={rows}
            rowKey={(r) => r.id}
            columns={columns}
            card={(row) => (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs text-fg">
                    {row.path}
                  </span>
                  <span className="shrink-0 font-mono text-xs text-fg-muted">
                    {row.fstype || "—"}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <span className="text-fg-muted">Size</span>
                  <span className="text-right font-mono tabular-nums">
                    {formatBytes(row.size)}
                  </span>
                  <span className="text-fg-muted">Available</span>
                  <span className="text-right font-mono tabular-nums">
                    {formatBytes(row.available)}
                  </span>
                </div>
                <UsageBar row={row} />
              </div>
            )}
          />
        )}
      </CardContent>
    </Card>
  );
}
