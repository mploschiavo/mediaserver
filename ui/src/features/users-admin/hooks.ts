// Tanstack Query hooks for the Users-admin feature surface.
//
// The shared `useUsers()` in `src/api/hooks.ts` is a stub that returns
// the directory list for the dashboard cards; this module covers
// everything else the v1.3.0 admin surface needs: user CRUD,
// role/state mutations, per-user sessions + login history, invites,
// roles, password policy, provider reconciliation, bulk import.
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

export interface AdminUser {
  id: string;
  username: string;
  email?: string;
  display_name?: string;
  role?: string;
  role_slug?: string;
  status?: "active" | "disabled" | "deleted" | "locked" | string;
  state?: string;
  created_at?: string;
  last_login_at?: string;
  avatar_url?: string;
  [key: string]: unknown;
}

export interface AdminUsersResponse {
  users: readonly AdminUser[];
  admins?: number;
  pending_invites?: number;
  [key: string]: unknown;
}

export interface AdminRole {
  slug: string;
  name?: string;
  description?: string;
  permissions?: readonly string[];
  grants?: readonly string[];
  [key: string]: unknown;
}

export interface RolesResponse {
  roles: readonly AdminRole[];
}

export interface AdminInvite {
  id: string;
  email?: string;
  role_slug?: string;
  token?: string;
  invite_url?: string;
  url?: string;
  expires_at?: string;
  created_at?: string;
  status?: string;
  [key: string]: unknown;
}

export interface InvitesResponse {
  invites: readonly AdminInvite[];
}

/**
 * The numeric + boolean values in `PasswordPolicyResponse.policy`.
 *
 * v1.3.3 expanded the wire shape to expose explicit booleans for
 * each character class, plus rotation + lockout knobs. The legacy
 * `require_classes` integer is kept on the read side (computed from
 * the booleans) for back-compat; on writes the booleans win and
 * `require_classes` is ignored.
 *
 * All fields are optional + tolerant; the defensive `fromResponse`
 * in `PasswordPolicyCard` falls back to `bounds.*.default`.
 */
export interface PasswordPolicyValues {
  min_length?: number;
  /** Legacy integer count — read-only, derived from the booleans. */
  require_classes?: number;
  require_uppercase?: boolean;
  require_lowercase?: boolean;
  require_digit?: boolean;
  require_special?: boolean;
  /** Recent passwords remembered + forbidden for re-use. */
  history_len?: number;
  /** Force rotation after N days; 0 = never. */
  max_age_days?: number;
  /** Failed-attempt count that triggers a lockout; 0 = disabled. */
  lockout_threshold?: number;
  /** Lockout duration in minutes. */
  lockout_window_minutes?: number;
  [key: string]: unknown;
}

export interface PolicyBound {
  floor: number;
  ceiling: number;
  default: number;
}

/**
 * `GET /api/password-policy` — `{ policy, bounds }`. The form fields
 * hydrate from `policy`; `bounds` drives `min`/`max`/`step` on the
 * sliders + numeric inputs.
 */
export interface PasswordPolicyResponse {
  policy: PasswordPolicyValues;
  bounds: {
    min_length?: PolicyBound;
    require_classes?: PolicyBound;
    history_len?: PolicyBound;
    max_age_days?: PolicyBound;
    lockout_threshold?: PolicyBound;
    lockout_window_minutes?: PolicyBound;
    [key: string]: PolicyBound | undefined;
  };
}

/**
 * Back-compat alias used by older callers that imported `PasswordPolicy`
 * as the response shape. Now points at the values-only object so
 * mutation bodies still type-check.
 */
export type PasswordPolicy = PasswordPolicyValues;

export interface UserSession {
  id: string;
  device?: string;
  ip?: string;
  user_agent?: string;
  started_at?: string;
  last_seen_at?: string;
  current?: boolean;
  [key: string]: unknown;
}

export interface UserSessionsResponse {
  sessions: readonly UserSession[];
}

export interface LoginHistoryEntry {
  ts?: string;
  ip?: string;
  user_agent?: string;
  result?: string;
  location?: string;
  is_first_seen?: boolean;
  [key: string]: unknown;
}

