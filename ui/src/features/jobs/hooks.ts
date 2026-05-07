// Feature-local hooks for the Jobs operator surface.
//
// The controller's `GET /api/jobs` response is `{jobs, tree, count,
// history}`:
//
//   - `jobs`: flat catalog of every registered job (hundreds of entries),
//   - `tree`: recursive parent/child hierarchy keyed off the controller's
//     dependency graph,
//   - `history`: last ~20 batch runs with per-job status + elapsed,
//   - `count`: scalar (the size of `jobs`).
//
// The action endpoints live OUTSIDE `/api/*` — they're rooted at the
// controller itself:
//
//   POST /actions/{name}    -> {task_id}
//   POST /actions/cancel    -> cancel the in-flight action
//
// The `fetcher` helper accepts any path (it just forwards to `fetch`)
// so we pass these verbatim. Note this means `/actions/cancel` will be
// picked up by the path-contract scanner; it's allowlisted for that
// reason. The dynamic `/actions/${name}` is built via template string
// and the scanner only catches `/api/*` prefixes, so it stays invisible.
//
// We deliberately keep this hook private to the feature folder — it is
// not promoted to `src/api/hooks.ts` so the shared API surface stays
// tied to the OpenAPI spec.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";
import { asArray } from "@/lib/coerce";

// ---- Types --------------------------------------------------------------

/**
 * Per-job catalog entry. All fields except `name` are best-effort —
 * the controller may add more keys in the future (`additionalProperties:
 * true`), and absent values mean "not configured" rather than "error".
 */
export interface JobMeta {
  name: string;
  label?: string;
  service?: string;
  requires?: readonly string[];
  after?: readonly string[];
  max_attempts?: number | null;
  non_blocking?: boolean;
  /** Optional handler identifier emitted by some controller builds. */
  handler?: string;
  /**
   * Optional cron expression. The controller emits this for jobs
   * scheduled via the cron sidecar (reconcile / prewarm / hygiene).
   * The field is undefined for manual / dependency-driven jobs.
   *
   * Only `m h * * *` shape is parsed by the UI's `nextCronFire`
   * helper; anything else falls through to "—".
   */
  schedule?: string;
}

/**
 * Recursive tree node. Children live under `sub_jobs[]`; the controller
 * emits leaf nodes with `sub_jobs: []`.
 */
export interface JobTreeNode {
  name: string;
  requires?: readonly string[];
  sub_jobs?: readonly JobTreeNode[];
}

/** One batch-run history entry. */
export interface JobHistoryEntry {
  /** Unix epoch in seconds (that's what the controller emits). */
  ts?: number;
  /** Total batch elapsed in seconds. */
  elapsed?: number;
  ok?: number;
  skipped?: number;
  errors?: number;
  /** Per-job result map within this batch. */
  jobs?: Record<string, JobHistoryJobResult>;
  /**
   * Actor that triggered the batch — `cron`, `manual`, `auto-heal`.
   * A sibling agent is concurrently teaching the controller to emit
   * this field; it may be absent on older builds, so render with a
   * defensive read.
   */
  source?: string;
}

/** One per-job result inside a batch history entry. */
export interface JobHistoryJobResult {
  status?: "ok" | "skipped" | "error" | string;
  elapsed?: number;
  error?: string;
}

export interface JobsResponse {
  jobs: readonly JobMeta[];
  tree: readonly JobTreeNode[];
  history: readonly JobHistoryEntry[];
  count?: number;
}

interface RawJobsResponse {
  jobs?: unknown;
  tree?: unknown;
  history?: unknown;
  count?: unknown;
}

// ---- Query keys ---------------------------------------------------------

export const JOBS_QUERY_KEY = ["jobs"] as const;

// ---- Read hook ----------------------------------------------------------

/**
 * Fetch the controller's jobs payload — flat catalog, hierarchy tree,
 * and run history together. Polls every 5 seconds while at least one
 * observer is mounted; React Query stops the timer on the last unmount.
 *
 * The returned data is defensively coerced so a stray non-array shape
 * from the controller renders as an empty list instead of throwing.
 */
