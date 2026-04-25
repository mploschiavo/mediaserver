import { useMemo } from "react";
import { History } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { formatRelative } from "@/features/media-integrity/format";
import { flattenHistory, useDownloadHistory } from "./hooks";

interface Row {
  id: string;
  service: string;
  title: string;
  event: string;
  date: string;
}

function eventVariant(
  event: string,
): "default" | "success" | "warning" | "danger" | "info" | "outline" {
  const e = event.toLowerCase();
  if (e.includes("import")) return "success";
  if (e.includes("grab")) return "info";
  if (e.includes("fail") || e.includes("error")) return "danger";
  if (e.includes("delete")) return "warning";
  return "default";
}

export function DownloadHistoryTable() {
  const query = useDownloadHistory();
  const rows = useMemo<Row[]>(() => {
    const flat = flattenHistory(query.data);
    return flat.map(({ service, entry }, i) => ({
      id: `${service}-${i}`,
      service,
      title: typeof entry.title === "string" ? entry.title : "(no title)",
      event: typeof entry.event === "string" ? entry.event : "",
      date: typeof entry.date === "string" ? entry.date : "",
    }));
  }, [query.data]);

  if (query.isLoading) {
    return (
      <div className="space-y-2" data-testid="download-history-loading">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (query.error) {
    return (
      <div
        role="alert"
        data-testid="download-history-error"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
      >
        <p className="font-medium">Failed to load history</p>
        <p className="mt-1 text-fg-muted">{query.error.message}</p>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="No history yet"
        description="The Servarr apps haven't recorded any download events recently."
      />
    );
  }

  const columns: ResponsiveTableColumn<Row>[] = [
    {
      id: "title",
      header: "Title",
      cell: (row) => (
        <span className="truncate font-medium text-fg" title={row.title}>
          {row.title}
        </span>
      ),
    },
    {
      id: "service",
      header: "Service",
      cell: (row) => <Badge variant="outline">{row.service}</Badge>,
    },
    {
      id: "event",
      header: "Event",
      cell: (row) =>
        row.event ? (
          <Badge variant={eventVariant(row.event)}>{row.event}</Badge>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
    },
    {
      id: "date",
      header: "When",
      cell: (row) => (
        <span className="font-mono text-xs tabular-nums text-fg-muted">
          {row.date ? formatRelative(row.date) : "—"}
        </span>
      ),
    },
  ];

  return (
    <Card className="p-0" data-testid="download-history">
      <ResponsiveTable
        rows={rows}
        rowKey={(r) => r.id}
        columns={columns}
        card={(row) => (
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-medium text-fg" title={row.title}>
                {row.title}
              </span>
              <Badge variant="outline">{row.service}</Badge>
            </div>
            <div className="flex items-center justify-between text-xs text-fg-muted">
              {row.event ? (
                <Badge variant={eventVariant(row.event)}>{row.event}</Badge>
              ) : (
                <span className="text-fg-faint">—</span>
              )}
              <span className="font-mono tabular-nums">
                {row.date ? formatRelative(row.date) : "—"}
              </span>
            </div>
          </div>
        )}
      />
    </Card>
  );
}
