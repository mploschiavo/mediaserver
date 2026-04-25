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
