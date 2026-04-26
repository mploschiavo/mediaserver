// Shared admin-summary hook + type so multiple cards can subscribe
// without re-declaring the query (React Query dedupes by key, but
// the type definition lives in one place).
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetcher } from "@/api/client";

interface ClusterRow {
  name: string;
  hosts: number;
  healthy: number;
  added_via_api: boolean;
}

interface LatencyPercentiles {
  p50: number | null;
  p95: number | null;
  p99: number | null;
}

interface DownstreamBreakdown {
  total: number;
  rq_2xx: number;
  rq_4xx: number;
  rq_5xx: number;
}

export interface EnvoyAdminSummary {
  clusters: readonly ClusterRow[];
  request_totals: Record<string, number>;
  request_p_latency_ms: Record<string, LatencyPercentiles>;
  active_connections: Record<string, number>;
  downstream_breakdown: DownstreamBreakdown;
  tls_handshake_errors: number;
  clusters_error?: string;
  stats_error?: string;
  // Convenience extras for cards that don't want to drill back to
  // the routing config to render a header label.
  gateway_label?: string;
}

export const ENVOY_ADMIN_SUMMARY_KEY = ["routing", "envoy", "admin-summary"] as const;

export function useEnvoyAdminSummary(
  intervalMs: number = 30_000,
): UseQueryResult<EnvoyAdminSummary> {
  return useQuery<EnvoyAdminSummary>({
    queryKey: ENVOY_ADMIN_SUMMARY_KEY,
    queryFn: () => fetcher<EnvoyAdminSummary>("api/envoy/admin-summary"),
    refetchInterval: intervalMs,
    staleTime: Math.max(1_000, Math.floor(intervalMs / 2)),
  });
}
