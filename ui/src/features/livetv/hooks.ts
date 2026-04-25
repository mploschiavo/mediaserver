// Feature-local Tanstack Query hooks for the Live TV / IPTV / EPG
// operator surface.
//
// The shared `src/api/hooks.ts` and `src/api/endpoints.ts` modules are
// owned by sibling agents working in parallel; this feature wires its
// own `fetcher` calls so the four Live-TV cards can ship without
// touching the shared barrels.
//
// OpenAPI path mapping (verified against
// `src/media_stack/api/openapi.yaml` — every endpoint lives under the
// `/api/` prefix and the spec models all responses as
// `additionalProperties: true` objects, so the strict slices below are
// hand-typed and any list field is read through `asArray()` on the
// render path):
//
//   GET    /api/livetv-sources    -> { sources: LivetvSource[] }
//   POST   /api/livetv-sources    body: { sources: LivetvSource[] }
//   GET    /api/iptv-countries    -> { countries: IptvCountry[] }
//   GET    /api/epg-providers     -> { providers: EpgProvider[] }
//   GET    /api/epg-health        -> EpgHealthShape
//
// `fetcher` from `@/api/client` threads the session cookie, generates
// an Idempotency-Key on POST, and emits `unauthenticated` on 401 to
// the shared auth event bus.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// ===========================
// Hand-typed shapes
// ===========================

/**
 * One named tuner/guide URL — the controller models tuners (M3U
 * playlists) and guides (XMLTV EPG) as two parallel arrays under
 * `/api/livetv-sources`. The legacy `LivetvSource` shape that
 * paired them per-row is gone; consumers now iterate the two
 * arrays independently and reconcile them via `tuner_url` /
 * `guide_url` (the currently-selected pair).
 */
export interface LivetvUrlEntry {
  url: string;
  name: string;
  [key: string]: unknown;
}

export interface LivetvSourcesResponse {
  /** All known tuner (M3U) URLs. */
  tuners?: readonly LivetvUrlEntry[];
  /** All known guide (XMLTV EPG) URLs. */
  guides?: readonly LivetvUrlEntry[];
  /** Currently-selected tuner URL — must match an entry in `tuners[]`. */
  tuner_url?: string;
  /** Currently-selected guide URL — must match an entry in `guides[]`. */
  guide_url?: string;
  /** When true, Jellyfin loads every entry in `tuners[]` (multi-tuner mode). */
  load_all_tuners?: boolean;
  /** Where the configuration was loaded from. */
  source?: "profile" | "persisted" | "defaults" | string;
  [key: string]: unknown;
}

/**
 * Back-compat alias used by older imports. The new tuner/guide split
 * means the merged `LivetvSource` no longer matches the wire shape;
 * the alias resolves to `LivetvUrlEntry` so existing dialogs that
 * still type their state by this name compile during the rollout.
 */
export type LivetvSource = LivetvUrlEntry;

export interface IptvCountry {
  /** ISO-3166 alpha-2 in most controller builds. */
  code: string;
  name?: string;
  /**
   * M3U / IPTV playlist URL bundled by the controller for the
   * country. Live response field is ``tuner_url``; ``m3u_url`` is
   * an older alias kept for back-compat with profiles that still
   * persist the legacy name. Cards SHOULD prefer ``tuner_url`` —
   * the IPTV-Countries widget had a "browse-only on every row"
   * regression before consumers learned to fall through.
   */
  tuner_url?: string;
  m3u_url?: string;
  /** Bundled XMLTV EPG URL when available. */
  guide_url?: string;
  [key: string]: unknown;
}

export interface IptvCountriesResponse {
  countries?: readonly IptvCountry[];
  [key: string]: unknown;
}

