// Feature-local hooks for the abuse-defence security-signals surface.
//
// Three independent read-only queries:
//   - GET /api/security/failed-logins   → credential-stuffing clusters
//   - GET /api/security/new-locations   → first-seen-IP login alerts
//   - GET /api/security/concurrent      → per-user concurrent-session spikes
//
// The OpenAPI spec marks every payload as `additionalProperties: true`
// so shapes are hand-typed against the legacy `tab_security.js` reader
// (which has been the de facto contract since v1.0). Each field is
// optional; renderers narrow before reading.
//
// These hooks live alongside the cards rather than in `src/api/hooks.ts`
// so concurrent agents working on adjacent feature folders (`sessions`,
// `bans`, `audit-log`, etc.) can land their changes without merge
// conflicts on the shared hook module.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetcher } from "@/api/client";

const FAILED_LOGINS_PATH = "api/security/failed-logins";
const NEW_LOCATIONS_PATH = "api/security/new-locations";
const CONCURRENT_PATH = "api/security/concurrent";

const FAILED_LOGINS_KEY = ["security", "failed-logins"] as const;
const NEW_LOCATIONS_KEY = ["security", "new-locations"] as const;
const CONCURRENT_KEY = ["security", "concurrent"] as const;

/**
 * One bucket of failed-login attempts, typically keyed by an IP /24
 * prefix and surfaced when the count crosses a rate-limit threshold.
 */
export interface FailedLoginCluster {
  /** Cluster key — usually an IP /24 prefix; may also be a username. */
  ip_prefix?: string;
  /** Alternative identifier when the cluster groups by username. */
  username?: string;
  /** Distinct usernames attempted from inside this cluster. */
  usernames?: readonly string[];
  /** How many failed attempts contributed to the cluster. */
  attempt_count?: number;
  /** ISO timestamp of the first attempt in this cluster. */
  first_seen?: string;
  /** ISO timestamp of the most recent attempt. */
  last_seen?: string;
  [key: string]: unknown;
}

export interface FailedLoginsResponse {
  clusters: readonly FailedLoginCluster[];
}

/**
 * Single new-location alert: a known user signed in from an IP/geo
 * the system has never seen for them before.
 */
export interface NewLocationAlert {
  /** Username that triggered the alert. */
  username?: string;
  /** Provider that surfaced the login (Authelia / Jellyfin / ...). */
  provider?: string;
  /** Best-effort prior-IP/geo (may be empty when the user is brand new). */
  prior_ip?: string;
  prior_geo?: string;
  /** New-location IP /24 prefix returned by the controller. */
  ip_prefix?: string;
  /** New-location IP (full, when the controller is willing to share). */
  ip?: string;
  /** Optional new-location geo descriptor (city / region / country). */
  geo?: string;
  /** ISO timestamp the new-location login was observed. */
  observed_at?: string;
  [key: string]: unknown;
}

export interface NewLocationsResponse {
  alerts: readonly NewLocationAlert[];
}

/**
 * One user currently over the concurrent-session threshold —
 * e.g. five active sessions across three providers.
 */
export interface ConcurrentSpikeAlert {
  /** Username, when known. */
  username?: string;
  /** Active session count for this user. */
  count?: number;
  /** Threshold that was crossed (used for severity calculations). */
  threshold?: number;
  /** Providers where the sessions live (Authelia / Jellyfin / ...). */
  providers?: readonly string[];
  [key: string]: unknown;
}

export interface ConcurrentSpikesResponse {
  alerts: readonly ConcurrentSpikeAlert[];
}

export function useFailedLogins(): UseQueryResult<FailedLoginsResponse> {
  return useQuery({
    queryKey: FAILED_LOGINS_KEY,
    queryFn: () => fetcher<FailedLoginsResponse>(FAILED_LOGINS_PATH),
    refetchInterval: 30_000,
  });
}

export function useNewLocations(): UseQueryResult<NewLocationsResponse> {
  return useQuery({
    queryKey: NEW_LOCATIONS_KEY,
    queryFn: () => fetcher<NewLocationsResponse>(NEW_LOCATIONS_PATH),
    refetchInterval: 30_000,
  });
}

export function useConcurrentSpikes(): UseQueryResult<ConcurrentSpikesResponse> {
  return useQuery({
    queryKey: CONCURRENT_KEY,
    queryFn: () => fetcher<ConcurrentSpikesResponse>(CONCURRENT_PATH),
    refetchInterval: 30_000,
  });
}

export const securitySignalsQueryKeys = {
  failedLogins: FAILED_LOGINS_KEY,
  newLocations: NEW_LOCATIONS_KEY,
  concurrent: CONCURRENT_KEY,
} as const;
