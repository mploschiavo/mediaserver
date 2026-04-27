import type { JSX, ReactNode } from "react";
import { Link } from "@tanstack/react-router";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Loader2,
  ScrollText,
  Sparkles,
  SkipForward,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { RunRecordShape } from "./hooks";
import { epochToIso, formatAbsolute, formatElapsed, formatRelative } from "./format";

type BadgeVariant =
  | "success"
  | "danger"
  | "warning"
  | "info"
  | "outline"
  | "default";

const STATUS_CONFIG: Record<
  string,
  { variant: BadgeVariant; icon: typeof CheckCircle2 }
> = {
  running: { variant: "info", icon: Loader2 },
  ok: { variant: "success", icon: CheckCircle2 },
  skipped: { variant: "warning", icon: SkipForward },
  error: { variant: "danger", icon: XCircle },
  cancelled: { variant: "outline", icon: AlertCircle },
  timeout: { variant: "danger", icon: AlertCircle },
};
const STATUS_FALLBACK = { variant: "default" as const, icon: Activity };

const TERMINAL_FAILURE_STATUSES = new Set(["error", "timeout", "cancelled"]);

export function StatusBadge({ status }: { status: string }): JSX.Element {
  const cfg = STATUS_CONFIG[status] ?? STATUS_FALLBACK;
  const Icon = cfg.icon;
  return (
    <Badge
      variant={cfg.variant}
      data-testid="run-drawer-status"
      data-status={status}
    >
      <Icon aria-hidden className="size-3" />
      {status}
    </Badge>
  );
}

export function SummaryPanel({ run }: { run: RunRecordShape }): JSX.Element {
  const isFailure = TERMINAL_FAILURE_STATUSES.has(run.status);
  return (
    <dl
      className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-2 text-sm"
      data-testid="run-drawer-summary"
    >
      <Row label="Status">
        <StatusBadge status={run.status} />
      </Row>
      {run.parent_run_id ? (
        <Row label="Parent">
          <div className="flex flex-col gap-0.5">
            {run.parent_job_name ? (
              <span
                className="font-medium text-fg"
                data-testid="run-drawer-parent-job-name"
              >
                {run.parent_job_name}
              </span>
            ) : null}
            <span
              className="font-mono text-[10px] text-fg-muted"
              data-testid="run-drawer-parent-id"
            >
              {run.parent_run_id}
            </span>
          </div>
        </Row>
      ) : null}
      {run.batch_id ? (
        <Row label="Batch">
          <span className="font-mono text-xs">{run.batch_id}</span>
        </Row>
      ) : null}
      <Row label="Triggered by">
        <span className="font-mono text-xs">
          {run.triggered_by}
          {run.actor ? ` · ${run.actor}` : ""}
        </span>
      </Row>
      <Row label="Started">
        <span title={formatAbsolute(run.started_at)}>
          {formatRelative(epochToIso(run.started_at))}
        </span>
      </Row>
      {run.completed_at ? (
        <Row label="Completed">
          <span title={formatAbsolute(run.completed_at)}>
            {formatRelative(epochToIso(run.completed_at))}
          </span>
        </Row>
      ) : null}
      <Row label="Elapsed">
        <span className="font-mono tabular-nums">
          {formatElapsed(run.elapsed)}
        </span>
      </Row>
      {run.attempts > 1 ? (
        <Row label="Attempts">
          <Badge variant="warning">×{run.attempts}</Badge>
        </Row>
      ) : null}
      {run.error ? (
        <Row label="Error">
          <pre
            className="whitespace-pre-wrap break-words rounded-md border border-danger/30 bg-danger/5 p-2 font-mono text-[11px] text-fg"
            data-testid="run-drawer-error-text"
          >
            {run.error}
          </pre>
        </Row>
      ) : null}
      {isFailure ? (
        <Row label="">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled
            title="AI explanations land in a follow-up release"
            data-testid="run-drawer-explain-stub"
          >
            <Sparkles aria-hidden className="size-3.5" />
            Explain failure
          </Button>
        </Row>
      ) : null}
    </dl>
  );
}

export function OutputPanel({ run }: { run: RunRecordShape }): JSX.Element {
  const anchor = run.log_anchor;
  const linkSearch = anchor
    ? {
        service: anchor.source,
        ...(anchor.action ? { action: anchor.action } : {}),
        since: anchor.since_iso,
        limit: 5_000,
      }
    : null;
  return (
    <div className="flex flex-col gap-3">
      {run.stdout_tail ? (
        <pre
          className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-bg-2 p-2 font-mono text-[11px] text-fg-muted"
          data-testid="run-drawer-stdout"
        >
          {run.stdout_tail}
        </pre>
      ) : (
        <p className="text-sm text-fg-faint" data-testid="run-drawer-no-output">
          No captured stdout for this run.
        </p>
      )}
      {linkSearch ? (
        <Button
          asChild
          variant="ghost"
          size="sm"
          className="self-start"
          data-testid="run-drawer-view-logs"
        >
          <Link to="/logs" search={linkSearch}>
            <ScrollText aria-hidden />
            View full logs for this run
          </Link>
        </Button>
      ) : null}
    </div>
  );
}

export function ChildrenPanel({
  children,
  onSelectRunId,
}: {
  children: readonly RunRecordShape[];
  onSelectRunId?: (runId: string) => void;
}): JSX.Element {
  if (children.length === 0) {
    return (
      <p className="text-sm text-fg-faint" data-testid="run-drawer-no-children">
        No child runs.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-1.5" data-testid="run-drawer-children">
      {children.map((c) => {
        const cfg = STATUS_CONFIG[c.status] ?? STATUS_FALLBACK;
        const Icon = cfg.icon;
        return (
          <li key={c.run_id}>
            <button
              type="button"
              onClick={() => onSelectRunId?.(c.run_id)}
              disabled={!onSelectRunId}
              className="flex w-full items-center gap-2 rounded-md border border-border bg-bg-1 px-2 py-1.5 text-left text-xs disabled:opacity-60 enabled:[@media(hover:hover)]:hover:bg-bg-2"
              data-testid={`run-drawer-child-${c.run_id}`}
            >
              <Badge variant={cfg.variant} className="gap-1">
                <Icon aria-hidden className="size-2.5" />
                {c.status}
              </Badge>
              <span className="flex-1 truncate font-medium">{c.job_name}</span>
              <span className="font-mono tabular-nums text-fg-muted">
                {formatElapsed(c.elapsed)}
              </span>
              {onSelectRunId ? (
                <ChevronRight aria-hidden className="size-3 text-fg-faint" />
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}): JSX.Element {
  return (
    <>
      <dt className="text-xs uppercase tracking-wide text-fg-muted">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  );
}
