import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Loader2, Square } from "lucide-react";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { fetcher } from "@/api/client";

interface RunningJob {
  id: string;
  name: string;
  kind: "action" | "k8s_job";
  started_at?: number | null;
  elapsed_seconds?: number | null;
  triggered_by?: string;
  active_pods?: number;
}

interface RunningJobsResponse {
  running: RunningJob[];
  count: number;
}

interface ControllerStatusShape {
  initial_bootstrap_done?: boolean;
}

function isBootstrapJob(job: RunningJob): boolean {
  return job.name.toLowerCase() === "bootstrap";
}

/**
 * Persistent global indicator: "N jobs running" pill in the chrome
 * with a popover that lists every running action / job / CronJob
 * pod. Single source of truth (`GET /api/jobs/running`) so the
 * Guardrails / Media Integrity / Jobs page indicators all reflect
 * the same state.
 *
 * Renders nothing when there's nothing running — the banner is
 * silent during normal operation. Polling cadence: 10s by default
 * (matches the throttled action heartbeat).
 *
 * Click "Open jobs page" to drill into the full table; individual
 * rows link to their per-job detail.
 */
export function RunningJobsBanner() {
  const q = useQuery<RunningJobsResponse>({
    queryKey: ["jobs", "running"],
    queryFn: () =>
      fetcher<RunningJobsResponse>("api/jobs/running", {
        // Background advisory query — a 401 here shouldn't redirect the
        // whole SPA. The other route-level queries will catch the auth
        // expiry; the banner just hides itself.
        silenceAuthEvent: true,
      }),
    refetchInterval: 10_000,
    staleTime: 5_000,
  });

  // During the first-run window, the BootstrapProgressBanner hero
  // card is the canonical surface for the bootstrap job — surfacing
  // it here too is double coverage. After first-run completes
  // (`initial_bootstrap_done === true`), bootstrap re-runs are
  // operator-initiated and absolutely should appear here so the
  // operator knows the system is mid-reconfigure.
  const status = useQuery<ControllerStatusShape>({
    queryKey: ["controller", "status"],
    queryFn: () =>
      fetcher<ControllerStatusShape>("status", {
        silenceAuthEvent: true,
      }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const inFirstRunWindow = status.data?.initial_bootstrap_done === false;

  const items = (q.data?.running ?? []).filter((job) =>
    inFirstRunWindow ? !isBootstrapJob(job) : true,
  );
  if (items.length === 0) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="border-b border-info/30 bg-info/10 px-4 py-2 text-sm sm:px-6"
      data-testid="running-jobs-banner"
    >
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            className="flex items-center gap-2 rounded-md px-1 py-0.5 text-info hover:bg-info/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            data-testid="running-jobs-banner-trigger"
          >
            <Loader2 className="size-4 animate-spin" aria-hidden />
            <span className="font-medium">
              {items.length} {items.length === 1 ? "job" : "jobs"} running
            </span>
            <span className="text-xs text-info/80">
              ({items
                .slice(0, 2)
                .map((j) => j.name)
                .join(", ")}
              {items.length > 2 ? `, +${items.length - 2} more` : ""}
              )
            </span>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="w-80"
          data-testid="running-jobs-banner-popover"
        >
          <DropdownMenuLabel>
            Currently running ({items.length})
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <ul
            className="max-h-72 overflow-auto"
            data-testid="running-jobs-banner-list"
          >
            {items.map((j) => (
              <RunningRow key={`${j.kind}-${j.id}`} job={j} />
            ))}
          </ul>
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <Link
              to="/jobs"
              className="flex items-center gap-2"
              data-testid="running-jobs-banner-open-jobs"
            >
              <Activity className="size-3.5" /> Open Jobs page
            </Link>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function RunningRow({ job }: { job: RunningJob }) {
  const elapsed = formatElapsed(job.elapsed_seconds, job.started_at);
  const startedAt = job.started_at
    ? formatStartedAt(job.started_at)
    : "unknown start";
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const cancel = useMutation({
    mutationFn: () =>
      fetcher("api/actions/cancel", {
        method: "POST",
        body: JSON.stringify({}),
      }),
    onSuccess: () => {
      toast.success(
        `Cancellation requested for ${job.name}. The runner stops at the next safe checkpoint.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["jobs", "running"] });
      setConfirmOpen(false);
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : "Cancel failed";
      toast.error(msg);
    },
  });

  // Only the in-process controller action can be cancelled today —
  // K8s CronJob pods are managed by the scheduler. Surface a
  // disabled tooltip in that case rather than a broken button.
  const cancellable = job.kind === "action";

  return (
    <li
      className="flex flex-col gap-0.5 px-2 py-1.5 text-xs"
      data-testid={`running-jobs-banner-row-${job.id}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-fg" title={job.name}>
          {job.name}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-fg-faint tabular-nums">{elapsed}</span>
          {cancellable ? (
            <button
              type="button"
              onClick={(e) => {
                // The popover swallows clicks by default; stop here
                // so the dropdown doesn't close before the dialog
                // renders.
                e.preventDefault();
                e.stopPropagation();
                setConfirmOpen(true);
              }}
              className="rounded p-0.5 text-fg-muted hover:bg-danger/10 hover:text-danger focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={`Cancel ${job.name}`}
              data-testid={`running-jobs-banner-cancel-${job.id}`}
              disabled={cancel.isPending}
            >
              <Square className="size-3" aria-hidden />
            </button>
          ) : null}
        </div>
      </div>
      <div className="flex items-center justify-between text-fg-muted">
        <span>{kindLabel(job.kind)}</span>
        <span className="text-fg-faint">since {startedAt}</span>
      </div>
      {job.triggered_by || job.active_pods ? (
        <div className="flex items-center gap-2 text-fg-faint">
          {job.triggered_by ? <span>by {job.triggered_by}</span> : null}
          {job.active_pods ? <span>{job.active_pods} pod(s)</span> : null}
        </div>
      ) : null}
      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent data-testid="running-jobs-banner-cancel-dialog">
          <DialogHeader>
            <DialogTitle>Cancel {job.name}?</DialogTitle>
            <DialogDescription>
              The job will stop at the next safe checkpoint — partial
              work may need cleanup. Safe to re-run from the Jobs page
              once the cancellation completes.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => setConfirmOpen(false)}
            >
              Keep running
            </Button>
            <Button
              type="button"
              variant="danger"
              loading={cancel.isPending}
              onClick={() => cancel.mutate()}
              data-testid="running-jobs-banner-cancel-confirm"
            >
              Cancel job
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </li>
  );
}

function formatStartedAt(epoch: number): string {
  const start = new Date(epoch * 1000);
  const now = new Date();
  const time = start.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (start.toDateString() === now.toDateString()) return time;
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (start.toDateString() === yesterday.toDateString()) {
    return `yesterday ${time}`;
  }
  if (start.getFullYear() === now.getFullYear()) {
    return (
      start.toLocaleDateString([], { month: "short", day: "numeric" }) +
      ` ${time}`
    );
  }
  return (
    start.toLocaleDateString([], {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }) + ` ${time}`
  );
}

function kindLabel(kind: RunningJob["kind"]): string {
  return kind === "action" ? "controller action" : "K8s job";
}

function formatElapsed(
  seconds?: number | null,
  startedAt?: number | null,
): string {
  let s = seconds ?? null;
  if (s === null && startedAt) s = (Date.now() / 1000) - startedAt;
  if (s === null || s === undefined) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s / 60).toFixed(1)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}
