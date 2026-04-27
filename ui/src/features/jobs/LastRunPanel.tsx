import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Clock,
  Loader2,
  ScrollText,
  SkipForward,
  XCircle,
} from "lucide-react";
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
import { cn } from "@/lib/cn";
import { useLatestRunForJob, useRun, type RunRecordShape } from "./hooks";
import { epochToIso } from "./format";
import { RunDrawer } from "./RunDrawer";
import { formatAbsolute, formatElapsed, formatRelative } from "./format";
import { RunGanttChart } from "./RunGanttChart";

/**
 * "Last run" detail panel, rendered above the Last-10-runs table
 * inside JobDetailPanel. Reads ``GET /api/runs/latest/<job>`` and
 * surfaces:
 *
 *   * Run-id (ULID, copy-friendly), status chip, when, elapsed
 *   * Triggered-by, actor (when set)
 *   * Error text (collapsible details if long)
 *   * stdout tail (collapsible details if present)
 *   * Child-runs list — clickable to drill into sub-run records
 *   * Deep-link to the Logs page filtered to this run's window
 *
 * Polling is at 2s while the run is in flight, 30s after it
 * settles. The 2s tail keeps the running indicator + elapsed
 * counter feeling live without hammering the controller.
 */
export function LastRunPanel({ jobName }: { jobName: string }) {
  const latest = useLatestRunForJob(jobName, {
    refetchInterval: 2_000,
  });

  if (latest.isLoading) {
    return (
      <Card data-testid="last-run-panel-loading">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Last run</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-20 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (latest.error) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Last run</CardTitle>
        </CardHeader>
        <CardContent>
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="last-run-panel-error"
          >
            Couldn't load run history: {(latest.error as Error).message}
          </p>
        </CardContent>
      </Card>
    );
  }

  const run = latest.data;
  if (!run) {
    return (
      <Card data-testid="last-run-panel-empty">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Last run</CardTitle>
          <CardDescription>
            No recorded run for {jobName} yet. Trigger one with "Run
            now" — the run shows up here as soon as the framework
            captures its start.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return <RunSummaryCard run={run} />;
}

function RunSummaryCard({ run }: { run: RunRecordShape }) {
  const isRunning = run.status === "running";
  const liveElapsed = useMemo(() => {
    if (!isRunning) return run.elapsed;
    return Math.max(0, Date.now() / 1000 - run.started_at);
  }, [isRunning, run.elapsed, run.started_at]);

  return (
    <Card
      data-testid="last-run-panel"
      data-status={run.status}
      data-run-id={run.run_id}
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-sm">Last run</CardTitle>
          <RunStatusBadge status={run.status} />
          <Badge variant="outline" className="font-mono text-[10px]">
            {run.triggered_by}
            {run.actor ? ` · ${run.actor}` : null}
          </Badge>
          {run.attempts > 1 ? (
            <Badge
              variant="warning"
              data-testid="last-run-attempts"
            >
              ×{run.attempts}
            </Badge>
          ) : null}
        </div>
        <CardDescription className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          <span title={formatAbsolute(run.started_at)}>
            <Clock aria-hidden className="mr-1 inline-block size-3" />
            {formatRelative(epochToIso(run.started_at))}
          </span>
          <span className="font-mono tabular-nums">
            {formatElapsed(liveElapsed)}
            {isRunning ? " (still running)" : ""}
          </span>
          <span
            className="font-mono text-[10px] text-fg-faint"
            data-testid="last-run-id"
          >
            {run.run_id}
          </span>
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {run.error ? (
          <details
            className="rounded-md border border-danger/30 bg-danger/5 p-2 text-xs"
            data-testid="last-run-error"
          >
            <summary className="cursor-pointer select-none font-medium text-danger">
              Error — click to expand
            </summary>
            <pre
              className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-fg"
              data-testid="last-run-error-text"
            >
              {run.error}
            </pre>
          </details>
        ) : null}

        {run.stdout_tail ? (
          <details
            className="rounded-md border border-border bg-bg-2 p-2 text-xs"
            data-testid="last-run-stdout"
          >
            <summary className="cursor-pointer select-none font-medium text-fg-muted">
              Output tail (last {run.stdout_tail.length} chars)
            </summary>
            <pre
              className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] text-fg-muted"
              data-testid="last-run-stdout-text"
            >
              {run.stdout_tail}
            </pre>
          </details>
        ) : null}

        <ChildRunsList run={run} />

        <div className="flex flex-wrap gap-2">
          {run.log_anchor ? (
            <Button
              asChild
              variant="ghost"
              size="sm"
              data-testid="last-run-view-logs"
            >
              <Link
                to="/logs"
                search={{
                  service: run.log_anchor.source,
                  ...(run.log_anchor.action
                    ? { action: run.log_anchor.action }
                    : {}),
                  since: run.log_anchor.since_iso,
                  limit: 5000,
                }}
              >
                <ScrollText aria-hidden />
                View logs for this run
              </Link>
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function ChildRunsList({ run }: { run: RunRecordShape }) {
  // Pull the full record (with inlined children) only when the
  // summary indicates there are children. Avoids a second request
  // for the (common) leaf-run case.
  const detail = useRun(run.child_run_ids.length > 0 ? run.run_id : null, {
    refetchInterval: run.status === "running" ? 2_000 : 30_000,
  });
  const [drawerRunId, setDrawerRunId] = useState<string | null>(null);
  if (run.child_run_ids.length === 0) return null;
  if (detail.isLoading) {
    return (
      <div data-testid="last-run-children-loading">
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }
  const children = detail.data?.children ?? [];
  if (children.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5" data-testid="last-run-children">
      <p className="text-xs font-medium uppercase tracking-wide text-fg-muted">
        Child runs ({children.length})
      </p>
      <RunGanttChart parent={run} children={children} />
      <ul className="flex flex-col gap-1">
        {children.map((c) => (
          <li
            key={c.run_id}
            data-testid={`last-run-child-${c.run_id}`}
            data-status={c.status}
          >
            <button
              type="button"
              onClick={() => setDrawerRunId(c.run_id)}
              className="flex w-full items-center gap-2 rounded-md border border-border bg-bg-1 px-2 py-1.5 text-left text-xs [@media(hover:hover)]:hover:bg-bg-2"
              data-testid={`last-run-child-button-${c.run_id}`}
            >
              <RunStatusBadge status={c.status} compact />
              <span className="flex-1 truncate font-medium text-fg">
                {c.job_name}
              </span>
              <span className="font-mono tabular-nums text-fg-muted">
                {formatElapsed(c.elapsed)}
              </span>
              <ChevronRight
                aria-hidden
                className="size-3 text-fg-faint"
              />
            </button>
          </li>
        ))}
      </ul>
      <RunDrawer
        runId={drawerRunId}
        onClose={() => setDrawerRunId(null)}
        onSelectRunId={(id) => setDrawerRunId(id)}
      />
    </div>
  );
}

function RunStatusBadge({
  status,
  compact = false,
}: {
  status: string;
  compact?: boolean;
}) {
  const cfg =
    STATUS_CONFIG[status] ?? STATUS_CONFIG.unknown ?? STATUS_DEFAULT;
  const Icon = cfg.icon;
  return (
    <Badge
      variant={cfg.variant}
      className={cn(
        "inline-flex items-center gap-1",
        compact ? "px-1.5 py-0 text-[10px]" : undefined,
      )}
      data-testid={`run-status-${status}`}
    >
      <Icon aria-hidden className={compact ? "size-2.5" : "size-3"} />
      {status}
    </Badge>
  );
}

const STATUS_DEFAULT = {
  variant: "default" as const,
  icon: Activity,
};
const STATUS_CONFIG: Record<
  string,
  {
    variant: "success" | "danger" | "warning" | "info" | "outline" | "default";
    icon: typeof CheckCircle2;
  }
> = {
  running: { variant: "info", icon: Loader2 },
  ok: { variant: "success", icon: CheckCircle2 },
  skipped: { variant: "warning", icon: SkipForward },
  error: { variant: "danger", icon: XCircle },
  cancelled: { variant: "outline", icon: AlertCircle },
  timeout: { variant: "danger", icon: AlertCircle },
  unknown: { variant: "default", icon: Activity },
};

