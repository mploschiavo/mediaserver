// Feature-local hooks for the infrastructure-detail operator surface
// (GPU detection + enable, mounts list, storage breakdown, container
// image-update detection).
//
// These deliberately live alongside the components rather than in the
// shared `src/api/hooks.ts` so concurrent feature agents (auth-admin,
// password-policy, setup-wizard) can land their changes without merge
// conflicts on the shared hook module. Each hook calls `fetcher` from
// `@/api/client` directly.
//
// The OpenAPI shapes for these endpoints either declare richer fields
// than the v1.4.0 UI surface needs (`/api/gpu`, `/api/image-updates`)
// or are loosely typed (`/api/storage-breakdown`, declared
// `additionalProperties: true`). We type them defensively here — every
// list field is optional and consumers must coerce via `asArray()`.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// ---------------------------------------------------------------------------
// GPU detection — `/api/gpu` and `/api/gpu/enable`.
// ---------------------------------------------------------------------------

/**
 * One detected GPU as reported by the controller. Mirrors the entries
 * inside `GpuInfo.gpus[]` from the OpenAPI schema.
 *
 * The controller returns `type` strings like `intel/va-api`, `nvidia`,
 * etc. We don't enum-type it — the field is descriptive only, and a
 * future driver classification shouldn't break the UI.
 */
export interface GpuDevice {
  type?: string;
  name?: string;
  driver?: string;
  devices?: readonly string[];
  container?: string;
}

/**
 * `/api/gpu` response. The OpenAPI shape uses `gpus[]` + boolean flags
 * like `jellyfin_configured` / `jellyfin_has_gpu`; the v1.3.0 UI spec
 * also expected boolean shortcuts `intel_qsv` / `nvidia` for the badge
 * row. We accept both — `intel_qsv` and `nvidia` are derived from the
 * `gpus[]` types when missing.
 */
export interface GpuInfoResponse {
  detected?: boolean;
  gpus?: readonly GpuDevice[];
  /** v1.3.0-style shortcuts; not in current OpenAPI but accepted. */
  intel_qsv?: boolean;
  nvidia?: boolean;
  /** v1.3.0 named the device list flat under `devices`; we accept it. */
  devices?: readonly GpuDevice[];
  jellyfin_configured?: boolean;
  jellyfin_has_gpu?: boolean;
  host_os?: string;
  runtime?: string;
  hw_accel_type?: string;
  compose_snippet?: string;
  can_auto_configure?: boolean;
  note?: string;
}

export interface GpuEnableResponse {
  status?: string;
  hw_accel_type?: string;
  changes?: readonly string[];
  note?: string;
  backup?: string;
  error?: string;
  compose_snippet?: string;
}

const GPU_KEY = ["infra-detail", "gpu"] as const;

export function useGpu(): UseQueryResult<GpuInfoResponse> {
  return useQuery({
    queryKey: GPU_KEY,
    queryFn: () => fetcher<GpuInfoResponse>("api/gpu"),
    staleTime: 30_000,
  });
}

export function useEnableGpu(): UseMutationResult<
  GpuEnableResponse,
  Error,
  void
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      fetcher<GpuEnableResponse>("api/gpu/enable", { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: GPU_KEY });
    },
  });
}

// ---------------------------------------------------------------------------
// Mounts — `/api/mounts`.
// ---------------------------------------------------------------------------

/**
 * One filesystem mount. The OpenAPI shape exposes `device`,
 * `mountpoint`, `fstype`. The v1.3.0 UI surface expected richer fields
 * (`path`, `size`, `used`, `available`) — we accept both: `path`
 * falls back to `mountpoint`, and the size/used/available fields are
 * optional (the controller may emit them in newer builds; current
 * builds omit them).
 */