export function useJobs(): UseQueryResult<JobsResponse> {
  return useQuery<JobsResponse>({
    queryKey: JOBS_QUERY_KEY,
    queryFn: async () => {
      const raw = await fetcher<RawJobsResponse>("api/jobs");
      // The controller historically emitted `tree` as a SINGLE object
      // (the root JobTreeNode); v1.0.186+ wraps it in a list to match
      // the SPA's typed shape. Coerce defensively so both shapes
      // render correctly — bare object becomes a 1-element array.
      const rawTree = raw.tree;
      const treeArr = Array.isArray(rawTree)
        ? rawTree
        : rawTree && typeof rawTree === "object"
          ? [rawTree as JobTreeNode]
          : [];
      return {
        jobs: asArray<JobMeta>(raw.jobs),
        tree: treeArr as readonly JobTreeNode[],
        history: asArray<JobHistoryEntry>(raw.history),
        count: typeof raw.count === "number" ? raw.count : undefined,
      };
    },
    refetchInterval: 5_000,
  });
}

// ---- Mutations ----------------------------------------------------------

export interface RunActionResult {
  task_id?: string;
}

/**
 * Trigger a controller action by name. Hits `POST /actions/{name}` —
 * note this lives at the controller root, NOT under `/api/*`. The
 * `fetcher` helper just forwards the path to `fetch`, so leading-slash
 * non-`api/` paths work fine.
 *
 * On success, the jobs query is invalidated so the next poll picks up
 * the freshly-running task; we don't optimistically mutate because
 * the history entry only materializes when the batch completes.
 */
export function useRunAction(
  name: string,
): UseMutationResult<RunActionResult, Error, void> {
  const qc = useQueryClient();
  return useMutation<RunActionResult, Error, void>({
    mutationFn: () =>
      // MUST go through `api/...` — the SPA's nginx only proxies
      // `/api/*` to the controller. A bare `/actions/<name>` falls
      // into the SPA-fallback `try_files` block and returns 405 on
      // POST (nginx default for static-file locations). The
      // controller registers both `/api/actions/<name>` and the
      // legacy `/actions/<name>` for backward compat.
      fetcher<RunActionResult>(`api/actions/${encodeURIComponent(name)}`, {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
    },
  });
}

/**
 * Cancel the controller's in-flight action. Returns whatever the
 * controller emits (typically a 200 with `{cancelled: true}` or a 409
 * if there was nothing to cancel — we just surface the body).
 */
/**
 * Tree node returned under the ``tree`` field of
 * ``GET /api/jobs/running``. Children are nested in ``started_at``
 * ascending order; orphan children (parent already settled) surface
 * as top-level nodes.
 */
export interface RunningTreeNodeShape {
  run_id: string;
  job_name: string;
  status: string;
  started_at: number;
  elapsed_seconds: number;
  triggered_by: string;
  actor: string;
  parent_run_id: string;
  batch_id: string;
  children: readonly RunningTreeNodeShape[];
}

export interface JobsRunningResponse {
  running: readonly unknown[];
  count: number;
  tree: readonly RunningTreeNodeShape[];
}

/**
 * Polls ``GET /api/jobs/running`` for the in-flight job tree the
 * Jobs page's ``CurrentlyRunningCard`` renders. Cadence is 5s so a
 * spawned sub-job appears promptly even when the SSE invalidation
 * is unavailable; SSE flips this query into instant-refresh mode
 * via the EventStreamProvider's ``handleEvent`` (job.* events
 * invalidate ``["jobs"]``).
 */
export function useJobsRunning(): UseQueryResult<JobsRunningResponse> {
  return useQuery<JobsRunningResponse>({
    queryKey: ["jobs", "running"],
    queryFn: () => fetcher<JobsRunningResponse>("api/jobs/running"),
    refetchInterval: 5_000,
    staleTime: 1_000,
    retry: false,
  });
}

// ---- Job queue (Phase 5) -----------------------------------------------

export interface QueueEntryShape {
  id: number;
  job_name: string;
  source: string;
  scheduled_at: number;
  enqueued_at: number;
  label: string;
}

export interface JobQueueResponse {
  queue: readonly QueueEntryShape[];
  count: number;
}

const QUEUE_QUERY_KEY = ["jobs", "queue"] as const;

export function useJobQueue(): UseQueryResult<JobQueueResponse> {
  return useQuery<JobQueueResponse>({
    queryKey: QUEUE_QUERY_KEY,
    queryFn: () => fetcher<JobQueueResponse>("api/jobs/queue"),
    refetchInterval: 10_000,
    staleTime: 2_000,
    retry: false,
  });
}

export interface QueueEnqueueInput {
  job_name: string;
  source?: string;
  label?: string;
}

export function useEnqueueJob(): UseMutationResult<
  unknown,
  Error,
  QueueEnqueueInput
> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, QueueEnqueueInput>({
    mutationFn: (input) =>
      fetcher<unknown>("api/jobs/queue", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUEUE_QUERY_KEY });
    },
  });
}

