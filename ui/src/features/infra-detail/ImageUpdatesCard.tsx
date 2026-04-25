import { useMemo } from "react";
import { CheckCircle2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
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
import { useImageUpdates, type ImageUpdateEntry } from "./hooks";

interface ImageUpdateRow {
  id: string;
  service: string;
  current: string;
  latest: string;
  available: string;
  /** epoch ms used for sort; 0 when unknown. */
  sortTs: number;
  hasUpdate: boolean;
}

function parseTs(value: string | undefined): number {
  if (!value) return 0;
  const t = Date.parse(value);
  return Number.isFinite(t) ? t : 0;
}

function formatRelative(ms: number, now: number = Date.now()): string {
  if (ms <= 0) return "—";
  const delta = Math.max(0, Math.floor((now - ms) / 1000));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function toRow(entry: ImageUpdateEntry, idx: number): ImageUpdateRow {
  const service = entry.service ?? entry.name ?? `image-${idx}`;
  const current = entry.current ?? entry.tag ?? "";
  const latest = entry.latest ?? "";
  const availableRaw = entry.available_at ?? entry.image_created ?? "";
  const sortTs = parseTs(availableRaw);
  const hasUpdate = Boolean(latest && latest !== current);
  return {
    id: `${service}-${idx}`,
    service,
    current,
    latest,
    available: sortTs > 0 ? formatRelative(sortTs) : availableRaw || "—",
    sortTs,
    hasUpdate,
  };
}

export function ImageUpdatesCard() {
  const query = useImageUpdates();

  const rows = useMemo<ImageUpdateRow[]>(() => {
    // Accept both v1.3.0 `updates[]` and current OpenAPI `images[]`.
    const updates = asArray<ImageUpdateEntry>(query.data?.updates);
    const images = asArray<ImageUpdateEntry>(query.data?.images);
    const list = updates.length > 0 ? updates : images;
    return list
      .map((e, i) => toRow(e, i))
      .sort((a, b) => b.sortTs - a.sortTs);
  }, [query.data]);

  const columns: ResponsiveTableColumn<ImageUpdateRow>[] = [
    {
      id: "service",
      header: "Service",
      cell: (row) => <span className="font-medium text-fg">{row.service}</span>,
    },
    {
      id: "current",
      header: "Current",
      cell: (row) => (
        <span className="font-mono text-xs text-fg-muted">
          {row.current || "—"}
        </span>
      ),
    },
    {
      id: "latest",
      header: "Latest",
      cell: (row) =>
        row.hasUpdate ? (
          <Badge variant="warning">{row.latest}</Badge>
        ) : (
          <span className="font-mono text-xs text-fg-muted">
            {row.latest || row.current || "—"}
          </span>
        ),
    },
    {
      id: "available",
      header: "Available",
      cell: (row) => (
        <span className="tabular-nums text-xs text-fg-muted">
          {row.available}
        </span>
      ),
    },
  ];

  return (
    <Card data-testid="image-updates-card">
      <CardHeader>
        <CardTitle>Image updates</CardTitle>
        <CardDescription>
          Container images with newer tags published upstream
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div
            className="flex flex-col gap-2"
            data-testid="image-updates-loading"
          >
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="image-updates-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="All images up to date"
            description="The controller hasn't detected any newer container tags."
          />
        ) : (
          <ResponsiveTable
            rows={rows}
            rowKey={(r) => r.id}
            columns={columns}
            card={(row) => (
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-fg">{row.service}</span>
                  {row.hasUpdate ? (
                    <Badge variant="warning">{row.latest}</Badge>
                  ) : (
                    <Badge variant="default">up to date</Badge>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <span className="text-fg-muted">Current</span>
                  <span className="text-right font-mono">
                    {row.current || "—"}
                  </span>
                  <span className="text-fg-muted">Available</span>
                  <span className="text-right tabular-nums">
                    {row.available}
                  </span>
                </div>
              </div>
            )}
          />
        )}
      </CardContent>
    </Card>
  );
}
