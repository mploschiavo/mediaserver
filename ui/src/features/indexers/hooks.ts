// Feature-local hooks for the Indexers surface (Prowlarr-backed).

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// ---- Indexer list ---------------------------------------------------------

export interface IndexerEntry {
  id: number;
  name: string;
  enable?: boolean;
  protocol?: string;
  [key: string]: unknown;
}

export interface IndexersResponse {
  indexers: readonly IndexerEntry[];
  total?: number;
  enabled?: number;
}

const INDEXERS_KEY = ["indexers", "list"] as const;
const INDEXER_STATS_KEY = ["indexers", "stats"] as const;

export function useIndexers(): UseQueryResult<IndexersResponse> {
  return useQuery({
    queryKey: INDEXERS_KEY,
    queryFn: () => fetcher<IndexersResponse>("api/indexers"),
    staleTime: 30_000,
  });
}

// ---- Indexer stats --------------------------------------------------------

/**
 * GET /api/indexer-stats — `{ stats: [{...}] }`. Schema is loose
 * (additionalProperties: true). The known Prowlarr fields are below.
 */
export interface IndexerStatEntry {
  indexerId?: number;
  indexerName?: string;
  numberOfQueries?: number;
  numberOfGrabs?: number;
  numberOfRssQueries?: number;
  numberOfFailedQueries?: number;
  numberOfFailedGrabs?: number;
  averageResponseTime?: number;
  lastError?: string;
  [key: string]: unknown;
}

export interface IndexerStatsResponse {
  stats: readonly IndexerStatEntry[];
}

export function useIndexerStats(): UseQueryResult<IndexerStatsResponse> {
  return useQuery({
    queryKey: INDEXER_STATS_KEY,
    queryFn: () => fetcher<IndexerStatsResponse>("api/indexer-stats"),
    staleTime: 30_000,
  });
}

// ---- Toggle / delete ------------------------------------------------------

export function useToggleIndexer(): UseMutationResult<
  unknown,
  Error,
  { indexerId: number; enable: boolean }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ indexerId, enable }) =>
      fetcher<unknown>(`api/indexers/${indexerId}/toggle`, {
        method: "POST",
        body: JSON.stringify({ enable }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: INDEXERS_KEY });
    },
  });
}

export function useDeleteIndexer(): UseMutationResult<
  unknown,
  Error,
  { indexerId: number }
> {
  const qc = useQueryClient();
  return useMutation({
    // The OpenAPI spec tunnels DELETE through a POST (see spec note).
    mutationFn: ({ indexerId }) =>
      fetcher<unknown>(`api/indexers/${indexerId}`, {
        method: "POST",
        body: JSON.stringify({ _method: "DELETE" }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: INDEXERS_KEY });
      void qc.invalidateQueries({ queryKey: INDEXER_STATS_KEY });
    },
  });
}

export const indexerQueryKeys = {
  list: INDEXERS_KEY,
  stats: INDEXER_STATS_KEY,
} as const;

/** Build a map from indexer id → its stats row. */
export function statsById(
  stats: IndexerStatsResponse | undefined,
): Map<number, IndexerStatEntry> {
  const map = new Map<number, IndexerStatEntry>();
  if (!stats?.stats) return map;
  for (const s of stats.stats) {
    if (typeof s.indexerId === "number") map.set(s.indexerId, s);
  }
  return map;
}
