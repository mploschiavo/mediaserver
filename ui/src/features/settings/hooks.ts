// Feature-local hooks for the /settings route. Each hook wraps a
// GET / mutation against the controller's settings surface using
// the shared `fetcher` from `@/api/client`. Kept here (not in the
// shared `src/api/hooks.ts`) so the settings feature can iterate
// independently of neighboring agents — see the wave-4 sibling
// agents for `auth-admin`, `infra-detail`, `stack-lifecycle`.
//
// Backend reference: src/media_stack/api/openapi.yaml under the
// `Profile`, `Env`, `Config`, `Display`, and `Logs` tags.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { fetcher } from "@/api/client";

// ---- Shape types --------------------------------------------------------
// The OpenAPI spec declares most of these as `additionalProperties: true`,
// so we use permissive hand-types. Every field is optional; components
// guard individually and fall back to a placeholder.

export interface ProfileResponse {
  /** Raw YAML string. The server is the source of truth for parsing. */
  yaml?: string;
  content?: string;
  updated_at?: string;
  saved_at?: string;
  [key: string]: unknown;
}

export interface ProfileSaveInput {
  yaml: string;
}

export interface EnvEntry {
  key?: string;
  name?: string;
  value?: string;
  source?: string;
  [key: string]: unknown;
}

export interface EnvResponse {
  env?: readonly EnvEntry[];
  values?: Record<string, string>;
  [key: string]: unknown;
}

export interface EnvVarEntry {
  key?: string;
  name?: string;
  value?: string;
  description?: string;
  sensitive?: boolean;
  [key: string]: unknown;
}

export interface EnvVarsResponse {
  vars?: readonly EnvVarEntry[];
  env_vars?: readonly EnvVarEntry[];
  [key: string]: unknown;
}

export interface DriftEntry {
  key?: string;
  path?: string;
  profile_value?: unknown;
  live_value?: unknown;
  severity?: "info" | "warn" | "error" | string;
  [key: string]: unknown;
}

export interface ConfigDriftResponse {
  drift?: readonly DriftEntry[];
  entries?: readonly DriftEntry[];
  [key: string]: unknown;
}

/**
 * `GET /api/display-preferences` — Jellyfin client display knobs.
 *
 * NOTE: Despite the name, this is **not** the controller dashboard's
 * theme/density. These are server-side preferences pushed to the
 * Jellyfin clients (web/emby) — backdrops, home-section ordering,
 * per-library sort defaults. The dashboard's own theme lives in
 * `next-themes` + `localStorage`; there is no controller endpoint
 * for it (and there shouldn't be — it's per-browser state).
 */
export interface DisplayPreferences {
  enabled?: boolean;
  show_backdrop?: boolean;
  /** Free-form Jellyfin client knobs (homesection0..9, etc.). */
  custom_prefs?: Record<string, unknown>;
  /** Per-library Jellyfin display prefs keyed by library kind. */
  per_library_prefs?: Record<string, Record<string, unknown>>;
  /** Jellyfin client identifiers the prefs apply to. */
  clients?: readonly string[];
  [key: string]: unknown;
}

export interface LogLevelResponse {
  level?: "debug" | "info" | "warn" | "error" | string;
  [key: string]: unknown;
}

export interface LogLevelInput {
  level: "debug" | "info" | "warn" | "error";
}

// ---- Query keys ---------------------------------------------------------

export const settingsKeys = {
  profile: ["settings", "profile"] as const,
  env: ["settings", "env"] as const,
  envVars: ["settings", "envvars"] as const,
  drift: ["settings", "config-drift"] as const,
  displayPrefs: ["settings", "display-preferences"] as const,
  logLevel: ["settings", "log-level"] as const,
};

// ---- Queries ------------------------------------------------------------

export function useProfileYaml(): UseQueryResult<ProfileResponse> {
  return useQuery({
    queryKey: settingsKeys.profile,
    queryFn: () => fetcher<ProfileResponse>("api/profile"),
    staleTime: 60_000,
  });
}

export function useEffectiveEnv(): UseQueryResult<EnvResponse> {
  return useQuery({
    queryKey: settingsKeys.env,
    queryFn: () => fetcher<EnvResponse>("api/env"),
    staleTime: 60_000,
  });
}

export function useEnvVars(): UseQueryResult<EnvVarsResponse> {
  return useQuery({
    queryKey: settingsKeys.envVars,
    queryFn: () => fetcher<EnvVarsResponse>("api/envvars"),
  });
}

export function useConfigDrift(): UseQueryResult<ConfigDriftResponse> {
  return useQuery({
    queryKey: settingsKeys.drift,
    queryFn: () => fetcher<ConfigDriftResponse>("api/config-drift"),
    refetchInterval: 60_000,
  });
}

export function useDisplayPreferences(): UseQueryResult<DisplayPreferences> {
  return useQuery({
    queryKey: settingsKeys.displayPrefs,
    queryFn: () => fetcher<DisplayPreferences>("api/display-preferences"),
    staleTime: 60_000,
  });
}

export function useLogLevel(): UseQueryResult<LogLevelResponse> {
  return useQuery({
    queryKey: settingsKeys.logLevel,
    queryFn: () => fetcher<LogLevelResponse>("api/log-level"),
    staleTime: 30_000,
  });
}

// ---- Mutations ----------------------------------------------------------

export function useSaveProfile(): UseMutationResult<
  ProfileResponse,
  Error,
  ProfileSaveInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input) =>
      fetcher<ProfileResponse>("api/profile", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: settingsKeys.profile });
      // Drift is computed from profile + live state — invalidate too.
      void qc.invalidateQueries({ queryKey: settingsKeys.drift });
    },
  });
}

export function useSaveDisplayPreferences(): UseMutationResult<
  DisplayPreferences,
  Error,
  DisplayPreferences
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<DisplayPreferences>("api/display-preferences", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: settingsKeys.displayPrefs });
    },
  });
}

export function useSetLogLevel(): UseMutationResult<
  LogLevelResponse,
  Error,
  LogLevelInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<LogLevelResponse>("api/log-level", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: settingsKeys.logLevel });
    },
  });
}

/**
 * Mask sensitive env values in render. The check is a simple
 * substring scan (case-insensitive) — sufficient for the spec's
 * stated keys (PASSWORD, SECRET, KEY, TOKEN). Pure helper, kept
 * here so the cards + tests can share the same matcher.
 */
export function isSensitiveKey(key: string): boolean {
  if (!key) return false;
  const upper = key.toUpperCase();
  return (
    upper.includes("PASSWORD") ||
    upper.includes("SECRET") ||
    upper.includes("KEY") ||
    upper.includes("TOKEN")
  );
}
