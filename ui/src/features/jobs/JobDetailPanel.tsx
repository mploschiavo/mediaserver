import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { History, Loader2, Play, ScrollText, Square } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/cn";
import { asArray } from "@/lib/coerce";
import { ApiError } from "@/api";
import {
  useCancelAction,
  useRunAction,
  type JobHistoryEntry,
  type JobMeta,
} from "./hooks";
import {
  epochToIso,
  formatAbsolute,
  formatElapsed,
  formatRelative,
  formatUntil,
  nextCronFire,
} from "./format";
import {
  JobDetailBreakdown,
  type JobDetailBreakdownRow,
} from "./JobDetailBreakdown";

interface JobDetailPanelProps {
  job: JobMeta;
  history: readonly JobHistoryEntry[];
  /**
   * Names that are *unmet* in the latest batch — i.e. anything in
   * `requires[]` whose latest history result is `skipped` or whose
   * dependency wasn't met.
   */
  unmet: ReadonlySet<string>;
  /** Called when the operator clicks "Show in tree" on a chip. */
  onReveal: (name: string) => void;
  /**
   * Full catalog so the panel can compute the inverse-dependency
   * ("Required by") chip list client-side.
   */
  catalog?: ReadonlyMap<string, JobMeta>;
  /**
   * Notification fired whenever the local `running` flag flips.
   * The parent uses this to drive the page-level "in-flight" banner
   * + tree badge. Optional — tests render the panel in isolation.
   */
  onRunningChange?: (running: boolean) => void;
}

type RunRow = JobDetailBreakdownRow;

function lastRunsForJob(
  name: string,
  history: readonly JobHistoryEntry[],
  cap: number = 10,
): RunRow[] {
  const out: RunRow[] = [];
  for (const entry of history) {
    const result = entry.jobs?.[name];
    if (!result) continue;
    out.push({
      ts: entry.ts,
      status: typeof result.status === "string" ? result.status : "—",
      elapsed: typeof result.elapsed === "number" ? result.elapsed : undefined,
      source: typeof entry.source === "string" ? entry.source : undefined,
    });
    if (out.length >= cap) break;
  }
  return out;
}

/**
 * Walk the FULL history (not just the visible 10) for the most recent
 * `ok` outcome. Returns `{ ts, foundAtIndex }` so callers can tell
 * whether it scanned to the bottom of the buffer.
 */
function lastSuccessfulRun(
  name: string,
  history: readonly JobHistoryEntry[],
): { ts: number; index: number } | null {
  for (let i = 0; i < history.length; i++) {
    const entry = history[i];
    if (!entry) continue;
    const result = entry.jobs?.[name];
    if (result?.status === "ok" && typeof entry.ts === "number") {
      return { ts: entry.ts, index: i };
    }
  }
  return null;
}

interface DepChipProps {
  name: string;
  unmet: boolean;
  onReveal: (name: string) => void;
  testIdPrefix?: string;
}

function DepChip({
  name,
  unmet,
  onReveal,
  testIdPrefix = "job-dep",
}: DepChipProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-mono text-xs",
        unmet
          ? "border-[color-mix(in_oklab,var(--color-warning)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-warning)_15%,transparent)] text-warning"
          : "border-border bg-bg-2 text-fg",
      )}
      data-testid={`${testIdPrefix}-chip-${name}`}
      data-unmet={unmet ? "true" : "false"}
    >
      {name}
      <button
        type="button"
        onClick={() => onReveal(name)}
        className="rounded text-[10px] uppercase tracking-wide text-fg-muted [@media(hover:hover)]:hover:text-fg"
        title="Show in tree"
        data-testid={`${testIdPrefix}-reveal-${name}`}
      >
        Show
      </button>
    </span>
  );
}

const RUN_NOW_TIMEOUT_MS = 60_000;

const SPARK_W = 120;
const SPARK_H = 24;
const SPARK_PAD = 2;

interface SparkPoint {
  ts: number | undefined;
  elapsed: number;
  status: string;
}

/**
 * Hand-rolled SVG sparkline of the last N runs' elapsed values.
 * Mirrors `HealthHistorySparkline.tsx`'s approach (no chart libs).
 * `error` runs render as red dots, `skipped` as amber, otherwise the
 * path is a thin muted line. Returns null when fewer than 2 points
 * exist (a 1-point line is meaningless visually).
 */