export interface LoginHistoryResponse {
  entries: readonly LoginHistoryEntry[];
}

export interface ReconcileDiff {
  user_id?: string;
  username?: string;
  email?: string;
  provider_name?: string;
  external_id?: string;
  kind?: "orphan" | "ghost" | "linked" | string;
  [key: string]: unknown;
}

export interface ReconcileResponse {
  diffs: readonly ReconcileDiff[];
}

export interface UserProvider {
  user_id?: string;
  username?: string;
  providers?: Record<string, { external_id?: string; status?: string } | string>;
  [key: string]: unknown;
}

export interface UserProvidersResponse {
  providers: readonly UserProvider[];
}

export interface CreateUserInput {
  username: string;
  email?: string;
  display_name?: string;
  role_slug: string;
  password?: string;
}

export interface PatchUserInput {
  user_id: string;
  body: Partial<{
    email: string;
    display_name: string;
    username: string;
  }>;
}

export interface SetUserRoleInput {
  user_id: string;
  role_slug: string;
}

export interface SetUserStateInput {
  user_id: string;
  state: "active" | "disabled" | "deleted" | "locked" | string;
}

export interface ResetUserPasswordInput {
  user_id: string;
  password?: string;
}

export interface BulkImportRow {
  username: string;
  email?: string;
  role_slug?: string;
  display_name?: string;
  [key: string]: unknown;
}

// =====================
// Query keys
// =====================

export const usersAdminKeys = {
  list: ["users-admin", "list"] as const,
  user: (id: string) => ["users-admin", "user", id] as const,
  sessions: (id: string) => ["users-admin", "sessions", id] as const,
  loginHistory: (id: string) =>
    ["users-admin", "login-history", id] as const,
  roles: ["users-admin", "roles"] as const,
  invites: ["users-admin", "invites"] as const,
  passwordPolicy: ["users-admin", "password-policy"] as const,
  reconcile: ["users-admin", "reconcile"] as const,
  providers: ["users-admin", "user-providers"] as const,
} as const;

// =====================
// Users
// =====================

export function useUsersAdmin(): UseQueryResult<AdminUsersResponse> {
  return useQuery({
    queryKey: usersAdminKeys.list,
    queryFn: () => fetcher<AdminUsersResponse>("api/users"),
  });
}

export function useAddUser(): UseMutationResult<
  AdminUser,
  Error,
  CreateUserInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<AdminUser>("api/users", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.list });
    },
  });
}

