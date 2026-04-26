// Tanstack Query hooks for the Auth-admin feature surface.
//
// Restores the operator-facing auth configuration UI that the v1.3.0
// dashboard exposed via `dashboard.html` (auth mode picker, OIDC
// provider editor, per-service auth policies). The surface is
// intentionally feature-local — `src/api/hooks.ts` only exposes
// `useIdentity()`, which we leave untouched since the controller
// already wires it up.
//
// Each hook calls `fetcher` from `@/api/client` directly so it
// inherits same-origin cookies, auto-Idempotency-Key on mutations,
// and 401 emission to the global auth event bus.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// =====================
// Shapes (hand-typed)
// =====================
//
// The OpenAPI spec models all of these as `additionalProperties: true`
// objects, so we keep the strict slice the UI renders and let extra
// fields ride along on the wire.

/** Recognized auth mode values. The controller may extend this list,
 *  so the rendered options are always sourced from `useAuthModes()`
 *  rather than hard-coded into the UI. */
export type AuthMode =
  | "authelia"
  | "authelia+oidc"
  | "authentik"
  | "basic"
  | "none"
  | string;

/** Per-service policy. `native` means "fall through to the service's
 *  own auth"; the others map to Authelia's access-control verbs. */
export type ServicePolicy =
  | "bypass"
  | "one_factor"
  | "two_factor"
  | "native"
  | string;

/**
 * `GET /api/auth/config` — flattened to the keys the cards read.
 * `oidc_provider` is the singular active-provider key; the parameter
 * bag for it lives in `oidc_config`. Sibling fields (`per_service`,
 * `app_auth`, ...) round out the snapshot. Legacy `oidc_providers[]`
 * is kept in the type as `unknown` so older payloads still parse,
 * but the OIDC card now reads `oidc_provider` + `oidc_config`.
 */
export interface AuthConfig {
  mode?: AuthMode;
  internet_exposed?: boolean;
  /** Singular active OIDC provider key (e.g. "local" / "google" / id). */
  oidc_provider?: string;
  /** Provider-specific configuration for the active `oidc_provider`. */
  oidc_config?: Record<string, unknown>;
  /** Per-service auth-policy overrides. */
  per_service?: Record<string, unknown>;
  app_auth?: {
    enabled?: boolean;
    method?: string;
    required?: string;
    fail_on_error?: boolean;
    username_env?: string;
    password_env?: string;
    [key: string]: unknown;
  };
  app_auth_method?: string;
  app_auth_summary?: string;
  /** Legacy alias retained for older payload variants — unused by the new card. */
  service_policies?: Record<string, ServicePolicy>;
  [key: string]: unknown;
}

export interface AuthModesResponse {
  modes: readonly string[];
  [key: string]: unknown;
}

export interface OidcProvider {
  id: string;
  name?: string;
  client_id?: string;
  client_secret?: string;
  discovery_url?: string;
  issuer?: string;
  auth_url?: string;
  token_url?: string;
  userinfo_url?: string;
  jwks_url?: string;
  redirect_uris?: readonly string[];
  scopes?: readonly string[];
  [key: string]: unknown;
}

export interface OidcProvidersResponse {
  providers: readonly OidcProvider[];
  [key: string]: unknown;
}

export interface ParsedOidcConfig {
  issuer?: string;
  auth_url?: string;
  token_url?: string;
  userinfo_url?: string;
  jwks_url?: string;
  scopes_supported?: readonly string[];
  [key: string]: unknown;
}

export interface ServicePoliciesResponse {
  services: Record<string, ServicePolicy | { policy?: ServicePolicy } | unknown>;
  [key: string]: unknown;
}

// =====================
// Query keys
// =====================

export const authAdminKeys = {
  config: ["auth-admin", "config"] as const,
  modes: ["auth-admin", "modes"] as const,
  oidcProviders: ["auth-admin", "oidc-providers"] as const,
  servicePolicies: ["auth-admin", "service-policies"] as const,
} as const;

// =====================
// Auth config
// =====================

export function useAuthConfig(): UseQueryResult<AuthConfig> {
  return useQuery({
    queryKey: authAdminKeys.config,
    queryFn: () => fetcher<AuthConfig>("api/auth/config"),
  });
}

/**
 * Partial-merge update against `POST /api/auth/config`. The controller
 * accepts a sparse body; only the keys present in `body` are touched.
 *
 * Mode changes invalidate every active session, so callers should
 * confirm before invoking and surface the destructive consequence
 * in the UI.
 */
export function useUpdateAuthConfig(): UseMutationResult<
  AuthConfig,
  Error,
  Partial<AuthConfig>
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<AuthConfig>("api/auth/config", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: authAdminKeys.config });
      void qc.invalidateQueries({ queryKey: authAdminKeys.servicePolicies });
      void qc.invalidateQueries({ queryKey: authAdminKeys.oidcProviders });
    },
  });
}

// =====================
// Modes
// =====================

export function useAuthModes(): UseQueryResult<AuthModesResponse> {
  return useQuery({
    queryKey: authAdminKeys.modes,
    queryFn: () => fetcher<AuthModesResponse>("api/auth/modes"),
    staleTime: 5 * 60_000,
  });
}

// =====================
// OIDC providers
// =====================

export function useOidcProviders(): UseQueryResult<OidcProvidersResponse> {
  return useQuery({
    queryKey: authAdminKeys.oidcProviders,
    queryFn: () => fetcher<OidcProvidersResponse>("api/auth/oidc-providers"),
  });
}

/**
 * Parse a discovery URL (or pasted issuer JSON) via the controller's
 * dedicated parse endpoint. The controller returns the canonical
 * `{auth_url, token_url, userinfo_url, ...}` slice we splice into the
 * provider form so the operator can review before saving.
 *
 * Two paths:
 *   * ``discovery_url`` only → controller fetches the well-known URL
 *     server-side (avoids CORS / rate-limit issues with Google etc.),
 *     then routes the resulting JSON through the parser.
 *   * ``raw`` (or ``issuer``) → parses the pre-fetched JSON directly
 *     for operators pasting from a config dump.
 *
 * When both are present, server-side fetch wins (the URL is the
 * canonical source).
 */
export function useParseOidc(): UseMutationResult<
  ParsedOidcConfig,
  Error,
  { discovery_url?: string; issuer?: string; raw?: string | object }
> {
  return useMutation({
    mutationFn: async (body) => {
      // discovery_url path: probe via the controller (server-side
      // fetch), then send the resolved doc to the existing parser.
      if (body.discovery_url && !body.raw) {
        const probe = await fetcher<{
          ok: boolean;
          summary?: Record<string, unknown>;
          raw?: Record<string, unknown>;
          error?: string;
        }>("api/auth/oidc/probe", {
          method: "POST",
          body: JSON.stringify({ discovery_url: body.discovery_url }),
        });
        if (!probe.ok || !probe.raw) {
          throw new Error(
            probe.error ||
              "Discovery probe didn't return a valid OIDC config",
          );
        }
        return await fetcher<ParsedOidcConfig>("api/auth/parse-oidc", {
          method: "POST",
          body: JSON.stringify({
            discovery_url: body.discovery_url,
            raw: JSON.stringify(probe.raw),
          }),
        });
      }
      return await fetcher<ParsedOidcConfig>("api/auth/parse-oidc", {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
  });
}

// =====================
// Service policies
// =====================

export function useServicePolicies(): UseQueryResult<ServicePoliciesResponse> {
  return useQuery({
    queryKey: authAdminKeys.servicePolicies,
    queryFn: () => fetcher<ServicePoliciesResponse>("api/auth/service-policies"),
  });
}
