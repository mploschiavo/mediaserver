// Feature-local hooks for the Library/Content surface.
//
// Lives alongside the components rather than in the shared
// `src/api/hooks.ts` so wave-3 feature agents can land their
// changes without colliding on the global hook module.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// ---- Library list ---------------------------------------------------------

/**
 * Configured (profile-side) library entry — the controller's
 * source of truth for what Jellyfin should expose. Always present
 * in the GET response; the operator-edit form mutates this list.
 */
export interface ConfiguredLibraryEntry {
  name: string;
  /** Jellyfin collection_type: movies/tvshows/music/books/... */
  collection_type: string;
  paths: readonly string[];
}

/**
 * Live (Jellyfin-reported) library entry. Empty until Jellyfin
 * has been bootstrapped and is reachable; the operator UI falls
 * back to `configured` for tile counts and library names.
 */
export interface LiveLibraryEntry {
  name?: string;
  collection_type?: string;
  paths?: readonly string[];
  item_count?: number;
  [key: string]: unknown;
}

/**
 * GET /api/libraries — `{live, configured, source, media_server}`.
 * Both arrays are always emitted (live may be empty); `source`
 * documents whether `configured` came from defaults / profile /
 * persisted overrides; `media_server` is the active backend.
 */
export interface LibrariesResponse {
  live: readonly LiveLibraryEntry[];
  configured: readonly ConfiguredLibraryEntry[];
  source: "defaults" | "profile" | "persisted" | string;
  media_server: "jellyfin" | "emby" | "plex" | string;
}

/**
 * Back-compat alias: older consumers grabbed `LibraryEntry` and
 * walked `entry.type` / `entry.kind` / `entry.path`. The new shape
 * keys those fields differently; we keep this union-ish type so
 * the table normaliser can hydrate from either side.
 */
export type LibraryEntry = ConfiguredLibraryEntry | LiveLibraryEntry;

const LIBRARIES_KEY = ["library", "libraries"] as const;
const RECENT_KEY = ["library", "recent-additions"] as const;
const CONFIG_LIBRARIES_KEY = ["library", "config"] as const;

export function useLibraries(): UseQueryResult<LibrariesResponse> {
  return useQuery({
    queryKey: LIBRARIES_KEY,
    queryFn: () => fetcher<LibrariesResponse>("api/libraries"),
    staleTime: 30_000,
  });
}

// ---- Add / update library -------------------------------------------------

export interface AddLibraryInput {
  name: string;
  /** "movies" | "tvshows" | "music" | "books" | custom. */
  collection_type: string;
  paths: readonly string[];
}

export function useAddLibrary(): UseMutationResult<
  unknown,
  Error,
  AddLibraryInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AddLibraryInput) =>
      fetcher<unknown>("api/libraries", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: LIBRARIES_KEY });
      void qc.invalidateQueries({ queryKey: CONFIG_LIBRARIES_KEY });
    },
  });
}

// ---- Recent additions -----------------------------------------------------

/**
 * GET /api/recent — `{ recent: { sonarr: [...], radarr: [...] } }`.
 * The wire shape is documented in the OpenAPI spec; we surface
 * the fields the dashboard widget needs and pass through the rest
 * via `additionalProperties: true` on the spec side.
 */
export interface RecentAdditionEntry {
  title?: string;
  added?: string;
  poster?: string;
  poster_url?: string;
  image?: string;
  service?: string;
  [key: string]: unknown;
}

/**
 * Service-keyed map under the top-level `recent` key. Known keys are
 * `radarr`, `sonarr`, `lidarr`, `readarr`; additional keys may appear
 * for newer *arr integrations.
 */
export interface RecentAdditionsRaw {
  recent?: Record<string, readonly RecentAdditionEntry[] | undefined>;
}

export function useRecentLibraryAdditions(): UseQueryResult<RecentAdditionsRaw> {
  return useQuery({
    queryKey: RECENT_KEY,
    queryFn: () => fetcher<RecentAdditionsRaw>("api/recent"),
    refetchInterval: 60_000,
  });
}

// ---- Config-side library declarations -------------------------------------

export interface ConfigLibrariesResponse {
  libraries?: readonly LibraryEntry[];
  [key: string]: unknown;
}

export function useConfigLibraries(): UseQueryResult<ConfigLibrariesResponse> {
  return useQuery({
    queryKey: CONFIG_LIBRARIES_KEY,
    queryFn: () => fetcher<ConfigLibrariesResponse>("api/config/libraries"),
    staleTime: 60_000,
  });
}

export const libraryQueryKeys = {
  libraries: LIBRARIES_KEY,
  recent: RECENT_KEY,
  configLibraries: CONFIG_LIBRARIES_KEY,
} as const;

export interface FlattenedRecentItem {
  id: string;
  title: string;
  service?: string;
  added?: string;
  poster?: string;
}

/**
 * Flatten the `/api/recent` per-service map into a single list,
 * sorted by `added` descending. Missing/empty timestamps sort to
 * the end so the freshest items rise to the top.
 *
 *     Object.entries(recent).flatMap(([service, items]) =>
 *       items.map(i => ({...i, service}))
 *     )
 *
 * `asArray` defends against a controller version that returns
 * `null` or an object map for one of the service buckets.
 */
export function flattenRecent(
  data: RecentAdditionsRaw | undefined,
  limit = 6,
): FlattenedRecentItem[] {
  if (!data || !data.recent || typeof data.recent !== "object") return [];

  const flat: FlattenedRecentItem[] = [];
  for (const [service, entries] of Object.entries(data.recent)) {
    const list = Array.isArray(entries)
      ? (entries as readonly RecentAdditionEntry[])
      : [];
    list.forEach((e, i) => {
      const title = typeof e.title === "string" ? e.title : "";
      if (!title) return;
      flat.push({
        id: `${service}-${i}-${title}`,
        title,
        service,
        added: typeof e.added === "string" && e.added ? e.added : undefined,
        poster:
          (typeof e.poster === "string" && e.poster) ||
          (typeof e.poster_url === "string" && e.poster_url) ||
          (typeof e.image === "string" && e.image) ||
          undefined,
      });
    });
  }

  // Sort by `added` desc; entries without a parseable timestamp
  // sort last so they don't clobber real recent items.
  flat.sort((a, b) => {
    const ta = a.added ? Date.parse(a.added) : NaN;
    const tb = b.added ? Date.parse(b.added) : NaN;
    const aValid = Number.isFinite(ta);
    const bValid = Number.isFinite(tb);
    if (aValid && bValid) return tb - ta;
    if (aValid) return -1;
    if (bValid) return 1;
    return 0;
  });

  return flat.slice(0, limit);
}
