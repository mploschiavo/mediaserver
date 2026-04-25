// Feature-local hooks for the active-sessions operator surface.
//
// These deliberately live alongside the SessionsTable component rather
// than the shared `src/api/hooks.ts` so concurrent agents working on
// adjacent features (`bans`, `emergency-revoke`, `/me`) can land their
// changes without merge conflicts on the shared hook module.
//
// Both hooks call `fetcher` from `@/api/client` directly. The shape
// below mirrors what `GET /api/sessions/active` actually returns
// across providers (controller + Authelia + Jellyfin + Jellyseerr +
// native admin) — fields are optional because not every provider
// fills every column.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

/** Provider that owns a session row. */
export type SessionProvider =
  | "authelia"
  | "jellyfin"
  | "jellyseerr"
  | "native"
  | string;

/**
 * Single active-session record. Optional because the OpenAPI schema is
 * `additionalProperties: true` and providers fill different subsets.
 */
export interface SessionShape {
  /** Stable identifier within the owning provider. */
  session_id?: string;
  /** Username string ("(anonymous)" when absent at render time). */
  username?: string;
  /** Originating provider — used to badge the row + key the revoke. */
  provider?: SessionProvider;
  /** IP address string. */
  client_ip?: string;
  /** Free-form user-agent / client banner. */
  user_agent?: string;
  /** Alternative client banner (e.g. "Jellyfin Web 10.9"). */
  client?: string;
  /** Device class hint ("TV" / "PHONE" / "DESKTOP" / ...). */
  device_class?: string;
  /** Device label, when known. */
  device?: string;
  /** ISO-8601 created/connected timestamp. */
  connected_since?: string;
  /** Alternative created-at timestamp from controller variants. */
  started_at?: string;
  /** ISO-8601 last-activity timestamp. */
  last_activity?: string;
  /** Alternative last-seen timestamp from controller variants. */
  last_seen_at?: string;
  /** "first seen at this IP" admin-review flag. */
  first_seen_ip?: boolean;
  /** Some providers (e.g. Jellyfin's read-only API key sessions) cannot be revoked. */
  revokable?: boolean;
}

export interface ActiveSessionsResponse {
  sessions: readonly SessionShape[];
}

const KEY = ["sessions", "active"] as const;

export function useActiveSessions(): UseQueryResult<ActiveSessionsResponse> {
  return useQuery({
    queryKey: KEY,
    queryFn: () => fetcher<ActiveSessionsResponse>("api/sessions/active"),
    refetchInterval: 15_000,
  });
}

export interface RevokeSessionInput {
  user_id: string;
  session_id: string;
  /** Provider hint forwarded to the controller in the body. */
  provider?: SessionProvider;
}

interface RevokeContext {
  previous?: ActiveSessionsResponse;
}

export function useRevokeSession(): UseMutationResult<
  unknown,
  Error,
  RevokeSessionInput,
  RevokeContext
> {
  const qc = useQueryClient();
  return useMutation<unknown, Error, RevokeSessionInput, RevokeContext>({
    mutationFn: ({ user_id, session_id, provider }) =>
      fetcher<unknown>(
        `api/users/${encodeURIComponent(user_id)}/sessions/${encodeURIComponent(session_id)}/revoke`,
        {
          method: "POST",
          body: provider ? JSON.stringify({ provider }) : undefined,
        },
      ),
    onMutate: async ({ session_id }) => {
      await qc.cancelQueries({ queryKey: KEY });
      const previous = qc.getQueryData<ActiveSessionsResponse>(KEY);
      if (previous) {
        qc.setQueryData<ActiveSessionsResponse>(KEY, {
          ...previous,
          sessions: previous.sessions.filter(
            (s) => s.session_id !== session_id,
          ),
        });
      }
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) {
        qc.setQueryData(KEY, context.previous);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: KEY });
    },
  });
}

export const sessionsQueryKey = KEY;
