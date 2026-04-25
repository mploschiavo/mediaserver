// Feature-local hooks for the Quality Profiles surface (per-Servarr
// service). The OpenAPI shapes use `additionalProperties: true`; we
// type defensively and narrow at the consumer site.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

export type QualityService = "sonarr" | "radarr" | "lidarr" | "readarr";

export const QUALITY_SERVICES: readonly QualityService[] = [
  "sonarr",
  "radarr",
  "lidarr",
  "readarr",
] as const;

export interface QualityProfileEntry {
  id?: number;
  name?: string;
  /** Some payloads expose enabled/active flags; default to true. */
  enabled?: boolean;
  active?: boolean;
  [key: string]: unknown;
}

/**
 * GET /api/quality-profiles/{service} returns a loose object. The
 * controller returns either `{ profiles: [...] }` or the raw upstream
 * array. We normalise into `entries`.
 */
export interface QualityProfilesPayload {
  profiles?: readonly QualityProfileEntry[];
  [key: string]: unknown;
}

const profilesKey = (service: QualityService) =>
  ["quality-profiles", service] as const;

const PRESETS_KEY = ["quality-profiles", "presets"] as const;

export function useQualityProfiles(
  service: QualityService,
): UseQueryResult<QualityProfilesPayload> {
  return useQuery({
    queryKey: profilesKey(service),
    queryFn: () =>
      fetcher<QualityProfilesPayload>(
        `api/quality-profiles/${encodeURIComponent(service)}`,
      ),
    staleTime: 60_000,
  });
}

export interface ToggleQualityProfileInput {
  service: QualityService;
  profileId: number;
  enabled: boolean;
}

export function useToggleQualityProfile(): UseMutationResult<
  unknown,
  Error,
  ToggleQualityProfileInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ service, profileId, enabled }) =>
      fetcher<unknown>("api/quality-profiles/toggle", {
        method: "POST",
        body: JSON.stringify({ service, profile_id: profileId, enabled }),
      }),
    onSuccess: (_data, variables) => {
      void qc.invalidateQueries({
        queryKey: profilesKey(variables.service),
      });
    },
  });
}

export interface QualityPresetsResponse {
  presets?: Record<string, unknown>;
  [key: string]: unknown;
}

export function useQualityPresets(): UseQueryResult<QualityPresetsResponse> {
  return useQuery({
    queryKey: PRESETS_KEY,
    queryFn: () => fetcher<QualityPresetsResponse>("api/quality-presets"),
    staleTime: 5 * 60_000,
  });
}

/** Pull a usable list of profile rows out of the loose payload. */
export function readProfiles(
  payload: QualityProfilesPayload | undefined,
): QualityProfileEntry[] {
  if (!payload) return [];
  if (Array.isArray(payload.profiles)) {
    return payload.profiles.filter(
      (p): p is QualityProfileEntry => p !== null && typeof p === "object",
    );
  }
  return [];
}

export const qualityProfileQueryKeys = {
  list: profilesKey,
  presets: PRESETS_KEY,
} as const;
