// Feature-local hooks for the /me route. Each hook wraps a GET or
// mutation against the controller's `/api/me` + `/api/tokens` surface
// using the shared `fetcher` from `@/api/client`. These hooks are kept
// here (rather than in `src/api/hooks.ts`) so the /me feature can
// iterate independently while the shared hooks file is being reworked
// by neighboring agents.
//
// Backend reference: contracts/api/openapi.yaml under the
// `Me`, `Tokens`, and `Users` tags.

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

export interface MeProfile {
  id?: string;
  username?: string;
  display_name?: string;
  email?: string;
  role?: string;
  role_slug?: string;
  avatar_url?: string;
  last_login_at?: string;
  [key: string]: unknown;
}

export interface MeSession {
  session_id?: string;
  id?: string;
  provider?: string;
  device?: string;
  device_class?: string;
  client?: string;
  client_ip?: string;
  ip?: string;
  user_agent?: string;
  connected_since?: string;
  started_at?: string;
  last_activity?: string;
  last_seen_at?: string;
  current?: boolean;
  // Synth caller-rows under SSO arrive with revokable=false; the UI
  // suppresses sign-out affordances on those rows since the cookie
  // is owned by Authelia (not the controller). See
  // ``_synth_caller_session`` in ``security_get_handlers.py``.
  revokable?: boolean;
  [key: string]: unknown;
}

export interface MeSessionsResponse {
  sessions: readonly MeSession[];
  current_session_id?: string;
  [key: string]: unknown;
}

export interface MeToken {
  id?: string;
  token_id?: string;
  name?: string;
  provider?: string;
  scopes?: readonly string[];
  prefix?: string;
  created_at?: string;
  last_used_at?: string;
  expires_at?: string;
  [key: string]: unknown;
}

export interface MeTokensResponse {
  tokens: readonly MeToken[];
  [key: string]: unknown;
}

export interface MfaFactor {
  type?: string;
  label?: string;
  enrolled_at?: string;
  last_used_at?: string;
  [key: string]: unknown;
}

export interface MeMfaState {
  enabled?: boolean;
  enrolled?: boolean;
  required?: boolean;
  factors?: readonly MfaFactor[];
  enrolled_methods?: readonly string[];
  last_used_at?: string;
  last_used_method?: string;
  [key: string]: unknown;
}

