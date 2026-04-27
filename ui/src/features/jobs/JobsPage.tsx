import { useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  CalendarClock,
  CheckCircle2,
  Clock,
  AlertTriangle,
  MinusCircle,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { asArray } from "@/lib/coerce";
import { CurrentlyRunningCard } from "./CurrentlyRunningCard";
import { SchedulesCard } from "./SchedulesCard";
import { JobsTreeView } from "./JobsTreeView";
import { JobDetailPanel } from "./JobDetailPanel";
import { JobHistoryPanel } from "./JobHistoryPanel";
import { JobsRuntimeChart } from "./JobsRuntimeChart";
import { RunHistoryPanel } from "./RunHistoryPanel";
import {
  useJobs,
  type JobHistoryEntry,
  type JobMeta,
  type JobTreeNode,
  type JobsResponse,
} from "./hooks";
import {
  epochToIso,
  formatAbsolute,
  formatElapsed,
  formatRelative,
  formatUntil,
  nextCronFire,
} from "./format";

interface JobsPageProps {
  /** Test/Storybook escape hatch — bypass the hook entirely. */
  data?: JobsResponse;
  loading?: boolean;
  error?: Error | null;
}

/**
 * Compute the set of dependency names that are *unmet* in the latest
 * batch. The controller marks these as `skipped` in the per-job
 * results, so we promote any name with `status === "skipped"` into
 * the unmet set.
 *
 * The set is keyed by job name only — the dependency tokens in
 * `requires[]` are sometimes capability names (e.g.
 * `media_server_api_key`) rather than job names. Those obviously
 * won't be in the per-job map; we still surface them as "unmet"
 * when no job by that name produced an `ok` outcome, since the
 * controller's gate refuses to run jobs whose tokens aren't met.
 *
 * For simplicity we treat any required token without an `ok` result
 * in the latest batch as unmet. Conservative — false positives
 * (operator sees a "blocked" hint) are cheaper than false negatives
 * (operator clicks Run, controller refuses).
 */
function buildUnmetSet(latest: JobHistoryEntry | undefined): Set<string> {
  const out = new Set<string>();
  if (!latest?.jobs) return out;
  for (const [name, value] of Object.entries(latest.jobs)) {
    const status = (value as { status?: unknown }).status;
    if (status === "skipped") out.add(name);
    if (status === "error" || status === "errors" || status === "failed") {
      out.add(name);
    }
  }
  return out;
}

type MetricTone = "ok" | "warn" | "err" | "muted";

const METRIC_TONE_FG: Record<MetricTone, string> = {
  ok: "text-success",
  warn: "text-warning",
  err: "text-danger",
  muted: "text-fg-muted",
};

function MetricCard({
  icon: Icon,
  label,
  value,
  tone,
  testId,
  hint,
}: {
  icon: typeof CheckCircle2;
  label: string;
  value: string;
  tone: MetricTone;
  testId: string;
  hint?: string;
}) {
  return (
    <div
      className="flex items-center gap-3 rounded-md border border-border bg-bg-1/60 p-3"
      data-testid={testId}
      title={hint}
    >
      <div
        className={`flex size-9 shrink-0 items-center justify-center rounded-md bg-bg-2 ${METRIC_TONE_FG[tone]}`}
      >
        <Icon className="size-4" aria-hidden />
      </div>
      <div className="flex min-w-0 flex-col">
        <span className="text-xs uppercase tracking-wide text-fg-muted">
          {label}
        </span>
        <span className="truncate font-mono text-sm tabular-nums text-fg">
          {value}
        </span>
      </div>
    </div>
  );
}

/** Earliest upcoming cron fire across the catalog, or null if none. */
function earliestNextFire(
  jobs: readonly JobMeta[],
  now: Date,
): { name: string; fire: Date } | null {
  let best: { name: string; fire: Date } | null = null;
  for (const j of jobs) {
    if (typeof j.schedule !== "string" || j.schedule.length === 0) continue;
    const fire = nextCronFire(j.schedule, now);
    if (!fire) continue;
    if (!best || fire.getTime() < best.fire.getTime()) {
      best = { name: j.name, fire };
    }
  }
  return best;
}

/**
 * Walk the tree once and return a name → JobTreeNode index. Used to
 * synthesize a `JobMeta` for parent nodes (bootstrap, configure-*)
 * that exist in the hierarchy but aren't in the flat `jobs[]`
 * catalog (which only contains contract-discovered leaves). Without
 * this lookup, selecting a parent node leaves the detail panel
 * unmounted and the operator can't trigger the parent — even though
 * `POST /api/actions/<parent>` is a valid call for the registered
 * parents (bootstrap / configure-media-server / aliases).
 */
function buildTreeIndex(
  tree: readonly JobTreeNode[],
): ReadonlyMap<string, JobTreeNode> {
  const out = new Map<string, JobTreeNode>();
  const walk = (node: JobTreeNode) => {
    out.set(node.name, node);
    for (const child of asArray<JobTreeNode>(node.sub_jobs)) walk(child);
  };
  for (const root of tree) walk(root);
  return out;
}

/** Per-service catalog count, sorted by descending count. */
function buildServiceCounts(
  jobs: readonly JobMeta[],
): readonly { service: string; count: number }[] {
  const m = new Map<string, number>();
  for (const j of jobs) {
    const svc = j.service ?? "—";
    m.set(svc, (m.get(svc) ?? 0) + 1);
  }
  const out = Array.from(m.entries()).map(([service, count]) => ({
    service,
    count,
  }));
  out.sort((a, b) => b.count - a.count || a.service.localeCompare(b.service));
  return out;
}

/**
 * Two-pane operator surface for the controller's job catalog. The
 * left pane is a recursive hierarchy tree; the right pane shows
 * the selected job's detail (or, with no selection, the recent
 * batch history table).
 *
 * Mobile collapses to a single column with the tree on top. The
 * route file owns the outer `max-w-6xl` page-shell + PageHeader +
 * entrance animation; this component only paints the in-column
 * composition.
 */
export function JobsPage({
  data: dataProp,
  loading: loadingProp,
  error: errorProp,
}: JobsPageProps = {}) {
  const reduce = useReducedMotion();
  const live = useJobs();

  const data = dataProp ?? live.data;
  const loading = loadingProp ?? live.isLoading;
  const error = errorProp ?? (live.error as Error | null);

  // Coerce defensively for every list field, in case a controller
  // build hands us a non-array (legacy nightly builds occasionally
  // emitted `{}` for empty payloads).
  const jobs = asArray<JobMeta>(data?.jobs);
  const tree = asArray<JobTreeNode>(data?.tree);
  const history = asArray<JobHistoryEntry>(data?.history);

  const catalog = useMemo(() => {
    const m = new Map<string, JobMeta>();
    for (const j of jobs) {
      if (j && typeof j.name === "string") m.set(j.name, j);
    }
    return m;
  }, [jobs]);

  const latest = history[0];
  const unmet = useMemo(() => buildUnmetSet(latest), [latest]);

  // Index every node in the tree so we can resolve parents (bootstrap,
  // configure-*) that aren't in the flat catalog. Memoised on tree
  // identity — the 5s poll only mints a new array when the controller
  // emits a new tree.
  const treeIndex = useMemo(() => buildTreeIndex(tree), [tree]);

  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ name: string; nonce: number } | null>(
    null,
  );
  // Resolve the selected name to a JobMeta. Catalog wins when present;
  // otherwise synthesize a minimal meta from the tree node so parent
  // jobs (which carry no contract entry) still render the detail panel
  // — including the Run / Cancel buttons. The controller accepts
  // `POST /api/actions/<parent>` for registered parents (bootstrap,
  // configure-media-server, aliases) and 404s otherwise; we surface
  // that error inline rather than hiding the button.
  const selected = useMemo<JobMeta | null>(() => {
    if (!selectedName) return null;
    const fromCatalog = catalog.get(selectedName);
    if (fromCatalog) return fromCatalog;
    const node = treeIndex.get(selectedName);
    if (!node) return null;
    return {
      name: node.name,
      requires: asArray<string>(node.requires),
    };
  }, [selectedName, catalog, treeIndex]);

  // Track the in-flight action name across the page. JobDetailPanel
  // owns the `useRunAction` hook (it's the only place where Run/Cancel
  // can be triggered today); it surfaces a `running` callback so the
  // header banner + tree badge can pulse on the affected leaf.
  const [inFlightName, setInFlightName] = useState<string | null>(null);

  // Earliest upcoming cron fire across the catalog. Computed once
  // per render; the catalog only refreshes on the 5s poll so this
  // is cheap.
  const nextFire = useMemo(() => earliestNextFire(jobs, new Date()), [jobs]);

  // Per-service coverage (used by the catalog metric card hover and
  // the "by service" mini-list). Memoised on the jobs array.
  const serviceCounts = useMemo(() => buildServiceCounts(jobs), [jobs]);

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
        data-testid="jobs-page-error"
      >
        <p className="font-medium">Failed to load jobs</p>
        <p className="mt-1 text-fg-muted">{error.message}</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <CurrentlyRunningCard />
      <JobsRuntimeChart />
      {inFlightName ? (
        <motion.div
          className="flex items-center gap-2 rounded-md border border-accent/40 bg-accent/10 px-3 py-2 text-sm text-accent"
          data-testid="jobs-inflight-banner"
          role="status"
          initial={reduce ? false : { opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
        >
          <motion.span
            className="size-2 shrink-0 rounded-full bg-accent"
            aria-hidden
            animate={reduce ? undefined : { opacity: [0.4, 1, 0.4] }}
            transition={
              reduce
                ? undefined
                : { duration: 1.2, repeat: Infinity, ease: "easeInOut" }
            }
          />
          <span>
            Running{" "}
            <span className="font-mono">{inFlightName}</span>
            …
          </span>
        </motion.div>
      ) : null}
      <Card data-testid="jobs-batch-summary">
        <CardContent className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-5">
          {loading && !latest ? (
            <>
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-14 w-full" />
              ))}
            </>
          ) : (
            <>
              <MetricCard
                icon={Clock}
                label="Last run"
                value={
                  latest?.ts
                    ? formatRelative(epochToIso(latest.ts))
                    : "—"
                }
                tone="muted"
                testId="jobs-summary-last-run"
              />
              <MetricCard
                icon={CheckCircle2}
                label="Ok / Skipped / Errors"
                value={`${latest?.ok ?? 0} / ${latest?.skipped ?? 0} / ${latest?.errors ?? 0}`}
                tone={
                  (latest?.errors ?? 0) > 0
                    ? "err"
                    : (latest?.skipped ?? 0) > 0
                      ? "warn"
                      : "ok"
                }
                testId="jobs-summary-counts"
              />
              <MetricCard
                icon={AlertTriangle}
                label="Elapsed"
                value={formatElapsed(latest?.elapsed)}
                tone="muted"
                testId="jobs-summary-elapsed"
              />
              <MetricCard
                icon={MinusCircle}
                label="Catalog"
                value={`${data?.count ?? jobs.length} jobs`}
                tone="muted"
                testId="jobs-summary-count"
                hint={
                  serviceCounts.length > 0
                    ? `By service: ${serviceCounts.map((s) => `${s.service} ${s.count}`).join(", ")}`
                    : undefined
                }
              />
              <MetricCard
                icon={CalendarClock}
                label="Next scheduled run"
                value={
                  nextFire
                    ? `${formatUntil(nextFire.fire)} (${nextFire.name})`
                    : "—"
                }
                tone="muted"
                testId="jobs-summary-next-run"
                hint={
                  nextFire
                    ? formatAbsolute(Math.floor(nextFire.fire.getTime() / 1000))
                    : "No scheduled jobs in catalog"
                }
              />
            </>
          )}
          {latest?.ts ? (
            <span
              className="sr-only"
              data-testid="jobs-summary-absolute"
            >
              {formatAbsolute(latest.ts)}
            </span>
          ) : null}
        </CardContent>
        {serviceCounts.length > 0 ? (
          <div
            className="border-t border-border px-4 py-3"
            data-testid="jobs-summary-by-service"
          >
            <div className="mb-1.5 text-xs uppercase tracking-wide text-fg-muted">
              Catalog by service
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {serviceCounts.map(({ service, count }) => (
                <span
                  key={service}
                  className="inline-flex items-center gap-1 rounded-md border border-border bg-bg-2 px-2 py-0.5 font-mono text-[11px] text-fg"
                  data-testid={`jobs-summary-service-${service}`}
                >
                  <span className="text-fg-muted">{service}</span>
                  <span className="tabular-nums text-fg-faint">{count}</span>
                </span>
              ))}
            </div>
          </div>
        ) : null}
        {/* "What ran" preview: explicit job-name list from the latest
            batch so the operator immediately knows what fired without
            opening the history drawer. Chip-style with status tones. */}
        {latest?.jobs && Object.keys(latest.jobs).length > 0 ? (
          <div
            className="border-t border-border px-4 py-3"
            data-testid="jobs-summary-names"
          >
            <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs uppercase tracking-wide text-fg-muted">
              <span>Jobs in this batch</span>
              {latest.source ? (
                <span
                  className="inline-flex items-center rounded-md border border-info/40 bg-info/10 px-1.5 py-0 text-[10px] text-info"
                  data-testid={`jobs-summary-source-${latest.source}`}
                  title={`Triggered by ${latest.source}`}
                >
                  {latest.source}
                </span>
              ) : null}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {Object.entries(latest.jobs)
                .sort((a, b) => a[0].localeCompare(b[0]))
                .map(([name, value]) => {
                  const status = (value as { status?: unknown })?.status;
                  const tone =
                    status === "ok"
                      ? "border-success/40 bg-success/10 text-success"
                      : status === "skipped"
                        ? "border-warning/40 bg-warning/10 text-warning"
                        : status === "error" ||
                            status === "errors" ||
                            status === "failed"
                          ? "border-danger/40 bg-danger/10 text-danger"
                          : "border-border bg-bg-2 text-fg-muted";
                  const elapsed = (value as { elapsed?: number })?.elapsed;
                  return (
                    <button
                      key={name}
                      type="button"
                      onClick={() => setSelectedName(name)}
                      title={
                        typeof elapsed === "number"
                          ? `${name} — ${formatElapsed(elapsed)}`
                          : name
                      }
                      className={
                        "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono text-[11px] transition-colors " +
                        "[@media(hover:hover)]:hover:brightness-110 " +
                        tone
                      }
                      data-testid={`jobs-summary-name-${name}`}
                    >
                      <span>{name}</span>
                      {typeof elapsed === "number" ? (
                        <span className="text-fg-faint">
                          {formatElapsed(elapsed)}
                        </span>
                      ) : null}
                    </button>
                  );
                })}
            </div>
          </div>
        ) : null}
      </Card>

      <div
        className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]"
        data-testid="jobs-two-pane"
      >
        <Card className="p-3 sm:p-4">
          {loading && tree.length === 0 ? (
            <div className="flex flex-col gap-2" data-testid="jobs-tree-loading">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-7 w-full" />
              ))}
            </div>
          ) : (
            <JobsTreeView
              tree={tree}
              catalog={catalog}
              latest={latest}
              selectedName={selectedName}
              onSelect={(name) => setSelectedName(name)}
              revealName={reveal?.name ?? null}
              inFlightName={inFlightName}
            />
          )}
        </Card>

        <div className="min-w-0">
          {selected ? (
            <JobDetailPanel
              key={selected.name}
              job={selected}
              history={history}
              unmet={unmet}
              catalog={catalog}
              onRunningChange={(running) =>
                setInFlightName(running ? selected.name : null)
              }
              onReveal={(name) => {
                setSelectedName(name);
                // Bump the nonce so the tree re-runs the reveal effect
                // even if the operator picks the same chip twice.
                setReveal({ name, nonce: (reveal?.nonce ?? 0) + 1 });
              }}
            />
          ) : (
            <JobHistoryPanel history={history} catalog={catalog} />
          )}
        </div>
      </div>

      <RunHistoryPanel />
      <SchedulesCard />
    </div>
  );
}
