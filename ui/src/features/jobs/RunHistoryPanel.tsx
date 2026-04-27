import { useMemo, useState, type JSX } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DataTable } from "@/components/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import { useRuns, type RunRecordShape } from "./hooks";
import { RunDrawer } from "./RunDrawer";
import {
  anomalyTone,
  anomalyTooltip,
  buildRunHistoryColumns,
} from "./RunHistoryColumns";

/**
 * Phase-2 cross-job run history. Reads ``GET /api/runs`` and
 * surfaces the last N records as a sortable / filterable / column-
 * visibility-aware DataTable. Replaces the legacy ``<ul>``-of-
 * ``<li>`` list (v1.3.55-v1.3.65) with the same DataTable primitive
 * the JobHistoryPanel and 30+ other tables across the app use.
 *
 * Column definitions live in ``RunHistoryColumns.tsx`` so the panel
 * component stays under the react-components-over-300-lines ratchet.
 *
 * The whole row is clickable via DataTable's ``onRowClick`` —
 * opens the existing RunDrawer for full detail (same drawer
 * shared with LastRunPanel + CurrentlyRunningCard). The Logs
 * cell uses ``e.stopPropagation()`` so clicking it doesn't also
 * open the drawer.
 *
 * Per-row ``data-tone`` / ``data-status`` / ``data-has-parent``
 * / ``data-child-count`` attributes are preserved through
 * ``renderRowAttributes`` so existing CSS + tests keep working.
 */
export interface RunHistoryPanelProps {
  /** Default limit threaded into ``useRuns``. Overridden in tests. */
  defaultLimit?: number;
}

function getRunRowId(row: RunRecordShape): string {
  return row.run_id;
}

export function RunHistoryPanel({
  defaultLimit = 100,
}: RunHistoryPanelProps = {}): JSX.Element {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const runsQuery = useRuns({ limit: defaultLimit });

  const columns = useMemo(() => buildRunHistoryColumns(), []);

  return (
    <Card data-testid="run-history-panel">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">Recent runs</CardTitle>
      </CardHeader>
      <CardContent>
        {runsQuery.isLoading ? (
          <div data-testid="run-history-loading" className="flex flex-col gap-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : runsQuery.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="run-history-error"
          >
            Couldn&apos;t load run history:{" "}
            {(runsQuery.error as Error).message}
          </p>
        ) : (
          <DataTable
            columns={columns}
            data={runsQuery.data ?? []}
            getRowId={getRunRowId}
            testId="run-history"
            onRowClick={(row) => setSelectedRunId(row.run_id)}
            renderRowAttributes={(row) => ({
              "data-status": row.status,
              "data-job": row.job_name,
              "data-has-parent": row.parent_run_id ? "true" : "false",
              "data-child-count": String(row.child_run_ids.length),
              ...(anomalyTone(row.anomaly_score)
                ? { "data-tone": anomalyTone(row.anomaly_score) }
                : {}),
              ...(row.anomaly_score != null
                ? { title: anomalyTooltip(row.anomaly_score) }
                : {}),
            })}
            emptyState={
              // DataTable wraps the empty state in a ``<td
              // data-testid="run-history-empty">``, so we only
              // ship the *content* — adding our own testid here
              // would duplicate it and break getByTestId queries.
              (runsQuery.data?.length ?? 0) === 0
                ? "No recorded runs yet."
                : "No runs match the current filter."
            }
          />
        )}
      </CardContent>
      <RunDrawer
        runId={selectedRunId}
        onClose={() => setSelectedRunId(null)}
        onSelectRunId={(id) => setSelectedRunId(id)}
      />
    </Card>
  );
}
