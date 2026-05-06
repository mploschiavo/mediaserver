// Feature-local hooks for the Disk Guardrails (Storage) operator
// surface. Each hook wraps one of the six endpoints exposed by
// ADR-0008 Phase 2:
//
//   GET  /api/disk-guardrails               — merged status snapshot
//   POST /api/disk-guardrails/cleanup       — run cleanup synchronously
//   POST /api/disk-guardrails/lockdown      — engage manual lockdown
//   POST /api/disk-guardrails/release       — release lockdown
//   POST /api/disk-guardrails/pause-auto    — pause auto evaluation N hours
//   POST /api/disk-guardrails/evaluate      — force one immediate tick
//
// Plus a passthrough to the GuardrailRegistry's existing per-rule
// threshold-update endpoint:
//
//   POST /api/guardrails/{rule_id}/threshold
//
// All bodies use snake_case wire keys per the project's wire-format
// convention (`bug_class_url_value_case_normalization` memory). The
// fetcher handles CSRF + Idempotency-Key automatically; the hooks
// just thread their body shapes.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";
import { storageQueryKeys } from "./queryKeys";

// ---- Wire-format types --------------------------------------------------
//
// The OpenAPI spec marks several of these objects `additionalProperties:
// true` (`thresholds`, `last_failures[]`, `transitions[]`) so we keep the
// inner shapes loose with `[key: string]: unknown` and narrow at the
// consumer site. See `bug_class_openapi_vs_live_shape` — the live
// controller is the source of truth.

export type DiskGuardrailState =
  | "NORMAL"
  | "WATCH"
  | "CLEANUP"
  | "AUTO_LOCKDOWN"
  | "MANUAL_LOCKDOWN"
  | string;

export type DiskGuardrailTrigger = "auto" | "manual" | null;

export interface DiskGuardrailThresholds {
  lockdown_percent?: number;
  release_percent?: number;
  watch_percent?: number;
  cleanup_percent?: number;
  [key: string]: unknown;
}

export interface DiskGuardrailFailure {
  client?: string;
  error?: string;
  ts?: number;
  [key: string]: unknown;
}

export interface DiskGuardrailTransition {
  ts?: number;
  action?: string;
  actor?: string;
  used_percent?: number;
  detail?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface DiskGuardrailStatus {
  state: DiskGuardrailState;
  used_percent_by_mount: Record<string, number>;
  thresholds: DiskGuardrailThresholds;
  engaged_at?: number;
  engaged_by?: string;
  trigger?: DiskGuardrailTrigger;
  auto_check_paused_until?: number | null;
  paused_clients: readonly string[];
  last_failures: readonly DiskGuardrailFailure[];
  transitions: readonly DiskGuardrailTransition[];
}

export interface RunCleanupInput {
  categories?: readonly string[];
  max_delete?: number;
}

export interface RunCleanupResponse {
  deleted?: number;
  freed_gb?: number;
  kept?: number;
  candidates_evaluated?: number;
  strategy?: string;
}

export interface EngageLockdownResponse {
  state: DiskGuardrailState;
  paused_clients: readonly string[];
  failures: readonly DiskGuardrailFailure[];
}

export interface ReleaseLockdownResponse {
  state: DiskGuardrailState;
  released_clients: readonly string[];
}

export interface PauseAutoResponse {
  paused_until: number | null;
  hours: number;
}

export interface EvaluateResponse {
  ran_at?: number;
  elapsed?: number;
  triggers?: readonly Record<string, unknown>[];
  actions?: readonly Record<string, unknown>[];
}

/** UI-facing shape for the threshold form. snake_case is the wire
 *  format; we expose camelCase to consumers and remap at the call
 *  site. */
export interface UpdateThresholdsInput {
  watchPercent: number;
  cleanupPercent: number;
  lockdownPercent: number;
  releasePercent: number;
}

/** Phase 4 cleanup-policy POST body. Snake_case wire keys match the
 *  controller's validator. Every field is optional — the body is a
 *  selective overlay over the controller's persisted defaults. */
export interface UpdateCleanupPolicyInput {
  categories?: readonly string[];
  min_completion_age_hours?: number;
  min_seeding_time_minutes?: number;
  min_ratio?: number;
  max_delete_per_run?: number;
  order_strategy?:
    | "oldest_first"
    | "largest_first"
    | "poor_ratio_first"
    | "watched_first";
}

export interface UpdateCleanupPolicyResponse {
  policy: UpdateCleanupPolicyInput;
}

// ---- Read hook ----------------------------------------------------------

/**
 * Polls the merged disk-guardrails status snapshot every 30 seconds.
 *
 * The cadence is intentionally slower than the jobs poll (5 s):
 * disk pressure changes more slowly than job state, and the SSE
 * bridge in `EventStreamProvider` flips this query into instant
 * refresh as soon as the controller starts publishing
 * `storage.lockdown_*` / `storage.cleanup_*` events.
 */
export function useDiskGuardrailsStatus(): UseQueryResult<DiskGuardrailStatus> {
  return useQuery<DiskGuardrailStatus>({
    queryKey: storageQueryKeys.status,
    queryFn: () => fetcher<DiskGuardrailStatus>("api/disk-guardrails"),
    refetchInterval: 30_000,
    staleTime: 5_000,
    retry: false,
  });
}

// ---- Mutations ----------------------------------------------------------

function useInvalidateStatus() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: storageQueryKeys.status });
  };
}

