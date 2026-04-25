import { useMemo } from "react";
import { Radar, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import {
  statsById,
  useDeleteIndexer,
  useIndexerStats,
  useIndexers,
  useToggleIndexer,
  type IndexerEntry,
  type IndexerStatEntry,
} from "./hooks";

interface IndexerRow {
  id: number;
  name: string;
  enable: boolean;
  protocol: string;
  stats: IndexerStatEntry | undefined;
}

function buildRows(
  indexers: readonly IndexerEntry[] | undefined,
  stats: ReturnType<typeof statsById>,
): IndexerRow[] {
  if (!indexers) return [];
  return indexers
    .filter((i) => typeof i.id === "number" && typeof i.name === "string")
    .map((i) => ({
      id: i.id,
      name: i.name,
      enable: i.enable !== false,
      protocol: typeof i.protocol === "string" ? i.protocol : "unknown",
      stats: stats.get(i.id),
    }));
}

interface ActionsProps {
  row: IndexerRow;
}

function StatsRow({ stats }: { stats: IndexerStatEntry | undefined }) {
  if (!stats) {
    return <span className="text-xs text-fg-faint">no data</span>;
  }
  const grabs = stats.numberOfGrabs ?? 0;
  const rss = stats.numberOfRssQueries ?? 0;
  const lastError = stats.lastError;
  return (
    <div className="flex flex-col text-xs text-fg-muted" data-testid="indexer-stats-row">
      <span className="font-mono tabular-nums">
        {grabs.toLocaleString()} grabs · {rss.toLocaleString()} RSS
      </span>
      {lastError ? (
        <span
          className="truncate text-danger"
          title={lastError}
          data-testid="indexer-last-error"
        >
          {lastError}
        </span>
      ) : null}
    </div>
  );
}

function Actions({ row }: ActionsProps) {
  const toggle = useToggleIndexer();
  const del = useDeleteIndexer();

  const onToggle = (next: boolean) => {
    toggle.mutate(
      { indexerId: row.id, enable: next },
      {
        onSuccess: () =>
          toast.success(`${row.name} ${next ? "enabled" : "disabled"}`),
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Toggle failed";
          toast.error(msg);
        },
      },
    );
  };

  const onDelete = () => {
    del.mutate(
      { indexerId: row.id },
      {
        onSuccess: () => toast.success(`${row.name} removed`),
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Delete failed";
          toast.error(msg);
        },
      },
    );
  };

  return (
    <div className="flex items-center gap-2">
      <Switch
        checked={row.enable}
        onCheckedChange={onToggle}
        disabled={toggle.isPending}
        aria-label={`${row.name} enabled`}
        data-testid={`indexer-toggle-${row.id}`}
      />
      <Button
        variant="ghost"
        size="sm"
        onClick={onDelete}
        loading={del.isPending}
        aria-label={`Delete ${row.name}`}
        data-testid={`indexer-delete-${row.id}`}
      >
        <Trash2 aria-hidden />
      </Button>
    </div>
  );
}

export function IndexersTable() {
  const indexers = useIndexers();
  const stats = useIndexerStats();
  const statsMap = useMemo(() => statsById(stats.data), [stats.data]);
  const rows = useMemo(
    () => buildRows(indexers.data?.indexers, statsMap),
    [indexers.data, statsMap],
  );

  if (indexers.isLoading) {
    return (
      <div className="space-y-2" data-testid="indexers-table-loading">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (indexers.error) {
    return (
      <div
        role="alert"
        data-testid="indexers-table-error"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
      >
        <p className="font-medium">Failed to load indexers</p>
        <p className="mt-1 text-fg-muted">{indexers.error.message}</p>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={Radar}
        title="No indexers configured"
        description="Configure Prowlarr to add torrent or usenet indexers."
      />
    );
  }

  const columns: ResponsiveTableColumn<IndexerRow>[] = [
    {
      id: "indexer",
      header: "Indexer",
      cell: (row) => (
        <div className="flex flex-col">
          <span className="font-medium text-fg">{row.name}</span>
          <Badge variant={row.protocol === "usenet" ? "info" : "outline"}>
            {row.protocol}
          </Badge>
        </div>
      ),
    },
    {
      id: "stats",
      header: "Performance",
      cell: (row) => <StatsRow stats={row.stats} />,
    },
    {
      id: "actions",
      header: "Actions",
      cell: (row) => <Actions row={row} />,
    },
  ];

  return (
    <Card className="p-0" data-testid="indexers-table">
      <ResponsiveTable
        rows={rows}
        rowKey={(r) => String(r.id)}
        columns={columns}
        card={(row) => (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-fg">{row.name}</span>
              <Badge variant={row.protocol === "usenet" ? "info" : "outline"}>
                {row.protocol}
              </Badge>
            </div>
            <StatsRow stats={row.stats} />
            <div className="flex items-center justify-end">
              <Actions row={row} />
            </div>
          </div>
        )}
      />
    </Card>
  );
}
