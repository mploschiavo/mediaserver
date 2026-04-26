// Feature-local hooks for the cross-domain Guardrails surface.
//
// Mirrors `features/jobs/hooks.ts`: this is intentionally not promoted
// into `src/api/hooks.ts` because the shared API layer is pinned to
// the OpenAPI spec and the guardrails endpoints are still landing.
// When `pnpm gen:api` produces typed shapes from the spec, switch
// to those instead of the local interfaces below.

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

export type GuardrailDomain =
  | "storage"
  | "bandwidth"
  | "external_api"
  | "media_quality"
  | "job_health"
  | "auth"
  | "dependency"
  | "cost";

export type GuardrailStatus =
  | "ok"
  | "info"
  | "warning"
  | "critical"
  | "disabled"
  | "unknown";

/** One row in the registry. Threshold is a free-form key/value bag
 *  whose shape varies per rule (e.g. {max_percent, target_percent}
 *  for the per-mount rule; {max_gb_per_day} for the upload cap). */
export interface Guardrail {
  id: string;
  domain: GuardrailDomain;
  description: string;
  threshold: Record<string, unknown>;
  default_threshold?: Record<string, unknown>;
  last_status?: GuardrailStatus;
  last_severity?: string;
  last_severity_streak?: number;
  last_evaluated_at?: number;
  last_triggered_at?: number;
  disabled?: boolean;
}

interface GuardrailsResponse {
  guardrails: readonly Guardrail[];
  /** Cadence (seconds) at which guardrails re-evaluate. Configurable
   *  via POST /api/guardrails/config. Default 300 (5min). */
  evaluation_interval_seconds: number;
}

interface RawGuardrailsResponse {
  guardrails?: unknown;
  evaluation_interval_seconds?: unknown;
}

export interface GuardrailTestResult {
  would_trigger: boolean;
  severity: string | null;
  current_value: unknown;
  threshold: Record<string, unknown>;
  description?: string;
}

// ---- Query keys ---------------------------------------------------------

export const GUARDRAILS_QUERY_KEY = ["guardrails"] as const;

// ---- Read hook ----------------------------------------------------------

/**
 * Fetch the registry payload. Polls every 30s — the registry mostly
 * changes when the auto-heal cycle updates last_status, which itself
 * runs on a 60s tick. Polling faster would mostly hit the cache.
 */
export function useGuardrails(): UseQueryResult<GuardrailsResponse> {
  return useQuery<GuardrailsResponse>({
    queryKey: GUARDRAILS_QUERY_KEY,
    queryFn: async () => {
      // `silenceAuthEvent`: this query mounts globally (TriggeredBanner
      // in AppShell) on every page. A 401 here means "user isn't
      // authorised for guardrails", NOT "session is dead" — bouncing
      // the whole SPA to Authelia would unmount whatever page the
      // operator was on (caused the v1.3.4 "Logs page briefly shows
      // then disappears" bug). The banner just renders nothing on
      // error; React Query continues polling at refetchInterval.
      const raw = await fetcher<RawGuardrailsResponse>("api/guardrails", {
        silenceAuthEvent: true,
      });
      const interval =
        typeof raw.evaluation_interval_seconds === "number" &&
        Number.isFinite(raw.evaluation_interval_seconds)
          ? raw.evaluation_interval_seconds
          : 300;
      return {
        guardrails: asArray<Guardrail>(raw.guardrails),
        evaluation_interval_seconds: interval,
      };
    },
    refetchInterval: 30_000,
    // Don't retry 4xx — re-firing an admin-gated endpoint that the
    // user isn't allowed to call just wastes round-trips.
    retry: (failureCount, err) => {
      const status = (err as { status?: number } | undefined)?.status;
      if (typeof status === "number" && status >= 400 && status < 500) {
        return false;
      }
      return failureCount < 1;
    },
  });
}

// ---- Mutations ----------------------------------------------------------

/**
 * Updates the cross-domain guardrail evaluation cadence. Backend
 * floors at 30s and ceilings at 3600s; the controller persists the
 * value in-process for the rest of its lifetime.
 */
export function useUpdateGuardrailsConfig(): UseMutationResult<
  { evaluation_interval_seconds: number },
  Error,
  { evaluation_interval_seconds: number }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<{ evaluation_interval_seconds: number }>(
        "api/guardrails/config",
        { method: "POST", body: JSON.stringify(body) },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: GUARDRAILS_QUERY_KEY });
    },
  });
}


export function useUpdateGuardrail(
  id: string,
): UseMutationResult<unknown, Error, Record<string, unknown>> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, Record<string, unknown>>({
    mutationFn: (threshold) =>
      // fetcher auto-sets Content-Type: application/json when body is
      // present; we only need to stringify.
      fetcher<unknown>(`api/guardrails/${encodeURIComponent(id)}`, {
        method: "POST",
        body: JSON.stringify({ threshold }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: GUARDRAILS_QUERY_KEY });
    },
  });
}

export function useTestGuardrail(
  id: string,
): UseMutationResult<GuardrailTestResult, Error, void> {
  return useMutation<GuardrailTestResult, Error, void>({
    mutationFn: () =>
      fetcher<GuardrailTestResult>(
        `api/guardrails/${encodeURIComponent(id)}/test`,
        { method: "POST" },
      ),
  });
}

export function useDisableGuardrail(
  id: string,
): UseMutationResult<unknown, Error, boolean> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, boolean>({
    mutationFn: (disabled) =>
      fetcher<unknown>(`api/guardrails/${encodeURIComponent(id)}/disable`, {
        method: "POST",
        body: JSON.stringify({ disabled }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: GUARDRAILS_QUERY_KEY });
    },
  });
}