function LatencySparkline({ points }: { points: readonly SparkPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const finite = points.filter((p) => Number.isFinite(p.elapsed));
  if (finite.length < 2) return null;
  const max = Math.max(...finite.map((p) => p.elapsed), 0.0001);
  const min = Math.min(...finite.map((p) => p.elapsed));
  // We display oldest → newest left → right; the input is newest-first
  // (the runs table sort), so reverse for the sparkline.
  const reversed = [...points].reverse();
  const innerW = SPARK_W - 2 * SPARK_PAD;
  const innerH = SPARK_H - 2 * SPARK_PAD;
  const xStep =
    reversed.length === 1 ? 0 : innerW / (reversed.length - 1);
  const range = max - min || 1;
  const coords = reversed.map((p, i) => {
    const e = Number.isFinite(p.elapsed) ? p.elapsed : min;
    const x = SPARK_PAD + i * xStep;
    const ratio = (e - min) / range;
    const y = SPARK_PAD + (1 - Math.max(0, Math.min(1, ratio))) * innerH;
    return { x, y, point: p };
  });
  const pathD = coords
    .map(
      (c, i) => `${i === 0 ? "M" : "L"} ${c.x.toFixed(2)} ${c.y.toFixed(2)}`,
    )
    .join(" ");
  const hoverCoord = hover !== null ? coords[hover] : null;
  return (
    <span
      className="relative inline-block align-middle"
      style={{ width: SPARK_W, height: SPARK_H }}
      data-testid="job-detail-sparkline"
    >
      <svg
        width={SPARK_W}
        height={SPARK_H}
        viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
        role="img"
        aria-label="Per-run elapsed sparkline"
        onMouseLeave={() => setHover(null)}
      >
        <path
          d={pathD}
          fill="none"
          stroke="currentColor"
          className="text-fg-muted"
          strokeWidth={1.25}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {coords.map((c, i) => {
          const status = c.point.status;
          if (status === "error" || status === "errors" || status === "failed") {
            return (
              <circle
                key={`err-${i}`}
                cx={c.x}
                cy={c.y}
                r={1.8}
                fill="var(--color-danger)"
              />
            );
          }
          if (status === "skipped") {
            return (
              <circle
                key={`skp-${i}`}
                cx={c.x}
                cy={c.y}
                r={1.8}
                fill="var(--color-warning)"
              />
            );
          }
          return null;
        })}
        {hoverCoord ? (
          <circle
            cx={hoverCoord.x}
            cy={hoverCoord.y}
            r={2}
            fill="var(--color-accent)"
            stroke="var(--color-bg)"
            strokeWidth={1}
          />
        ) : null}
        {coords.map((c, i) => {
          const half =
            i === coords.length - 1
              ? SPARK_W - c.x
              : ((coords[i + 1]?.x ?? c.x) - c.x) / 2 + 1;
          const stripX = Math.max(0, c.x - half);
          const stripW =
            i === 0
              ? c.x + half
              : i === coords.length - 1
                ? half + 1
                : half * 2;
          return (
            <rect
              key={`hit-${i}`}
              x={stripX}
              y={0}
              width={Math.max(1, stripW)}
              height={SPARK_H}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
            />
          );
        })}
      </svg>
      {hoverCoord ? (
        <span
          role="tooltip"
          data-testid="job-detail-sparkline-tooltip"
          className="pointer-events-none absolute z-10 -translate-x-1/2 whitespace-nowrap rounded-md border border-border bg-bg-3 px-1.5 py-0.5 text-[10px] text-fg shadow-md"
          style={{
            left: hoverCoord.x,
            top: -4,
            transform: "translate(-50%, -100%)",
          }}
        >
          {formatAbsolute(hoverCoord.point.ts)} · {formatElapsed(hoverCoord.point.elapsed)}
        </span>
      ) : null}
    </span>
  );
}

/**
 * Detail card for a single selected job. Shows the metadata, the
 * `requires[]` / `after[]` dependency chip lists, the last-10-runs
 * table, and the Run / Cancel actions.
 *
 * "Run now" wires through `useRunAction(name)` — disabled when any
 * `requires[]` chip is in the unmet set. While the mutation is
 * in-flight (i.e. we have a `task_id` but no fresh history entry yet)
 * we set a local `running` flag and poll the parent's history feed via
 * its 5s `useJobs()` cycle. We auto-clear the flag when:
 *
 *   - a new history entry for this job appears, OR
 *   - the 60s `RUN_NOW_TIMEOUT_MS` watchdog fires.
 *
 * The watchdog matters because a job can fail to register a result
 * (e.g. the controller restarted) and we don't want the spinner to
 * spin forever. The timeout doesn't cancel the action — operators can
 * still click "Cancel" if they really want it stopped.
 */
export function JobDetailPanel({
  job,
  history,
  unmet,
  onReveal,
  catalog,
  onRunningChange,
}: JobDetailPanelProps) {
  const reduce = useReducedMotion();
  const runAction = useRunAction(job.name);
  const cancelAction = useCancelAction();

  const requires = asArray<string>(job.requires);
  const after = asArray<string>(job.after);
  const runs = useMemo(() => lastRunsForJob(job.name, history), [history, job.name]);
  // Sparkline uses up to 20 points so trends survive a couple of
  // skipped batches without collapsing.
  const sparkRuns = useMemo(
    () => lastRunsForJob(job.name, history, 20),
    [history, job.name],
  );

  // Track an in-flight task by recording the timestamp of the most
  // recent run we've already seen. When a *newer* timestamp lands in
  // history for this job, we know the task we kicked off has either
  // succeeded or failed and we clear the in-flight flag.
  const [running, setRunning] = useState(false);
  const watchdogRef = useRef<number | null>(null);
  const seenTsRef = useRef<number | null>(
    runs[0]?.ts !== undefined && Number.isFinite(runs[0].ts)
      ? (runs[0].ts as number)
      : null,
  );

  // Reset the watchdog + seen-ts when the user switches to a different
  // job (so a fresh detail panel doesn't inherit stale running state).
  useEffect(() => {
    setRunning(false);
    if (watchdogRef.current !== null) {
      window.clearTimeout(watchdogRef.current);
      watchdogRef.current = null;
    }
    seenTsRef.current =
      runs[0]?.ts !== undefined && Number.isFinite(runs[0].ts)
        ? (runs[0].ts as number)
        : null;
    // Intentionally only re-run when the selected job changes; runs[0]
    // shifts on every poll and is already handled by the effect below.

  }, [job.name]);

  // Detect a newer history entry → clear the spinner.
  useEffect(() => {
    if (!running) return;
    const latestTs =
      runs[0]?.ts !== undefined && Number.isFinite(runs[0].ts)
        ? (runs[0].ts as number)
        : null;
    if (latestTs === null) return;
    const seen = seenTsRef.current;
    if (seen === null || latestTs > seen) {
      setRunning(false);
      seenTsRef.current = latestTs;
      if (watchdogRef.current !== null) {
        window.clearTimeout(watchdogRef.current);
        watchdogRef.current = null;
      }
    }
  }, [runs, running]);

  // Cleanup any pending watchdog on unmount.
  useEffect(() => {
    return () => {
      if (watchdogRef.current !== null) {
        window.clearTimeout(watchdogRef.current);
        watchdogRef.current = null;
      }
    };
  }, []);

  // Bubble running flips up so the parent page can paint a banner +
  // tree badge. Fires only on changes; mount with `running=false`
  // does NOT bubble (parents start in the same state).
  const lastRunningRef = useRef<boolean>(false);
  useEffect(() => {
    if (lastRunningRef.current === running) return;
    lastRunningRef.current = running;
    if (onRunningChange) onRunningChange(running);
  }, [running, onRunningChange]);

  // Reset the parent flag when the user switches jobs.
  useEffect(() => {
    if (onRunningChange) onRunningChange(false);
    // Intentionally only on job-name change — running flips bubble
    // through the effect above.

  }, [job.name]);

  const reqUnmet = useMemo(
    () => requires.some((r) => unmet.has(r)),
    [requires, unmet],
  );

  // Compute the next-fire time from the cron schedule, when present.
  // The wrap is a useMemo so we don't re-compute Date.now() on every
  // render of the panel (parent polls every 5s).
  const next = useMemo(() => {
    if (typeof job.schedule !== "string" || job.schedule.length === 0) {
      return { fire: null, supported: false };
    }
    const fire = nextCronFire(job.schedule, new Date());
    return { fire, supported: true };
  }, [job.schedule]);

  // Inverse-dependency lookup: walk the catalog to find every job
  // that requires/afters the currently selected name. Memoised on
  // catalog identity so the parent's stable Map keeps this cheap.
  const requiredBy = useMemo(() => {
    if (!catalog) return [] as string[];
    const out: string[] = [];
    for (const meta of catalog.values()) {
      if (meta.name === job.name) continue;
      const r = asArray<string>(meta.requires);
      const a = asArray<string>(meta.after);
      if (r.includes(job.name) || a.includes(job.name)) {
        out.push(meta.name);
      }
    }
    out.sort((a, b) => a.localeCompare(b));
    return out;
  }, [catalog, job.name]);

  // Last successful run, scanning the full history (not the visible
  // 10). Used for the "Last green: 2h 14m ago" badge.
  const lastGreen = useMemo(
    () => lastSuccessfulRun(job.name, history),
    [history, job.name],
  );

  const handleRun = () => {
    setRunning(true);
    if (watchdogRef.current !== null) {
      window.clearTimeout(watchdogRef.current);
    }
    watchdogRef.current = window.setTimeout(() => {
      setRunning(false);
      watchdogRef.current = null;
    }, RUN_NOW_TIMEOUT_MS);
    runAction.mutate(undefined, {
      onError: () => {
        // Drop the spinner immediately on a hard error so the operator
        // can retry; the watchdog cleanup is harmless either way.
        setRunning(false);
        if (watchdogRef.current !== null) {
          window.clearTimeout(watchdogRef.current);
          watchdogRef.current = null;
        }
      },
    });
  };

  const handleCancel = () => {
    cancelAction.mutate(undefined, {
      onSettled: () => {
        // Either way, drop the local in-flight flag. The next poll
        // will surface the cancelled history entry naturally.
        setRunning(false);
      },
    });
  };

  const errorText = runAction.error
    ? runAction.error instanceof ApiError
      ? runAction.error.message
      : runAction.error.message
    : null;

  return (
    <motion.section
      className="flex flex-col gap-4"
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      data-testid="job-detail-panel"
      data-job-name={job.name}
    >
      <header className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-lg font-semibold tracking-tight text-fg">
            {job.label ?? job.name}
          </h2>
          {job.service ? (
            <Badge variant="info" data-testid="job-detail-service">
              {job.service}
            </Badge>
          ) : null}
          {job.non_blocking ? (
            <Badge variant="outline" data-testid="job-detail-non-blocking">
              non-blocking
            </Badge>
          ) : null}
          {typeof job.max_attempts === "number" ? (
            <Badge variant="outline" data-testid="job-detail-max-attempts">
              max {job.max_attempts}
            </Badge>
          ) : null}
        </div>
        <p className="font-mono text-xs text-fg-faint">{job.name}</p>
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-muted"
          data-testid="job-detail-schedule"
        >
          <span>
            <span className="uppercase tracking-wide text-fg-faint">
              Next run:
            </span>{" "}
            {next.supported ? (
              next.fire ? (
                <span title={formatAbsolute(Math.floor(next.fire.getTime() / 1000))}>
                  {formatUntil(next.fire)} ({next.fire.toLocaleTimeString()})
                </span>
              ) : (
                <span>—</span>
              )
            ) : (
              <span>Manual / dependency-driven</span>
            )}
          </span>
          {typeof job.schedule === "string" && job.schedule.length > 0 ? (
            <span className="font-mono text-fg-faint">{job.schedule}</span>
          ) : null}
        </div>
      </header>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Dependencies</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div>
            <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-fg-muted">
              Requires
            </p>
            {requires.length === 0 ? (
              <span className="text-sm text-fg-faint">— none —</span>
            ) : (
              <div
                className="flex flex-wrap gap-1.5"
                data-testid="job-detail-requires"
              >
                {requires.map((r) => (
                  <DepChip
                    key={r}
                    name={r}
                    unmet={unmet.has(r)}
                    onReveal={onReveal}
                  />
                ))}
              </div>
            )}
          </div>
          <div>
            <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-fg-muted">
              After
            </p>
            {after.length === 0 ? (
              <span className="text-sm text-fg-faint">— none —</span>
            ) : (
              <div
                className="flex flex-wrap gap-1.5"
                data-testid="job-detail-after"
              >
                {after.map((r) => (
                  <DepChip
                    key={r}
                    name={r}
                    unmet={unmet.has(r)}
                    onReveal={onReveal}
                  />
                ))}
              </div>
            )}
          </div>
          <div>
            <p className="mb-1.5 text-xs font-medium uppercase tracking-wide text-fg-muted">
              Required by
            </p>
            {requiredBy.length === 0 ? (
              <span
                className="text-sm text-fg-faint"
                data-testid="job-detail-required-by-empty"
              >
                — none —
              </span>
            ) : (
              <div
                className="flex flex-wrap gap-1.5"
                data-testid="job-detail-required-by"
              >
                {requiredBy.map((r) => (
                  <DepChip
                    key={r}
                    name={r}
                    unmet={false}
                    onReveal={onReveal}
                    testIdPrefix="job-required-by"
                  />
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-sm">Last 10 runs</CardTitle>
            <span
              className={cn(
                "inline-flex items-center rounded-md border px-1.5 py-0 text-[10px] uppercase tracking-wide",
                lastGreen
                  ? "border-success/40 bg-success/10 text-success"
                  : "border-border bg-bg-2 text-fg-muted",
              )}
              data-testid="job-detail-last-green"
              title={
                lastGreen
                  ? formatAbsolute(lastGreen.ts)
                  : "No successful run in recorded history"
              }
            >
              {lastGreen
                ? `Last green: ${formatRelative(epochToIso(lastGreen.ts))}`
                : "Never green in last 20 batches"}
            </span>
          </div>
        </CardHeader>
        <CardContent>
          {runs.length === 0 ? (
            <p className="text-sm text-fg-faint" data-testid="job-detail-no-runs">
              No recorded runs yet.
            </p>
          ) : (
            <div className="flex flex-col gap-3">
              {sparkRuns.length >= 2 ? (
                <div className="flex items-center gap-2 text-xs text-fg-muted">
                  <span className="uppercase tracking-wide text-fg-faint">
                    Latency trend
                  </span>
                  <LatencySparkline
                    points={sparkRuns.map((r) => ({
                      ts: r.ts,
                      elapsed:
                        typeof r.elapsed === "number" && Number.isFinite(r.elapsed)
                          ? r.elapsed
                          : 0,
                      status: r.status,
                    }))}
                  />
                </div>
              ) : null}
              <JobDetailBreakdown rows={runs} />
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          onClick={handleRun}
          disabled={running || reqUnmet || runAction.isPending}
          data-testid="job-detail-run-now"
        >
          {running || runAction.isPending ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <Play aria-hidden />
          )}
          Run now
        </Button>
        <Button
          variant="outline"
          onClick={handleCancel}
          disabled={!running || cancelAction.isPending}
          data-testid="job-detail-cancel"
        >
          <Square aria-hidden />
          Cancel
        </Button>
        <Button
          asChild
          variant="ghost"
          size="sm"
          data-testid="job-detail-view-logs"
        >
          <Link
            to="/logs"
            search={{ service: "controller", filter: job.name }}
          >
            <ScrollText aria-hidden />
            View logs
          </Link>
        </Button>
        <Button
          asChild
          variant="ghost"
          size="sm"
          data-testid="job-detail-audit-history"
        >
          <Link
            to="/audit-log"
            search={{ action: `job:${job.name}` }}
          >
            <History aria-hidden />
            Audit history
          </Link>
        </Button>
        {reqUnmet ? (
          <span
            className="text-xs text-warning"
            data-testid="job-detail-blocked-hint"
          >
            Required dependencies unmet — resolve before running.
          </span>
        ) : null}
        {errorText ? (
          <span
            role="alert"
            className="text-xs text-danger"
            data-testid="job-detail-run-error"
          >
            {errorText}
          </span>
        ) : null}
      </div>
    </motion.section>
  );
}
