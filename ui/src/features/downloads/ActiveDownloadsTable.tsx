import { useMemo } from "react";
import { Download } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { formatBytes } from "@/features/media-integrity/format";
import { flattenActive, useDownloads } from "./hooks";

interface Row {
  id: string;
  client: "qbittorrent" | "sabnzbd";
  name: string;
  progress: number;
  state: string;
  size: number;
  dlspeed: number;
}

function formatSpeed(bps: number): string {
  if (!Number.isFinite(bps) || bps <= 0) return "—";
  return `${formatBytes(bps)}/s`;
}

function clampProgress(p: unknown): number {
  if (typeof p !== "number" || !Number.isFinite(p)) return 0;
  // qBittorrent can report ratios (0..1) or percentages (0..100).
  if (p > 1.5) return Math.max(0, Math.min(100, p));
  return Math.max(0, Math.min(100, p * 100));
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-2">
      <div
        className="h-1.5 w-24 overflow-hidden rounded-full bg-bg-3"
        role="progressbar"
        aria-valuenow={Math.round(value)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full bg-accent"
          style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
        />
      </div>
      <span className="font-mono text-xs tabular-nums text-fg-muted">
        {value.toFixed(1)}%
      </span>
    </div>
  );
}

export function ActiveDownloadsTable() {
  const query = useDownloads();
  const rows = useMemo<Row[]>(() => {
    const flat = flattenActive(query.data);
    return flat.map(({ client, item }, i) => {
      const progress = clampProgress(item.progress);
      const name = typeof item.name === "string" ? item.name : `Item ${i + 1}`;
      return {
        id: `${client}-${i}-${name}`,
        client,
        name,
        progress,
        state: typeof item.state === "string" ? item.state : "",
        size: typeof item.size === "number" ? item.size : 0,
        dlspeed: typeof item.dlspeed === "number" ? item.dlspeed : 0,
      };
    });
  }, [query.data]);

  if (query.isLoading) {
    return (
      <div className="space-y-2" data-testid="active-downloads-loading">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (query.error) {
    return (
      <div
        role="alert"
        data-testid="active-downloads-error"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
      >
        <p className="font-medium">Failed to load downloads</p>
        <p className="mt-1 text-fg-muted">{query.error.message}</p>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Download}
        title="Nothing downloading"
        description="qBittorrent and SABnzbd queues are empty."
      />
    );
  }

  const columns: ResponsiveTableColumn<Row>[] = [
    {
      id: "name",
      header: "Item",
      cell: (row) => (
        <div className="flex flex-col">
          <span className="truncate font-medium text-fg" title={row.name}>
            {row.name}
          </span>
          <Badge variant="outline" className="self-start">
            {row.client}
          </Badge>
        </div>
      ),
    },
    {
      id: "progress",
      header: "Progress",
      cell: (row) => <ProgressBar value={row.progress} />,
    },
    {
      id: "speed",
      header: "Speed",
      cell: (row) => (
        <span className="font-mono tabular-nums text-fg">
          {formatSpeed(row.dlspeed)}
        </span>
      ),
    },
    {
      id: "size",
      header: "Size",
      cell: (row) => (
        <span className="font-mono tabular-nums text-fg-muted">
          {row.size > 0 ? formatBytes(row.size) : "—"}
        </span>
      ),
    },
    {
      id: "state",
      header: "State",
      cell: (row) =>
        row.state ? (
          <Badge variant={row.state === "seeding" ? "success" : "info"}>
            {row.state}
          </Badge>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
    },
  ];

  return (
    <Card className="p-0" data-testid="active-downloads">
      <ResponsiveTable
        rows={rows}
        rowKey={(r) => r.id}
        columns={columns}
        card={(row) => (
          <div className="flex flex-col gap-2">
            <div className="flex items-start justify-between gap-2">
              <span className="truncate font-medium text-fg" title={row.name}>
                {row.name}
              </span>
              <Badge variant="outline">{row.client}</Badge>
            </div>
            <ProgressBar value={row.progress} />
            <div className="flex items-center justify-between text-xs text-fg-muted">
              <span className="font-mono tabular-nums">
                {formatSpeed(row.dlspeed)}
              </span>
              <span className="font-mono tabular-nums">
                {row.size > 0 ? formatBytes(row.size) : "—"}
              </span>
              {row.state ? <Badge variant="info">{row.state}</Badge> : null}
            </div>
          </div>
        )}
      />
    </Card>
  );
}