export function usePatchUser(): UseMutationResult<
  unknown,
  Error,
  PatchUserInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ user_id, body }) =>
      fetcher<unknown>(`api/users/${encodeURIComponent(user_id)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.list });
      void qc.invalidateQueries({
        queryKey: usersAdminKeys.user(vars.user_id),
      });
    },
  });
}

export function useDeleteUser(): UseMutationResult<
  unknown,
  Error,
  { user_id: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ user_id }) =>
      fetcher<unknown>(
        `api/users/${encodeURIComponent(user_id)}/delete`,
        { method: "POST", body: JSON.stringify({}) },
      ),
    // Optimistic update — drop the row from the cached list right
    // away so the table snaps even before the server replies.
    onMutate: async ({ user_id }) => {
      await qc.cancelQueries({ queryKey: usersAdminKeys.list });
      const prev = qc.getQueryData<AdminUsersResponse>(usersAdminKeys.list);
      if (prev) {
        qc.setQueryData<AdminUsersResponse>(usersAdminKeys.list, {
          ...prev,
          users: prev.users.filter((u) => u.id !== user_id),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, context) => {
      const c = context as { prev?: AdminUsersResponse } | undefined;
      if (c?.prev) qc.setQueryData(usersAdminKeys.list, c.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.list });
    },
  });
}

export function useResetUserPassword(): UseMutationResult<
  Record<string, unknown>,
  Error,
  ResetUserPasswordInput
> {
  return useMutation({
    mutationFn: ({ user_id, password }) =>
      fetcher<Record<string, unknown>>(
        `api/users/${encodeURIComponent(user_id)}/reset-password`,
        {
          method: "POST",
          body: JSON.stringify(password ? { password } : {}),
        },
      ),
  });
}

export function useSetUserRole(): UseMutationResult<
  unknown,
  Error,
  SetUserRoleInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ user_id, role_slug }) =>
      fetcher<unknown>(`api/users/${encodeURIComponent(user_id)}/role`, {
        method: "POST",
        body: JSON.stringify({ role_slug }),
      }),
    // Optimistic role swap.
    onMutate: async ({ user_id, role_slug }) => {
      await qc.cancelQueries({ queryKey: usersAdminKeys.list });
      const prev = qc.getQueryData<AdminUsersResponse>(usersAdminKeys.list);
      if (prev) {
        qc.setQueryData<AdminUsersResponse>(usersAdminKeys.list, {
          ...prev,
          users: prev.users.map((u) =>
            u.id === user_id ? { ...u, role: role_slug, role_slug } : u,
          ),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, context) => {
      const c = context as { prev?: AdminUsersResponse } | undefined;
      if (c?.prev) qc.setQueryData(usersAdminKeys.list, c.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.list });
    },
  });
}

export function useSetUserState(): UseMutationResult<
  unknown,
  Error,
  SetUserStateInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ user_id, state }) =>
      fetcher<unknown>(`api/users/${encodeURIComponent(user_id)}/state`, {
        method: "POST",
        body: JSON.stringify({ state }),
      }),
    onMutate: async ({ user_id, state }) => {
      await qc.cancelQueries({ queryKey: usersAdminKeys.list });
      const prev = qc.getQueryData<AdminUsersResponse>(usersAdminKeys.list);
      if (prev) {
        qc.setQueryData<AdminUsersResponse>(usersAdminKeys.list, {
          ...prev,
          users: prev.users.map((u) =>
            u.id === user_id ? { ...u, status: state, state } : u,
          ),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, context) => {
      const c = context as { prev?: AdminUsersResponse } | undefined;
      if (c?.prev) qc.setQueryData(usersAdminKeys.list, c.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.list });
    },
  });
}

export function useRevokeUserSessions(): UseMutationResult<
  unknown,
  Error,
  { user_id: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ user_id }) =>
      fetcher<unknown>(
        `api/users/${encodeURIComponent(user_id)}/revoke-sessions`,
        { method: "POST", body: JSON.stringify({}) },
      ),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({
        queryKey: usersAdminKeys.sessions(vars.user_id),
      });
    },
  });
}

export function useRevokeUserSession(): UseMutationResult<
  unknown,
  Error,
  { user_id: string; session_id: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ user_id, session_id }) =>
      fetcher<unknown>(
        `api/users/${encodeURIComponent(user_id)}/sessions/${encodeURIComponent(session_id)}/revoke`,
        { method: "POST", body: JSON.stringify({}) },
      ),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({
        queryKey: usersAdminKeys.sessions(vars.user_id),
      });
    },
  });
}

export function useUserSessions(
  user_id: string | undefined,
): UseQueryResult<UserSessionsResponse> {
  return useQuery({
    queryKey: usersAdminKeys.sessions(user_id ?? ""),
    queryFn: () =>
      fetcher<UserSessionsResponse>(
        `api/users/${encodeURIComponent(user_id as string)}/sessions`,
      ),
    enabled: Boolean(user_id),
  });
}

export function useUserLoginHistory(
  user_id: string | undefined,
): UseQueryResult<LoginHistoryResponse> {
  return useQuery({
    queryKey: usersAdminKeys.loginHistory(user_id ?? ""),
    queryFn: () =>
      fetcher<LoginHistoryResponse>(
        `api/users/${encodeURIComponent(user_id as string)}/login-history`,
      ),
    enabled: Boolean(user_id),
  });
}

// =====================
// Bulk import
// =====================

export function useBulkImportUsers(): UseMutationResult<
  Record<string, unknown>,
  Error,
  { rows: readonly BulkImportRow[] }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ rows }) =>
      fetcher<Record<string, unknown>>("api/users-bulk-import", {
        method: "POST",
        body: JSON.stringify({ rows }),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.list });
    },
  });
}

// =====================
// Roles
// =====================

export function useRoles(): UseQueryResult<RolesResponse> {
  return useQuery({
    queryKey: usersAdminKeys.roles,
    queryFn: () => fetcher<RolesResponse>("api/roles"),
  });
}

export function useUpdateRole(): UseMutationResult<
  unknown,
  Error,
  { role_slug: string; body: Record<string, unknown> }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ role_slug, body }) =>
      fetcher<unknown>(`api/roles/${encodeURIComponent(role_slug)}`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.roles });
    },
  });
}

// =====================
// Invites
// =====================

export function useInvites(): UseQueryResult<InvitesResponse> {
  return useQuery({
    queryKey: usersAdminKeys.invites,
    queryFn: () => fetcher<InvitesResponse>("api/invites"),
  });
}

export function useCreateInvite(): UseMutationResult<
  AdminInvite,
  Error,
  { email?: string; role_slug?: string; expires_at?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<AdminInvite>("api/invites", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.invites });
    },
  });
}

export function useRevokeInvite(): UseMutationResult<
  unknown,
  Error,
  { invite_id: string }
> {
  const qc = useQueryClient();
  return useMutation({
    // The OpenAPI lists `POST /api/invites/{invite_id}` for revoke;
    // a DELETE-equivalent is also accepted (per the spec's POST-as-
    // sudo convention). We use POST so existing CSRF gates apply.
    mutationFn: ({ invite_id }) =>
      fetcher<unknown>(
        `api/invites/${encodeURIComponent(invite_id)}`,
        { method: "POST", body: JSON.stringify({ revoke: true }) },
      ),
    onMutate: async ({ invite_id }) => {
      await qc.cancelQueries({ queryKey: usersAdminKeys.invites });
      const prev = qc.getQueryData<InvitesResponse>(usersAdminKeys.invites);
      if (prev) {
        qc.setQueryData<InvitesResponse>(usersAdminKeys.invites, {
          ...prev,
          invites: prev.invites.filter((i) => i.id !== invite_id),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, context) => {
      const c = context as { prev?: InvitesResponse } | undefined;
      if (c?.prev) qc.setQueryData(usersAdminKeys.invites, c.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.invites });
    },
  });
}

// =====================
// Password policy
// =====================

/**
 * `GET /api/password-policy` — returns the current policy values
 * AND their `floor`/`ceiling`/`default` bounds, so the UI can
 * hydrate `min`/`max` attributes on each numeric field instead of
 * hard-coding magic numbers.
 */
export function usePasswordPolicy(): UseQueryResult<PasswordPolicyResponse> {
  return useQuery({
    queryKey: usersAdminKeys.passwordPolicy,
    queryFn: () =>
      fetcher<PasswordPolicyResponse>("api/password-policy"),
  });
}

export function useUpdatePasswordPolicy(): UseMutationResult<
  unknown,
  Error,
  PasswordPolicyValues
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<unknown>("api/password-policy", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.passwordPolicy });
    },
  });
}

// =====================
// Provider reconciliation
// =====================

export function useUsersReconcile(): UseQueryResult<ReconcileResponse> {
  return useQuery({
    queryKey: usersAdminKeys.reconcile,
    queryFn: () => fetcher<ReconcileResponse>("api/users-reconcile"),
  });
}

export function useUserProviders(): UseQueryResult<UserProvidersResponse> {
  return useQuery({
    queryKey: usersAdminKeys.providers,
    queryFn: () => fetcher<UserProvidersResponse>("api/user-providers"),
  });
}

export function useImportOrphanUser(): UseMutationResult<
  unknown,
  Error,
  { provider_name: string; external_id: string; role_slug?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<unknown>("api/users-reconcile/import", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.reconcile });
      void qc.invalidateQueries({ queryKey: usersAdminKeys.providers });
      void qc.invalidateQueries({ queryKey: usersAdminKeys.list });
    },
  });
}

export function useUnlinkGhostUser(): UseMutationResult<
  unknown,
  Error,
  { user_id: string; provider_name: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<unknown>("api/users-reconcile/unlink", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: usersAdminKeys.reconcile });
      void qc.invalidateQueries({ queryKey: usersAdminKeys.providers });
    },
  });
}
