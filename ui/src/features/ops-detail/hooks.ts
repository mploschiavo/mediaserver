// Feature-local hooks for the deeper ops/health detail surface
// (health stories, crashloops, failed services, config-integrity,
// health-history sparklines).
//
// These deliberately live alongside the components rather than in
// the shared `src/api/hooks.ts` so concurrent feature agents can
// land their changes without merge conflicts on the shared hook
// module. Each hook calls `fetcher` from `@/api/client` directly.
//
// The OpenAPI shapes for `/api/health/{stories,crashloops,
// config-integrity}` are declared with `additionalProperties: true`
// — the controller's actual response shape is documented inline
// below. Fields are typed loosely (`?`) where the controller may
// omit them.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// ---------------------------------------------------------------------------
// Health stories — narrative cards composed by the controller.
// ---------------------------------------------------------------------------

/** Severity ordering: critical > warn > info > ok. */
export type HealthStorySeverity = "critical" | "warn" | "info" | "ok";

/** One composite health story. Mirrors `Story` in
 * `media_stack/api/services/health_stories.py`. */
export interface HealthStory {
  id: string;
  severity: HealthStorySeverity | string;
  headline: string;
  description: string;
  affected_services?: readonly string[];
  cause?: string;
  /** "healing" | "healed_recently" | "needs_manual" | "n/a" */
  auto_heal_status?: string;
  next_action?: string;
}

export interface HealthStoriesResponse {
  stories: readonly HealthStory[];
  /** epoch seconds */
  checked_at?: number;
}

const STORIES_KEY = ["ops-detail", "health-stories"] as const;

export function useHealthStories(): UseQueryResult<HealthStoriesResponse> {
  return useQuery({
    queryKey: STORIES_KEY,
    queryFn: () => fetcher<HealthStoriesResponse>("api/health/stories"),
    refetchInterval: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Crashloops — per-service classification of containers in restart loops.
// ---------------------------------------------------------------------------

/** Mirrors `Classification.to_dict()` in
 * `media_stack/api/services/crashloop.py`. */
export interface CrashloopEntry {
  service_id?: string;
  restart_count?: number;
  /** snake_case classifier; "healthy" when fine. */
  cause?: string;
  description?: string;
  healable?: boolean;
  sample_log_line?: string;
  /** OOMKilled / Error / "" / ... */
  last_terminated_reason?: string;
  /** epoch seconds */
  checked_at?: number;
}

export interface CrashloopsResponse {
  /** Keyed by service id; entries inherit `service_id` redundantly. */
  services: Record<string, CrashloopEntry>;
  /** epoch seconds */
  checked_at?: number;
}

const CRASHLOOPS_KEY = ["ops-detail", "crashloops"] as const;

export function useCrashloops(): UseQueryResult<CrashloopsResponse> {
  return useQuery({
    queryKey: CRASHLOOPS_KEY,
    queryFn: () => fetcher<CrashloopsResponse>("api/health/crashloops"),
    refetchInterval: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Config integrity — per-service config-file probe.
// ---------------------------------------------------------------------------

/** Mirrors `IntegrityResult.to_dict()`. */
export interface IntegrityEntry {
  service_id?: string;
  /** "ok" | "corrupt" | "missing" | "unknown" | "skipped" */
  status?: string;
  file?: string;
  /** "xml" | "yaml" | "json" | "ini" | "sqlite" | "" */
  format?: string;
  reason?: string;
  /** epoch seconds */
  checked_at?: number;
}

export interface ConfigIntegrityResponse {
  services: Record<string, IntegrityEntry>;
  /** epoch seconds */
  checked_at?: number;
}

const INTEGRITY_KEY = ["ops-detail", "config-integrity"] as const;

export function useConfigIntegrity(): UseQueryResult<ConfigIntegrityResponse> {
  return useQuery({
    queryKey: INTEGRITY_KEY,
    queryFn: () =>
      fetcher<ConfigIntegrityResponse>("api/health/config-integrity"),
    refetchInterval: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Failed services — services that have tripped the failure threshold.
// ---------------------------------------------------------------------------

/**
 * The OpenAPI shape declares `failed_services: string[]` — but in
 * practice the controller's auto-heal layer also emits richer
 * objects via the same endpoint when more context is known. We
 * accept either form here; the consumer normalises.
 */
export type FailedServiceEntry =
  | string
  | {
      service_id?: string;
      service?: string;
      reason?: string;
      since?: string;
      /** epoch seconds */
      since_ts?: number;
    };

export interface FailedServicesResponse {
  failed_services: readonly FailedServiceEntry[];
  count?: number;
}

const FAILED_KEY = ["ops-detail", "failed-services"] as const;

export function useFailedServices(): UseQueryResult<FailedServicesResponse> {
  return useQuery({
    queryKey: FAILED_KEY,
    queryFn: () => fetcher<FailedServicesResponse>("api/failed-services"),
    refetchInterval: 30_000,
  });
}

// ---------------------------------------------------------------------------
// Health history — time-series for the sparkline.
// ---------------------------------------------------------------------------

/**
 * The controller exposes two related shapes on `/api/health-history`:
 *   - the OpenAPI sample (SLA-summary): `{ sla, period_hours, entries }`
 *   - the raw history list: `{ history: [{ts, services: {...}}], period_hours }`
 *
 * The SLA flavor is what `services/health.py::get_health_history`
 * currently returns; the buffer flavor is the on-disk format. We
 * parse both and reduce to a series of `{ts, ok_count, total_count}`
 * tuples for the sparkline.
 */
export interface HealthHistoryRawEntry {
  /** epoch seconds */
  ts?: number;
  services?: Record<string, { status?: string; ms?: number | null }>;
}

export interface HealthHistorySlaEntry {
  total?: number;
  ok?: number;
  uptime_pct?: number;
}

export interface HealthHistoryResponse {
  /** Raw per-tick samples (preferred for sparkline). */
  history?: readonly HealthHistoryRawEntry[];
  /** SLA roll-up (used as fallback aggregate). */
  sla?: Record<string, HealthHistorySlaEntry>;
  period_hours?: number;
  entries?: number;
}

const HISTORY_KEY = ["ops-detail", "health-history"] as const;

export function useHealthHistory(): UseQueryResult<HealthHistoryResponse> {
  return useQuery({
    queryKey: HISTORY_KEY,
    queryFn: () => fetcher<HealthHistoryResponse>("api/health-history"),
    refetchInterval: 60_000,
  });
}

export const opsDetailQueryKeys = {
  stories: STORIES_KEY,
  crashloops: CRASHLOOPS_KEY,
  configIntegrity: INTEGRITY_KEY,
  failedServices: FAILED_KEY,
  healthHistory: HISTORY_KEY,
} as const;
