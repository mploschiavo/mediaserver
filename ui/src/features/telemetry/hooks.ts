// Feature-local hooks for the /settings -> Telemetry surface.
//
// Backend reference: src/media_stack/api/openapi.yaml only
// declares `GET /api/telemetry` (operationId: getTelemetry).
// The audit asked for opt-in/out, so we POST to the same path
// for consent updates — the controller has accepted POST on
// `additionalProperties: true` payloads on every other Config
// endpoint, so this matches the wider pattern. If the server
// later rejects POST we fall through to a clean ApiError that
// the card surfaces as a toast — safer than silently dropping.
//
// asArray() from `@/lib/coerce` is used to normalise the
// `categories` list payload (the spec leaves the response as
// `additionalProperties: true`, so the field shape is loose).

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { fetcher } from "@/api";
import { asArray } from "@/lib/coerce";

/** Consent levels the operator can pick from. */
export type TelemetryConsentLevel = "none" | "minimal" | "standard" | "full";

/**
 * Telemetry preferences blob. The shape is intentionally loose —
 * the OpenAPI declares `additionalProperties: true` so we accept
 * either explicit fields or a free-form dictionary.
 */
export interface TelemetryPreferences {
  consent?: TelemetryConsentLevel | string;
  /** Categories the operator opted into. Names are server-driven. */
  categories?: readonly string[];
  /** ISO timestamp of last consent update. */
  updated_at?: string;
  [key: string]: unknown;
}

export interface TelemetrySaveInput {
  consent: TelemetryConsentLevel;
  categories: readonly string[];
}

export const telemetryKeys = {
  preferences: ["telemetry", "preferences"] as const,
};

export function useTelemetry(): UseQueryResult<TelemetryPreferences> {
  return useQuery({
    queryKey: telemetryKeys.preferences,
    queryFn: () => fetcher<TelemetryPreferences>("api/telemetry"),
    staleTime: 60_000,
  });
}

export function useSaveTelemetry(): UseMutationResult<
  TelemetryPreferences,
  Error,
  TelemetrySaveInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<TelemetryPreferences>("api/telemetry", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: telemetryKeys.preferences });
    },
  });
}

/** Pick the consent level out of a loosely-typed payload. */
export function pickConsentLevel(
  prefs: TelemetryPreferences | undefined,
): TelemetryConsentLevel {
  const raw =
    typeof prefs?.consent === "string" ? prefs.consent.toLowerCase() : "";
  if (
    raw === "none" ||
    raw === "minimal" ||
    raw === "standard" ||
    raw === "full"
  ) {
    return raw;
  }
  return "none";
}

/** Coerce the categories list defensively. */
export function pickCategories(
  prefs: TelemetryPreferences | undefined,
): readonly string[] {
  return asArray<unknown>(prefs?.categories)
    .filter((v): v is string => typeof v === "string")
    .map((v) => v);
}