export function useRemoveQueueEntry(): UseMutationResult<
  unknown,
  Error,
  number
> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, number>({
    mutationFn: (id) =>
      fetcher<unknown>(`api/jobs/queue/${id}/remove`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUEUE_QUERY_KEY });
    },
  });
}

export interface QueueReorderInput {
  entry_id: number;
  direction?: "up" | "down";
  position?: number;
}

export function useReorderQueueEntry(): UseMutationResult<
  unknown,
  Error,
  QueueReorderInput
> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, QueueReorderInput>({
    mutationFn: ({ entry_id, ...body }) =>
      fetcher<unknown>(`api/jobs/queue/${entry_id}/reorder`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUEUE_QUERY_KEY });
    },
  });
}

// ---- Schedules (Phase 4) -----------------------------------------------

export interface ScheduleShape {
  id: number;
  action: string;
  interval_seconds: number;
  label: string;
  created_at: number;
  last_run: number;
  enabled: boolean;
}

export interface SchedulesResponse {
  schedules: readonly ScheduleShape[];
  count: number;
}

const SCHEDULES_QUERY_KEY = ["schedules"] as const;

export function useSchedules(): UseQueryResult<SchedulesResponse> {
  return useQuery<SchedulesResponse>({
    queryKey: SCHEDULES_QUERY_KEY,
    queryFn: () => fetcher<SchedulesResponse>("api/schedules"),
    refetchInterval: 30_000,
    retry: false,
  });
}

export interface ScheduleCreateInput {
  action: string;
  interval_seconds: number;
  label?: string;
  enabled?: boolean;
}

export function useAddSchedule(): UseMutationResult<
  unknown,
  Error,
  ScheduleCreateInput
> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, ScheduleCreateInput>({
    mutationFn: (input) =>
      fetcher<unknown>("api/schedules", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export interface ScheduleUpdateInput {
  schedule_id: number;
  action?: string;
  interval_seconds?: number;
  label?: string;
  enabled?: boolean;
}

export function useUpdateSchedule(): UseMutationResult<
  unknown,
  Error,
  ScheduleUpdateInput
> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, ScheduleUpdateInput>({
    mutationFn: ({ schedule_id, ...body }) =>
      fetcher<unknown>(`api/schedules/${schedule_id}/update`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export function usePauseSchedule(): UseMutationResult<unknown, Error, number> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, number>({
    mutationFn: (id) =>
      fetcher<unknown>(`api/schedules/${id}/pause`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export function useResumeSchedule(): UseMutationResult<unknown, Error, number> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, number>({
    mutationFn: (id) =>
      fetcher<unknown>(`api/schedules/${id}/resume`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export function useDeleteSchedule(): UseMutationResult<unknown, Error, number> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, number>({
    mutationFn: (id) =>
      fetcher<unknown>(`api/schedules/${id}/delete`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
    },
  });
}

export function useCancelAction(): UseMutationResult<unknown, Error, void> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, void>({
    mutationFn: () =>
      fetcher<unknown>("api/actions/cancel", { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
    },
  });
}

// ---- Run history (Jobs Phase 2) ----------------------------------------

/**
 * Per-run telemetry record returned by ``GET /api/runs/...``. Mirrors
 * the Python ``RunRecord`` shape (see
 * ``src/media_stack/domain/jobs/run_record.py``). All optional fields
 * may be absent.
 */
export interface RunRecordShape {
  run_id: string;
  job_name: string;
  status:
    | "running"
    | "ok"
    | "skipped"
    | "error"
    | "cancelled"
    | "timeout"
    | string;
  started_at: number;
  parent_run_id?: string;
  batch_id?: string;
  completed_at?: number;
  elapsed?: number;
  triggered_by: string;
  actor?: string;
  attempts: number;
  error?: string;
  stdout_tail?: string;
  log_anchor?: {
    source: string;
    since_iso: string;
    until_iso?: string;
    action?: string;
  };
  child_run_ids: readonly string[];
  /** Z-score relative to the rolling mean of recent runs of the
   *  same job. ``null``/absent until the controller has 10+
   *  prior samples for this job. The UI tints rows above 1σ amber
   *  and above 2σ red. */
  anomaly_score?: number | null;
  /** When ``parent_run_id`` is set AND the parent record is still
   *  in the persistence window, the controller inlines the parent
   *  record's ``job_name`` here so the UI can render
   *  "child-job (under parent-name)" without a second fetch. */
  parent_job_name?: string;
}

/** Returned by ``GET /api/runs/<run_id>`` — the record plus its
 *  child runs inlined. */
export interface RunRecordWithChildrenShape extends RunRecordShape {
  children: readonly RunRecordShape[];
}

/** Most-recent run for a given job name. ``null`` when there's no
 *  history yet. */
export function useLatestRunForJob(
  jobName: string | null | undefined,
  opts?: { refetchInterval?: number | false },
): UseQueryResult<RunRecordShape | null> {
  return useQuery<RunRecordShape | null>({
    queryKey: ["runs", "latest", jobName ?? ""],
    queryFn: async () => {
      if (!jobName) return null;
      try {
        return await fetcher<RunRecordShape>(
          `api/runs/latest/${encodeURIComponent(jobName)}`,
        );
      } catch (err) {
        if (
          typeof err === "object" &&
          err !== null &&
          "status" in err &&
          (err as { status?: number }).status === 404
        ) {
          return null;
        }
        throw err;
      }
    },
    enabled: Boolean(jobName),
    refetchInterval: opts?.refetchInterval ?? 5_000,
    retry: false,
  });
}

/** A single run by id, including its inline children. */
export function useRun(
  runId: string | null | undefined,
  opts?: { refetchInterval?: number | false },
): UseQueryResult<RunRecordWithChildrenShape | null> {
  return useQuery<RunRecordWithChildrenShape | null>({
    queryKey: ["runs", "detail", runId ?? ""],
    queryFn: async () => {
      if (!runId) return null;
      return await fetcher<RunRecordWithChildrenShape>(
        `api/runs/${encodeURIComponent(runId)}`,
      );
    },
    enabled: Boolean(runId),
    refetchInterval: opts?.refetchInterval ?? 5_000,
    retry: false,
  });
}

// ---- Lifecycle ensurer dispatch (ADR-0005 Phase 5b step 3) -------------

/**
 * Body for ``POST /api/lifecycle-ensurers/{service}/{method}``. Both
 * fields are optional; the controller defaults ``source`` to
 * ``"operator"`` when omitted.
 *
 * Mirrors ``paths["/api/lifecycle-ensurers/{service}/{method}"]
 * ["post"]["requestBody"]["content"]["application/json"]`` from
 * ``src/api/types.ts``. We don't import that path directly because the
 * codegen union is awkward to spell here and the surface is a single
 * request body shape — duplicating two narrow fields is cheaper than
 * dragging the operations[] type through.
 */
export interface LifecycleEnsurerInvokeInput {
  /**
   * Caller tag. Defaults to ``"operator"`` server-side. The dispatcher
   * recognizes ``"operator"`` (dashboard "Run now"), ``"auto-heal"``
   * (recovery loop), and ``"orchestrator-tick"`` (reserved — the
   * orchestrator never goes through this endpoint).
   */
  source?: "operator" | "auto-heal" | "orchestrator-tick";
  /**
   * Per-call config overrides. Reserved — today the dispatcher reads
   * config from the contract YAML.
   */
  overrides?: Readonly<Record<string, unknown>>;
}

/**
 * Outcome envelope returned by the lifecycle-ensurer endpoint. The
 * HTTP status is 200 when the dispatcher RAN; the ``status`` field
 * carries the ensurer's outcome.
 *
 *   - ``success``    — ensurer reached the desired state.
 *   - ``transient``  — failed but worth retrying.
 *   - ``permanent``  — failed; operator must intervene.
 *
 * 404 → ApiError with ``status: 404`` (unknown ``(service, method)``
 * pair). The mutation surfaces that to the caller via ``error``.
 */
export interface LifecycleEnsurerInvokeResult {
  status: "success" | "transient" | "permanent";
  message: string;
  source: string;
  evidence: Readonly<Record<string, unknown>>;
  attempts?: number;
  elapsed_seconds?: number;
}

/**
 * Optional extra React Query keys to invalidate after a successful
 * dispatch. Always invalidates the jobs query (``["jobs"]``) and the
 * running-jobs query (``["jobs","running"]``) — those are unconditional
 * because every ensurer dispatch may surface in the running tree
 * mid-flight and in the next batch history poll.
 *
 * Pass per-feature keys (``["indexers"]``, ``["jellyfin","libraries"]``)
 * when the ensurer mutates data the operator is currently viewing so
 * the affected card refetches without a manual reload.
 */
export interface UseRunLifecycleEnsurerOptions {
  invalidateKeys?: ReadonlyArray<readonly unknown[]>;
}

/**
 * ADR-0005 Phase 5b step 3: dispatch a single lifecycle ensurer by
 * ``(service, method)``. Replaces ``useRunAction("ensure-X")`` for
 * ensurer-shaped names — those resolve through the legacy
 * ``run_job(name)`` path, which Phase 5b step 5 deletes.
 *
 * Hits ``POST /api/lifecycle-ensurers/{service}/{method}`` (single
 * dispatch entry point shared with the orchestrator and auto-heal).
 * The mutation completes synchronously: the response carries the
 * outcome (``success`` / ``transient`` / ``permanent``) inline rather
 * than a ``task_id`` to poll on. Callers branch on ``data.status``
 * to render the post-dispatch toast.
 *
 *   - 200 + ``status: "success"``   → ensurer succeeded.
 *   - 200 + ``status: "transient"`` → ensurer failed; retry is safe.
 *   - 200 + ``status: "permanent"`` → ensurer failed; operator must
 *                                     intervene (read ``message`` /
 *                                     ``evidence`` for why).
 *   - 404                           → unknown ``(service, method)``;
 *                                     surfaces as an ``ApiError`` on
 *                                     the mutation's ``error`` field.
 *   - 403                           → CSRF / admin gate; ``ApiError``.
 *
 * On success, invalidates the jobs queries (``["jobs"]`` +
 * ``["jobs","running"]``) so the next poll picks up any history
 * record the dispatcher persisted, plus any caller-supplied
 * ``invalidateKeys`` for per-feature refetch.
 */
export function useRunLifecycleEnsurer(
  service: string,
  method: string,
  options: UseRunLifecycleEnsurerOptions = {},
): UseMutationResult<
  LifecycleEnsurerInvokeResult,
  Error,
  LifecycleEnsurerInvokeInput | void
> {
  const qc = useQueryClient();
  const { invalidateKeys } = options;
  return useMutation<
    LifecycleEnsurerInvokeResult,
    Error,
    LifecycleEnsurerInvokeInput | void
  >({
    mutationFn: (input) => {
      const path = `api/lifecycle-ensurers/${encodeURIComponent(
        service,
      )}/${encodeURIComponent(method)}`;
      const body =
        input && (input.source !== undefined || input.overrides !== undefined)
          ? JSON.stringify(input)
          : undefined;
      const init: { method: "POST"; body?: string } = { method: "POST" };
      if (body !== undefined) init.body = body;
      return fetcher<LifecycleEnsurerInvokeResult>(path, init);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: JOBS_QUERY_KEY });
      void qc.invalidateQueries({ queryKey: ["jobs", "running"] });
      if (invalidateKeys) {
        for (const key of invalidateKeys) {
          void qc.invalidateQueries({ queryKey: key });
        }
      }
    },
  });
}

/** Filtered list of runs. Useful for the Run history tab. */
export function useRuns(
  filters: {
    jobName?: string;
    parentRunId?: string;
    batchId?: string;
    sinceTs?: number;
    limit?: number;
  } = {},
): UseQueryResult<readonly RunRecordShape[]> {
  return useQuery<readonly RunRecordShape[]>({
    queryKey: ["runs", "list", filters],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (filters.jobName) params.set("job", filters.jobName);
      if (filters.parentRunId) params.set("parent", filters.parentRunId);
      if (filters.batchId) params.set("batch", filters.batchId);
      if (filters.sinceTs !== undefined) {
        params.set("since", String(filters.sinceTs));
      }
      params.set("limit", String(filters.limit ?? 100));
      const qs = params.toString();
      const data = await fetcher<{ runs?: readonly RunRecordShape[] }>(
        `api/runs?${qs}`,
      );
      return data.runs ?? [];
    },
    refetchInterval: 5_000,
    retry: false,
  });
}