/**
 * Forces a synchronous cleanup. The body is optional — empty body
 * means "use the controller's persisted defaults" (categories /
 * max_delete fall back to `cfg["disk_guardrails"]["qbit_cleanup"]`).
 */
export function useRunCleanup(): UseMutationResult<
  RunCleanupResponse,
  Error,
  RunCleanupInput | void
> {
  const invalidate = useInvalidateStatus();
  return useMutation<RunCleanupResponse, Error, RunCleanupInput | void>({
    mutationFn: (input) =>
      fetcher<RunCleanupResponse>("api/disk-guardrails/cleanup", {
        method: "POST",
        body: input ? JSON.stringify(input) : undefined,
      }),
    onSuccess: () => invalidate(),
  });
}

export function useEngageLockdown(): UseMutationResult<
  EngageLockdownResponse,
  Error,
  void
> {
  const invalidate = useInvalidateStatus();
  return useMutation<EngageLockdownResponse, Error, void>({
    mutationFn: () =>
      fetcher<EngageLockdownResponse>("api/disk-guardrails/lockdown", {
        method: "POST",
      }),
    onSuccess: () => invalidate(),
  });
}

export function useReleaseLockdown(): UseMutationResult<
  ReleaseLockdownResponse,
  Error,
  void
> {
  const invalidate = useInvalidateStatus();
  return useMutation<ReleaseLockdownResponse, Error, void>({
    mutationFn: () =>
      fetcher<ReleaseLockdownResponse>("api/disk-guardrails/release", {
        method: "POST",
      }),
    onSuccess: () => invalidate(),
  });
}

/**
 * Pauses AUTO evaluation for N hours (1-24). `hours` is sent as a
 * query parameter per the OpenAPI contract — the controller rejects
 * out-of-range values with 400 and clamps `>24` server-side.
 */
export function usePauseGuardrails(): UseMutationResult<
  PauseAutoResponse,
  Error,
  { hours: number }
> {
  const invalidate = useInvalidateStatus();
  return useMutation<PauseAutoResponse, Error, { hours: number }>({
    mutationFn: ({ hours }) =>
      fetcher<PauseAutoResponse>(
        `api/disk-guardrails/pause-auto?hours=${encodeURIComponent(String(hours))}`,
        { method: "POST" },
      ),
    onSuccess: () => invalidate(),
  });
}

export function useForceEvaluate(): UseMutationResult<
  EvaluateResponse,
  Error,
  void