export interface MountEntry {
  /** Newer/expected field; older controllers return `mountpoint`. */
  path?: string;
  /** Legacy / OpenAPI canonical name. */
  mountpoint?: string;
  device?: string;
  fstype?: string;
  /** Total bytes — not in current OpenAPI but accepted. */
  size?: number;
  /** Used bytes. */
  used?: number;
  /** Available bytes. */
  available?: number;
}

export interface MountsResponse {
  mounts?: readonly MountEntry[];
  nfs_available?: boolean;
  cifs_available?: boolean;
}

const MOUNTS_KEY = ["infra-detail", "mounts"] as const;

export function useMounts(): UseQueryResult<MountsResponse> {
  return useQuery({
    queryKey: MOUNTS_KEY,
    queryFn: () => fetcher<MountsResponse>("api/mounts"),
    refetchInterval: 60_000,
  });
}

// ---------------------------------------------------------------------------
// Storage breakdown — `/api/storage-breakdown`.
// ---------------------------------------------------------------------------

/**
 * Per-library byte breakdown by file kind. The OpenAPI declares this
 * endpoint as `additionalProperties: true`, so the runtime shape is
 * documented inline below.
 *
 * Two shapes have shipped historically:
 *   1. Legacy keyed shape (v1.3.0 spec): top-level keys `movies`,
 *      `tv`, `tracks`, `books`, each holding `{bytes, files, by_kind}`.
 *   2. Live controller shape (v1.0.x backend, `disk.py
 *      ::get_storage_breakdown`): `{breakdown: [{name, path, bytes,
 *      display}], total_bytes, total_display, media_root}`. The list
 *      is generated by walking the configured MEDIA_ROOT, so it
 *      reflects whatever folders exist (Movies, TV Shows, Music,
 *      Audiobooks, Books, Anime, …) — not a fixed enum.
 *
 * The card normalises both into `{id, label, bytes}` rows; consumers
 * that only need one shape should still use the typed accessors here.
 */
export interface StorageBreakdownLibrary {
  /** Total bytes attributable to this library. */
  bytes?: number;
  /** File count, when the controller can compute it. */
  files?: number;
  /** Optional sub-kind breakdown. */
  by_kind?: Record<string, number>;
}

/**
 * One entry from the live controller's `breakdown[]` array (shape #2
 * above). `name` is the directory basename inside MEDIA_ROOT — e.g.
 * "Movies", "TV Shows", "Music", "Audiobooks". `display` is the
 * controller's pre-formatted size string ("4.2 GB"); the UI prefers
 * `bytes` and re-formats locally for unit consistency.
 */
export interface StorageBreakdownItem {
  name?: string;
  path?: string;
  bytes?: number;
  display?: string;
}

export interface StorageBreakdownResponse {
  /** Live controller shape — preferred when present. */
  breakdown?: readonly StorageBreakdownItem[];
  total_bytes?: number;
  total_display?: string;
  media_root?: string;
  error?: string;
  /** Legacy keyed shape — accepted for back-compat. */
  movies?: StorageBreakdownLibrary;
  tv?: StorageBreakdownLibrary;
  tracks?: StorageBreakdownLibrary;
  books?: StorageBreakdownLibrary;
  /** Allow forward-compat additional libraries (legacy shape). */
  [k: string]: unknown;
}

const STORAGE_KEY = ["infra-detail", "storage-breakdown"] as const;

export function useStorageBreakdown(): UseQueryResult<StorageBreakdownResponse> {
  return useQuery({
    queryKey: STORAGE_KEY,
    queryFn: () =>
      fetcher<StorageBreakdownResponse>("api/storage-breakdown"),
    refetchInterval: 5 * 60_000,
  });
}

// ---------------------------------------------------------------------------
// Image updates — `/api/image-updates`.
// ---------------------------------------------------------------------------

/**
 * One container image update entry. The v1.3.0 UI spec called the
 * fields `service`, `current`, `latest`, `available_at` — the
 * controller's actual OpenAPI uses `name` + `image` + `tag` +
 * `started_at` + `image_created`. We accept both shapes; the card
 * normalises.
 */
