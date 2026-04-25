import { useMemo, useState } from "react";
import { Camera, Eye, GitCompare } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useSnapshots, useTakeSnapshot, type SnapshotEntry } from "./hooks";
import { SnapshotContentDrawer } from "./SnapshotContentDrawer";
import { SnapshotDiffDialog } from "./SnapshotDiffDialog";

function formatBytes(bytes: number | undefined): string {
  if (bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

function countServices(snap: SnapshotEntry): string {
  // The list endpoint doesn't include a per-snapshot service count,
  // so we surface a dash; the per-snapshot detail (drawer) includes
  // the full mapping. Keeping the column means we don't reshape the
  // table when richer data lands.
  const rec = snap as unknown as Record<string, unknown>;
  if (typeof rec.services_count === "number") return String(rec.services_count);
  if (typeof rec.configs === "number") return String(rec.configs);
  return "—";
}

interface SnapshotsTableProps {
  /** Test seam — the route uses the default. */
  initialSelected?: string[];
}

/**
 * Snapshots list with per-row View / Diff actions and a top-bar
 * "Take snapshot now" trigger. Diff selection is a tiny stateful
 * toggle: clicking "Diff" on a row toggles it in/out of a 2-slot
 * comparison set; once two are selected, the diff dialog opens.
 */
export function SnapshotsTable({ initialSelected = [] }: SnapshotsTableProps) {
  const snapshots = useSnapshots();
  const take = useTakeSnapshot();
  const [drawerFor, setDrawerFor] = useState<string | null>(null);
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffSelected, setDiffSelected] =
    useState<string[]>(initialSelected);

  const rows = useMemo<SnapshotEntry[]>(
    () => snapshots.data?.snapshots?.slice() ?? [],
    [snapshots.data],
  );

  const handleTake = () => {
    if (take.isPending) return;
    take.mutate(undefined, {
      onSuccess: (out) => {
        toast.success(`Snapshot created: ${out.file}`);
      },
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Snapshot failed";
        toast.error(msg);
      },
    });
  };

  const toggleDiff = (file: string) => {
    // Compute next state up front so we can decide whether to open the
    // dialog without relying on closure side-effects from the updater
    // (React queues updater functions and reduces them during the next
    // render, which would race the `if (shouldOpen) setDiffOpen(true)`
    // check that previously sat below the setter).
    const next = diffSelected.includes(file)
      ? diffSelected.filter((f) => f !== file)
      : [...diffSelected, file].slice(-2);
    setDiffSelected(next);
    if (next.length === 2) setDiffOpen(true);
  };

  const columns: ResponsiveTableColumn<SnapshotEntry>[] = [
    {
      id: "file",
      header: "Filename",
      cell: (row) => (
        <span className="font-mono text-xs text-fg" title={row.file}>
          {row.file}
        </span>
      ),
    },
    {
      id: "created",
      header: "Taken at",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">{row.created}</span>
      ),
    },
    {
      id: "size",
      header: "Size",
      cell: (row) => (
        <span className="font-mono tabular-nums text-fg-muted">
          {formatBytes(row.size)}
        </span>
      ),
    },
    {
      id: "services-count",
      header: "Services",
      cell: (row) => (
        <span className="font-mono tabular-nums text-fg-muted">
          {countServices(row)}
        </span>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      cell: (row) => (
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setDrawerFor(row.file)}
            data-testid={`snapshot-view-${row.file}`}
          >
            <Eye aria-hidden />
            View
          </Button>
          <Button
            variant={diffSelected.includes(row.file) ? "primary" : "ghost"}
            size="sm"
            onClick={() => toggleDiff(row.file)}
            data-testid={`snapshot-diff-${row.file}`}
          >
            <GitCompare aria-hidden />
            Diff
          </Button>
        </div>
      ),
    },
  ];

  return (
    <>
      <Card data-testid="snapshots-card">
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex flex-col gap-1.5">
            <CardTitle>Config snapshots</CardTitle>
            <CardDescription>
              {rows.length} snapshot{rows.length === 1 ? "" : "s"} retained.
              Up to 24 are kept; older ones are pruned automatically.
            </CardDescription>
          </div>
          <Button
            variant="primary"
            onClick={handleTake}
            disabled={take.isPending}
            loading={take.isPending}
            data-testid="snapshot-take"
          >
            <Camera aria-hidden />
            Take snapshot now
          </Button>
        </CardHeader>
        <CardContent>
          {snapshots.isLoading ? (
            <div className="space-y-2" data-testid="snapshots-loading">
              {[0, 1, 2, 3].map((i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : snapshots.error ? (
            <div
              role="alert"
              data-testid="snapshots-error"
              className="text-sm text-danger"
            >
              {snapshots.error.message}
            </div>
          ) : rows.length === 0 ? (
            <EmptyState
              icon={Camera}
              title="No snapshots yet"
              description="Take your first snapshot to capture the current config state. The controller redacts API keys automatically."
            />
          ) : (
            <ResponsiveTable
              rows={rows}
              rowKey={(r) => r.file}
              columns={columns}
              card={(row) => (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-3">
                    <span
                      className="truncate font-mono text-xs text-fg"
                      title={row.file}
                    >
                      {row.file}
                    </span>
                    <span className="font-mono tabular-nums text-xs text-fg-muted">
                      {formatBytes(row.size)}
                    </span>
                  </div>
                  <span className="text-xs tabular-nums text-fg-muted">
                    {row.created}
                  </span>
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setDrawerFor(row.file)}
                      data-testid={`snapshot-view-${row.file}-mobile`}
                    >
                      <Eye aria-hidden />
                      View
                    </Button>
                    <Button
                      variant={diffSelected.includes(row.file) ? "primary" : "ghost"}
                      size="sm"
                      onClick={() => toggleDiff(row.file)}
                      data-testid={`snapshot-diff-${row.file}-mobile`}
                    >
                      <GitCompare aria-hidden />
                      Diff
                    </Button>
                  </div>
                </div>
              )}
            />
          )}
        </CardContent>
      </Card>

      <SnapshotContentDrawer
        filename={drawerFor}
        onOpenChange={(open) => {
          if (!open) setDrawerFor(null);
        }}
      />

      <SnapshotDiffDialog
        open={diffOpen}
        onOpenChange={(open) => {
          setDiffOpen(open);
          if (!open) setDiffSelected([]);
        }}
        snapshots={rows}
        initialA={diffSelected[0]}
        initialB={diffSelected[1]}
      />
    </>
  );
}
