// Feature-local Tanstack Query hooks for the stack lifecycle surface
// (update probe, in-place upgrade, upgrade-progress poll, migration
// safety check). Lives here rather than in `src/api/hooks.ts` because
// the parent api/hooks barrel is owned by sibling agents shipping
// other waves in parallel — the stack-lifecycle endpoints are a
// self-contained controller surface and the feature-folder convention
// keeps the patches reviewable in isolation.
//
// Each hook wraps `fetcher` from the shared client so it inherits:
//   - same-origin cookie threading,
//   - automatic Idempotency-Key generation on POST,
//   - 401 emission to the global auth event bus (the layout shell
//     listens and decides what to do — these hooks do not navigate).

import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

const STACK_UPDATE_PATH = "api/stack/update";
const STACK_UPGRADE_PATH = "api/stack/upgrade";
const VALIDATE_MIGRATION_PATH = "api/validate-migration";

/** GET /api/stack/update — release-probe payload. */
export interface StackUpdateShape {
  available: boolean;
  current_version?: string;
  latest_version?: string;
  release_notes?: string;
}

/** POST /api/stack/upgrade — kicks off an in-place upgrade. */
export interface StackUpgradeAcceptedShape {
  task_id: string;
}

export type StackUpgradeState = "queued" | "running" | "done" | "failed";

/** GET /api/stack/upgrade/{task_id} — upgrade progress. */
export interface StackUpgradeProgressShape {
  state: StackUpgradeState;
  progress?: number;
  log_tail?: readonly string[];
}

/** GET /api/validate-migration — pre-upgrade safety check. */
export interface ValidateMigrationShape {
  ok: boolean;
  blockers?: readonly string[];
  warnings?: readonly string[];
}

export const stackLifecycleQueryKeys = {
  update: ["stack", "update"] as const,
  upgradeProgress: (taskId: string) =>
    ["stack", "upgrade", taskId] as const,
  validateMigration: ["validate-migration"] as const,
};

/**
 * `useStackUpdate` — polls `/api/stack/update` for the release probe
 * payload. We refetch every 60 s so an admin who leaves the dashboard
 * open over a release window sees the banner without reloading.
 */
export function useStackUpdate(): UseQueryResult<StackUpdateShape> {
  return useQuery({
    queryKey: stackLifecycleQueryKeys.update,
    queryFn: () => fetcher<StackUpdateShape>(STACK_UPDATE_PATH),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });
}

/**
 * `useStackUpgrade` — POSTs `/api/stack/upgrade` to kick off the in-place
 * upgrade. Returns the `{ task_id }` payload so the caller can switch
 * to the progress dialog and poll via `useStackUpgradeProgress`.
 */
export function useStackUpgrade(): UseMutationResult<
  StackUpgradeAcceptedShape,
  Error,
  void
> {
  return useMutation({
    mutationFn: () =>
      fetcher<StackUpgradeAcceptedShape>(STACK_UPGRADE_PATH, {
        method: "POST",
      }),
  });
}

/**
 * `useStackUpgradeProgress` — polls `/api/stack/upgrade/{task_id}` every
 * 5 s while the task is still running. Stops polling automatically as
 * soon as the server reports `done` or `failed` so the dialog can be
 * dismissed without leaking timers.
 *
 * Disabled when `taskId` is undefined.
 */
export function useStackUpgradeProgress(
  taskId: string | undefined,
): UseQueryResult<StackUpgradeProgressShape> {
  return useQuery({
    queryKey: stackLifecycleQueryKeys.upgradeProgress(taskId ?? ""),
    queryFn: () =>
      fetcher<StackUpgradeProgressShape>(
        `${STACK_UPGRADE_PATH}/${encodeURIComponent(taskId as string)}`,
      ),
    enabled: typeof taskId === "string" && taskId.length > 0,
    // Stop polling once the task is terminal. Per Tanstack Query's
    // contract returning `false` from the callback halts the interval;
    // the active query stays cached so the UI keeps the last frame.
    refetchInterval: (q) =>
      q.state.data?.state === "running" ? 5000 : false,
    retry: false,
  });
}

/**
 * `useValidateMigration` — pre-upgrade migration safety check. The
 * controller returns `{ ok, blockers, warnings }`; the UI surfaces
 * blockers as red, warnings as amber, ok as green. Light cache so
 * an upgrade triggered shortly after the page mounts re-runs the
 * check.
 */
export function useValidateMigration(): UseQueryResult<ValidateMigrationShape> {
  return useQuery({
    queryKey: stackLifecycleQueryKeys.validateMigration,
    queryFn: () => fetcher<ValidateMigrationShape>(VALIDATE_MIGRATION_PATH),
    staleTime: 30_000,
    retry: false,
  });
}