> {
  const invalidate = useInvalidateStatus();
  return useMutation<EvaluateResponse, Error, void>({
    mutationFn: () =>
      fetcher<EvaluateResponse>("api/disk-guardrails/evaluate", {
        method: "POST",
      }),
    onSuccess: () => invalidate(),
  });
}

/**
 * Updates the four-tier thresholds via the GuardrailRegistry's
 * existing per-rule update endpoint. The Storage card surfaces
 * `storage:lockdown_threshold` (lockdown + release) AND the
 * cleanup-tier thresholds owned by `_PerMountThreshold`; both
 * accept a `threshold` JSON object via the same route shape.
 *
 * Endpoint: `POST /api/guardrails/{rule_id}` (per `updateGuardrailThreshold`
 * in `openapi.yaml`) with body
 *   { threshold: { lockdown_percent, release_percent } }
 *
 * The UI submits both rules' thresholds in a single click by
 * fanning out two POSTs and awaiting both before resolving — the
 * registry has no batched-update shape.
 */
/**
 * Persists cleanup-policy overrides via the Phase 4
 * `POST /api/disk-guardrails/cleanup-policy` endpoint. The body is
 * a partial overlay — every field is optional. The controller writes
 * the JSON file at `/srv-config/.controller/disk-cleanup-policy.json`;
 * the next `DiskGuardrailsService.enforce()` pass reads from it.
 */
export function useUpdateCleanupPolicy(): UseMutationResult<
  UpdateCleanupPolicyResponse,
  Error,
  UpdateCleanupPolicyInput
> {
  const invalidate = useInvalidateStatus();
  return useMutation<UpdateCleanupPolicyResponse, Error, UpdateCleanupPolicyInput>({
    mutationFn: (input) =>
      fetcher<UpdateCleanupPolicyResponse>(
        "api/disk-guardrails/cleanup-policy",
        {
          method: "POST",
          body: JSON.stringify(input),
        },
      ),
    onSuccess: () => invalidate(),
  });
}

export function useUpdateThresholds(): UseMutationResult<
  unknown,
  Error,
  UpdateThresholdsInput
> {
  const invalidate = useInvalidateStatus();
  return useMutation<unknown, Error, UpdateThresholdsInput>({
    mutationFn: async ({
      watchPercent,
      cleanupPercent,
      lockdownPercent,
      releasePercent,
    }) => {
      // Lockdown rule owns lockdown+release; PerMountThreshold owns
      // the cleanup tier. Watch is purely a UI hint until the
      // controller adopts a `_WatchTier` rule (Phase 4 follow-up).
      const lockdownBody = JSON.stringify({
        threshold: {
          lockdown_percent: lockdownPercent,
          release_percent: releasePercent,
          watch_percent: watchPercent,
        },
      });
      const cleanupBody = JSON.stringify({
        threshold: {
          cleanup_percent: cleanupPercent,
        },
      });
      // The OpenAPI rule id is sent as the {id} path param; the
      // colon character is allowed in REST path segments by RFC 3986
      // but we encode it for safety in case Envoy normalises.
      const lockdownReq = fetcher<unknown>(
        `api/guardrails/${encodeURIComponent("storage:lockdown_threshold")}`,
        { method: "POST", body: lockdownBody },
      );
      const cleanupReq = fetcher<unknown>(
        `api/guardrails/${encodeURIComponent("storage:per_mount_threshold")}`,
        { method: "POST", body: cleanupBody },
      );
      // `Promise.allSettled` so a 404 on one of the rules (e.g. the
      // cleanup rule isn't registered on a stripped-down install)
      // doesn't blow away the other half of the save. The component
      // surfaces partial-failure as a toast.
      const results = await Promise.allSettled([lockdownReq, cleanupReq]);
      const failed = results.filter((r) => r.status === "rejected");
      if (failed.length === results.length) {
        throw (failed[0] as PromiseRejectedResult).reason;
      }
      return results;
    },
    onSuccess: () => invalidate(),
  });
}