export interface ImageUpdateEntry {
  /** Service id. v1.3.0 shape used `service`; OpenAPI uses `name`. */
  service?: string;
  name?: string;
  /** Currently-running tag. v1.3.0 used `current`; OpenAPI uses `tag`. */
  current?: string;
  tag?: string;
  /** Latest tag available upstream. Newer field. */
  latest?: string;
  /** When the new tag became available. v1.3.0 used `available_at`. */
  available_at?: string;
  /** OpenAPI: when the upstream image was built. */
  image_created?: string;
  /** OpenAPI: full image reference. */
  image?: string;
  started_at?: string;
  digest?: string;
}

export interface ImageUpdatesResponse {
  /** v1.3.0 spec key. */
  updates?: readonly ImageUpdateEntry[];
  /** OpenAPI canonical key. */
  images?: readonly ImageUpdateEntry[];
  total?: number;
  pinned?: number;
}

const IMAGE_UPDATES_KEY = ["infra-detail", "image-updates"] as const;

export function useImageUpdates(): UseQueryResult<ImageUpdatesResponse> {
  return useQuery({
    queryKey: IMAGE_UPDATES_KEY,
    queryFn: () => fetcher<ImageUpdatesResponse>("api/image-updates"),
    refetchInterval: 5 * 60_000,
  });
}

// ---------------------------------------------------------------------------
// Manifests — `/api/manifests`.
// ---------------------------------------------------------------------------

/**
 * `/api/manifests` response. Surfaces the active deployment mode so
 * the UI chrome (TopBar stack-mode chip) can label the running
 * topology. The OpenAPI schema declares the canonical fields below;
 * the controller historically also emitted `project_name` for the
 * docker-compose case (under v1.3.x), so we accept it as an
 * optional alias for `namespace`.
 *
 * The v1.3.2 OpenAPI tightening locked the canonical `type` enum to
 * `"kubernetes" | "compose" | "bootstrap-config" | "compose-runtime"
 * | "unknown"` — but the prior dashboard talked about "docker" mode.
 * We accept both and let consumers map them to the user-facing
 * "Docker" label.
 */
export interface ManifestsResponse {
  type?:
    | "kubernetes"
    | "compose"
    | "compose-runtime"
    | "bootstrap-config"
    | "docker"
    | "unknown"
    | string;
  file?: string;
  content?: string | null;
  namespace?: string;
  /** v1.3.x docker-compose alias for `namespace`. */
  project_name?: string;
  deployments?: number;
  services?: readonly Record<string, unknown>[];
  error?: string;
  note?: string;
}

const MANIFESTS_KEY = ["stack-mode"] as const;

/**
 * `/api/manifests` returns the deployment manifest summary. The hook
 * keys the query as `["stack-mode"]` (not `["infra-detail",
 * "manifests"]`) because the v1.4.0 TopBar uses this hook for the
 * stack-mode chip — the cache key matches the spec'd surface.
 *
 * `staleTime` is 5 minutes; the deployment topology won't change at
 * runtime, so refetching aggressively wastes a request per poll.
 */
export function useManifests(): UseQueryResult<ManifestsResponse> {
  return useQuery({
    queryKey: MANIFESTS_KEY,
    queryFn: () => fetcher<ManifestsResponse>("api/manifests"),
    staleTime: 5 * 60_000,
    // The TopBar chip falls back to "no chip" on error — don't retry
    // on a bad response (the cards layout is what matters; chrome
    // gracefully degrades).
    retry: false,
  });
}

export const infraDetailQueryKeys = {
  gpu: GPU_KEY,
  mounts: MOUNTS_KEY,
  storageBreakdown: STORAGE_KEY,
  imageUpdates: IMAGE_UPDATES_KEY,
  manifests: MANIFESTS_KEY,
} as const;