export interface EpgProvider {
  id?: string;
  name: string;
  /**
   * Live shape (verified against the contract fixture): each provider
   * declares a parameterized URL pattern like
   * ``"https://iptv-epg.org/files/epg-{code}.xml"`` rather than a
   * plain base URL. The card renders this verbatim — earlier reads
   * of ``base_url`` always returned undefined and produced "—" for
   * every row.
   */
  url_template?: string;
  format?: string;
  priority?: number;
  enabled?: boolean;
  notes?: string;
  /** Some providers carry a real base URL too; keep optional. */
  base_url?: string;
  /** Open vs auth-required catalog flag — present on a few providers. */
  requires_auth?: boolean;
  [key: string]: unknown;
}

export interface EpgProvidersResponse {
  providers?: readonly EpgProvider[];
  [key: string]: unknown;
}

export interface EpgHealthShape {
  /**
   * Live `/api/epg-health` shape (verified 2026-04-25):
   *   ``{healthy, unhealthy, countries, providers, details: {...}}``
   * The earlier interface (``last_run``/``status``/``ok``) was an
   * aspirational contract that never landed — the card showed
   * "failing — last run never" because every legacy field was
   * undefined on the real payload. ``details`` is keyed by country
   * code, each entry being ``{provider_id: bool}``.
   */
  healthy?: number;
  unhealthy?: number;
  countries?: number;
  providers?: number;
  details?: Record<string, Record<string, boolean>>;
  /** Aspirational fields kept optional for forward compat. */
  last_run?: string;
  status?: string;
  ok?: boolean;
  errors?: readonly string[] | number;
  missing_channels?: readonly string[];
  [key: string]: unknown;
}

// ===========================
// Query keys
// ===========================

export const livetvKeys = {
  sources: ["livetv", "sources"] as const,
  iptvCountries: ["livetv", "iptv-countries"] as const,
  epgProviders: ["livetv", "epg-providers"] as const,
  epgHealth: ["livetv", "epg-health"] as const,
} as const;

// ===========================
// Hooks
// ===========================

export function useLivetvSources(): UseQueryResult<LivetvSourcesResponse> {
  return useQuery({
    queryKey: livetvKeys.sources,
    queryFn: () => fetcher<LivetvSourcesResponse>("api/livetv-sources"),
  });
}

/**
 * Body accepted by `POST /api/livetv-sources`. Tuners and guides
 * are persisted independently; pass either or both. The controller
 * treats whatever you send as the canonical list for that array.
 *
 * `tuner_url` / `guide_url` set the active pair — must reference
 * an entry in the corresponding array.
 */
export interface SaveLivetvSourcesInput {
  tuners?: readonly LivetvUrlEntry[];
  guides?: readonly LivetvUrlEntry[];
  tuner_url?: string;
  guide_url?: string;
  load_all_tuners?: boolean;
}

/**
 * Replace the live-TV tuner/guide configuration. The controller
 * treats the body as the canonical set for whichever arrays are
 * present; add/edit/delete all flow through this single mutation.
 */
export function useSaveLivetvSources(): UseMutationResult<
  LivetvSourcesResponse,
  Error,
  SaveLivetvSourcesInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<LivetvSourcesResponse>("api/livetv-sources", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: livetvKeys.sources });
      void qc.invalidateQueries({ queryKey: livetvKeys.epgHealth });
    },
  });
}

export function useIptvCountries(): UseQueryResult<IptvCountriesResponse> {
  return useQuery({
    queryKey: livetvKeys.iptvCountries,
    queryFn: () => fetcher<IptvCountriesResponse>("api/iptv-countries"),
    staleTime: 5 * 60_000,
  });
}

export function useEpgProviders(): UseQueryResult<EpgProvidersResponse> {
  return useQuery({
    queryKey: livetvKeys.epgProviders,
    queryFn: () => fetcher<EpgProvidersResponse>("api/epg-providers"),
    staleTime: 5 * 60_000,
  });
}

export function useEpgHealth(): UseQueryResult<EpgHealthShape> {
  return useQuery({
    queryKey: livetvKeys.epgHealth,
    queryFn: () => fetcher<EpgHealthShape>("api/epg-health"),
    refetchInterval: 30_000,
  });
}