export interface LoginHistoryEntry {
  id?: string;
  timestamp?: string;
  ts?: string;
  action?: string;
  result?: string;
  ip?: string;
  user_agent?: string;
  location?: string;
  detail?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface LoginHistoryResponse {
  entries: readonly LoginHistoryEntry[];
  [key: string]: unknown;
}

export interface RevokeOthersResponse {
  revoked?: number;
  [key: string]: unknown;
}

export interface ThisWasntMeInput {
  session_id?: string;
  audit_id?: string;
  login_timestamp?: string;
  flagged_ip?: string;
}

export interface GenerateTokenInput {
  name: string;
  scopes?: readonly string[];
  expires_at?: string;
  [key: string]: unknown;
}

export interface GenerateTokenResponse {
  id?: string;
  token_id?: string;
  token?: string;
  access_token?: string;
  secret?: string;
  raw?: string;
  name?: string;
  scopes?: readonly string[];
  created_at?: string;
  expires_at?: string;
  [key: string]: unknown;
}

// ---- Query keys ---------------------------------------------------------

export const meKeys = {
  me: ["me", "profile"] as const,
  sessions: ["me", "sessions"] as const,
  tokens: ["me", "tokens"] as const,
  mfa: ["me", "mfa-state"] as const,
  loginHistory: (userId?: string) =>
    ["me", "login-history", userId ?? null] as const,
};

// ---- Queries ------------------------------------------------------------

export function useMe(): UseQueryResult<MeProfile> {
  return useQuery({
    queryKey: meKeys.me,
    queryFn: () => fetcher<MeProfile>("api/me"),
    staleTime: 60_000,
  });
}

export function useMeSessions(): UseQueryResult<MeSessionsResponse> {
  return useQuery({
    queryKey: meKeys.sessions,
    queryFn: () => fetcher<MeSessionsResponse>("api/me/sessions"),
    refetchInterval: 30_000,
  });
}

export function useMeTokens(): UseQueryResult<MeTokensResponse> {
  return useQuery({
    queryKey: meKeys.tokens,
    queryFn: () => fetcher<MeTokensResponse>("api/me/tokens"),
  });
}

export function useMeMfaState(): UseQueryResult<MeMfaState> {
  return useQuery({
    queryKey: meKeys.mfa,
    queryFn: () => fetcher<MeMfaState>("api/me/mfa-state"),
    staleTime: 60_000,
  });
}

/**
 * Login history for the authenticated caller. The controller exposes
 * `/api/me/login-history` (registered in the session-visibility GET
 * route table) which scopes the audit-log query to the resolved
 * actor's username — no `user_id` parameter needed and no admin role
 * required. The earlier admin-keyed `/api/users/{user_id}/login-
 * history` shape doesn't work for self-service: it filtered audit
 * entries by `target == user_id`, but login events store `target =
 * username`, so the UUID `user_id` never matched anything.
 *
 * The `userId` argument is retained on the hook so existing callers
 * compile, but it now only gates the query (so we don't fire before
 * the `/api/me` profile has loaded) and is folded into the query key
 * to keep React Query's cache scoped correctly.
 */
export function useMeLoginHistory(
  userId: string | undefined,
): UseQueryResult<LoginHistoryResponse> {
  return useQuery({
    queryKey: meKeys.loginHistory(userId),
    queryFn: () => fetcher<LoginHistoryResponse>("api/me/login-history"),
    enabled: typeof userId === "string" && userId.length > 0,
  });
}

// ---- Mutations ----------------------------------------------------------

export function useRevokeOthers(): UseMutationResult<
  RevokeOthersResponse,
  Error,
  void
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      fetcher<RevokeOthersResponse>("api/me/revoke-others", {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: meKeys.sessions });
    },
  });
}

export function useThisWasntMe(): UseMutationResult<
  unknown,
  Error,
  ThisWasntMeInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<unknown>("api/me/this-wasnt-me", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: meKeys.sessions });
      void qc.invalidateQueries({ queryKey: ["me", "login-history"] });
    },
  });
}

/**
 * POST /api/tokens — the raw token is returned once in the response
 * body. Callers must display it to the user immediately and then
 * discard it from memory when the "I've stored it" dismiss button
 * is pressed. We never cache the token anywhere.
 */
export function useGenerateToken(): UseMutationResult<
  GenerateTokenResponse,
  Error,
  GenerateTokenInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input) =>
      fetcher<GenerateTokenResponse>("api/tokens", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: meKeys.tokens });
    },
  });
}

/**
 * The OpenAPI spec declares `POST /api/tokens/{token_id}` as the
 * revoke endpoint (not DELETE). We POST, consistent with the spec.
 */
export function useRevokeToken(): UseMutationResult<unknown, Error, string> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tokenId) =>
      fetcher<unknown>(`api/tokens/${encodeURIComponent(tokenId)}`, {
        method: "POST",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: meKeys.tokens });
    },
  });
}

/**
 * Revoke a single session on the caller. `/api/me/sessions/{id}/revoke`
 * does not exist in the spec; the admin-scoped sibling
 * `/api/users/{user_id}/sessions/{session_id}/revoke` does, and it
 * accepts the caller revoking one of their own sessions.
 */
export function useRevokeMySession(): UseMutationResult<
  unknown,
  Error,
  { userId: string; sessionId: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, sessionId }) =>
      fetcher<unknown>(
        `api/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sessionId)}/revoke`,
        { method: "POST" },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: meKeys.sessions });
    },
  });
}
