// Feature-local hooks for the Discovery / Import Lists surface.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// ---- Import lists ---------------------------------------------------------

export interface ImportListEntry {
  id?: number;
  name?: string;
  enabled?: boolean;
  listType?: string;
  [key: string]: unknown;
}

/**
 * GET /api/import-lists is `{ lists: { sonarr: [...], radarr: [...] } }`.
 * /api/import-lists-all is loose (additionalProperties: true). We
 * accept either; consumers normalise via `flattenImportLists`.
 */
export interface ImportListsResponse {
  lists?: Record<string, readonly ImportListEntry[]>;
  /** Some payloads return service-keyed objects at the top level. */
  [key: string]: unknown;
}

const IMPORT_LISTS_KEY = ["discovery", "import-lists"] as const;
const IMPORT_LISTS_ALL_KEY = ["discovery", "import-lists-all"] as const;
const DISCOVERY_LISTS_KEY = ["discovery", "discovery-lists"] as const;
const POPULAR_TV_KEY = ["discovery", "popular-tv"] as const;

export function useImportLists(): UseQueryResult<ImportListsResponse> {
  return useQuery({
    queryKey: IMPORT_LISTS_KEY,
    queryFn: () => fetcher<ImportListsResponse>("api/import-lists"),
    staleTime: 60_000,
  });
}

export function useImportListsAll(): UseQueryResult<ImportListsResponse> {
  return useQuery({
    queryKey: IMPORT_LISTS_ALL_KEY,
    queryFn: () => fetcher<ImportListsResponse>("api/import-lists-all"),
    staleTime: 60_000,
  });
}

export interface ToggleImportListInput {
  service: string;
  listId: number;
  enabled: boolean;
}

export function useToggleImportList(): UseMutationResult<
  unknown,
  Error,
  ToggleImportListInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ service, listId, enabled }) =>
      fetcher<unknown>(
        `api/import-lists/${encodeURIComponent(service)}/${listId}/toggle`,
        {
          method: "POST",
          body: JSON.stringify({ enabled }),
        },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: IMPORT_LISTS_KEY });
      void qc.invalidateQueries({ queryKey: IMPORT_LISTS_ALL_KEY });
    },
  });
}

export interface DeleteImportListInput {
  service: string;
  listId: number;
}

export function useDeleteImportList(): UseMutationResult<
  unknown,
  Error,
  DeleteImportListInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ service, listId }) =>
      fetcher<unknown>(
        `api/import-lists/${encodeURIComponent(service)}/${listId}/delete`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: IMPORT_LISTS_KEY });
      void qc.invalidateQueries({ queryKey: IMPORT_LISTS_ALL_KEY });
    },
  });
}

// ---- Discovery lists / popular feeds --------------------------------------

export interface DiscoveryListsResponse {
  lists?: readonly Record<string, unknown>[];
  [key: string]: unknown;
}

export function useDiscoveryLists(): UseQueryResult<DiscoveryListsResponse> {
  return useQuery({
    queryKey: DISCOVERY_LISTS_KEY,
    queryFn: () => fetcher<DiscoveryListsResponse>("api/discovery-lists"),
    staleTime: 5 * 60_000,
  });
}

export interface PopularTvEntry {
  tvdbId?: number;
  title?: string;
  [key: string]: unknown;
}

export function usePopularTv(): UseQueryResult<readonly PopularTvEntry[]> {
  return useQuery({
    queryKey: POPULAR_TV_KEY,
    queryFn: () => fetcher<readonly PopularTvEntry[]>("api/discovery/popular-tv"),
    staleTime: 5 * 60_000,
  });
}

export const discoveryQueryKeys = {
  importLists: IMPORT_LISTS_KEY,
  importListsAll: IMPORT_LISTS_ALL_KEY,
  discoveryLists: DISCOVERY_LISTS_KEY,
  popularTv: POPULAR_TV_KEY,
} as const;

interface FlatListEntry {
  service: string;
  list: ImportListEntry;
}

/** Pull `{ service, list }` rows out of the loose payload. */
export function flattenImportLists(
  payload: ImportListsResponse | undefined,
): FlatListEntry[] {
  const out: FlatListEntry[] = [];
  if (!payload) return out;
  const source =
    payload.lists && typeof payload.lists === "object"
      ? payload.lists
      : payload;
  if (!source || typeof source !== "object") return out;
  for (const [key, value] of Object.entries(source)) {
    if (key === "lists") continue;
    if (!Array.isArray(value)) continue;
    for (const item of value as readonly ImportListEntry[]) {
      if (item && typeof item === "object") {
        out.push({ service: key, list: item });
      }
    }
  }
  return out;
}

interface PerServiceGroup {
  service: string;
  lists: readonly ImportListEntry[];
}

/** Group `flattenImportLists` rows by service. */
export function groupImportLists(
  payload: ImportListsResponse | undefined,
): PerServiceGroup[] {
  const flat = flattenImportLists(payload);
  const grouped = new Map<string, ImportListEntry[]>();
  for (const { service, list } of flat) {
    const arr = grouped.get(service) ?? [];
    arr.push(list);
    grouped.set(service, arr);
  }
  return Array.from(grouped.entries())
    .map(([service, lists]) => ({ service, lists }))
    .sort((a, b) => a.service.localeCompare(b.service));
}
