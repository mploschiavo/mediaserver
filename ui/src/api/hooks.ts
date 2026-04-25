// Tanstack Query hooks for the controller surface used by the
// dashboard. Each hook is intentionally small — just a wired-up
// `useQuery` / `useMutation` with cache invalidation on success.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { api } from "./endpoints";
import type {
  AuditLogShape,
  BrandingShape,
  EnforceReportShape,
  HealthShape,
  IdentityShape,
  LibraryStatsShape,
  LogSource,
  LogStreamShape,
  MeProfileShape,
  MediaIntegrityProgressShape,
  MediaIntegrityStatusShape,
  OpsHealthShape,
  RecentAdditionsShape,
  ReconcileReportShape,
  ResolveReviewInput,
  ResolveReviewOutput,
  RoutingShape,
  SessionsShape,
  UsersShape,
  WebhooksShape,
} from "./shapes";

const KEYS = {
  health: ["health"] as const,
  identity: ["auth", "identity"] as const,
  branding: ["branding"] as const,
  mediaIntegrity: ["media-integrity"] as const,
  mediaIntegrityStatus: ["media-integrity", "status"] as const,
  mediaIntegrityProgress: ["media-integrity", "progress"] as const,
  auditLog: (limit: number, action?: string) =>
    ["audit-log", { limit, action: action ?? null }] as const,
  sessions: ["sessions", "active"] as const,
  libraryStats: ["library", "stats"] as const,
  recentAdditions: ["library", "recent"] as const,
  logs: (source: LogSource) => ["logs", source] as const,
  routing: ["routing"] as const,
  webhooks: ["webhooks"] as const,
  users: ["users"] as const,
  opsHealth: ["ops", "health"] as const,
  meProfile: ["me", "profile"] as const,
};

export function useHealth(): UseQueryResult<HealthShape> {
  return useQuery({ queryKey: KEYS.health, queryFn: api.health });
}

export function useIdentity(): UseQueryResult<IdentityShape> {
  return useQuery({
    queryKey: KEYS.identity,
    queryFn: api.auth.identity,
    staleTime: 60_000,
  });
}

export function useBranding(): UseQueryResult<BrandingShape> {
  return useQuery({
    queryKey: KEYS.branding,
    queryFn: api.branding,
    staleTime: 5 * 60_000,
  });
}

export function useMediaIntegrityStatus(): UseQueryResult<MediaIntegrityStatusShape> {
  return useQuery({
    queryKey: KEYS.mediaIntegrityStatus,
    queryFn: api.mediaIntegrity.status,
  });
}

export function useMediaIntegrityProgress(
  enabled = true,
): UseQueryResult<MediaIntegrityProgressShape> {
  return useQuery({
    queryKey: KEYS.mediaIntegrityProgress,
    queryFn: api.mediaIntegrity.progress,
    enabled,
    // Poll fast while a pass is running, idle slowly otherwise.
    refetchInterval: (query) => {
      const data = query.state.data;
      return data?.in_progress ? 750 : 5_000;
    },
  });
}

export function useReconcile(): UseMutationResult<
  ReconcileReportShape,
  Error,
  { dryRun?: boolean; idempotencyKey?: string } | void
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars) => api.mediaIntegrity.reconcile(vars ?? undefined),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEYS.mediaIntegrity });
    },
  });
}

export function useEnforceConfig(): UseMutationResult<
  EnforceReportShape,
  Error,
  { idempotencyKey?: string } | void
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars) => api.mediaIntegrity.enforceConfig(vars ?? undefined),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEYS.mediaIntegrity });
    },
  });
}

export function useResolveReview(): UseMutationResult<
  ResolveReviewOutput,
  Error,
  { body: ResolveReviewInput; idempotencyKey?: string }
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ body, idempotencyKey }) =>
      api.mediaIntegrity.resolveReview(
        body,
        idempotencyKey !== undefined ? { idempotencyKey } : undefined,
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEYS.mediaIntegrity });
    },
  });
}

export function useAuditLog(opts?: {
  limit?: number;
  action?: string;
}): UseQueryResult<AuditLogShape> {
  const limit = opts?.limit ?? 50;
  return useQuery({
    queryKey: KEYS.auditLog(limit, opts?.action),
    queryFn: () =>
      api.auditLog(opts?.action ? { limit, action: opts.action } : { limit }),
  });
}

export function useSessions(): UseQueryResult<SessionsShape> {
  return useQuery({
    queryKey: KEYS.sessions,
    queryFn: api.sessions,
    refetchInterval: 15_000,
  });
}

// ---- Skeleton-tab hooks ---------------------------------------------------
// Each hook below renders real-shape data so the new dashboard tabs can
// flush out their visuals while the controller endpoints catch up. When
// the endpoint lands, replace the `Promise.resolve(...)` body with the
// matching `api.*` call and drop the TODO comment.

export function useLibraryStats(): UseQueryResult<LibraryStatsShape> {
  return useQuery({
    queryKey: KEYS.libraryStats,
    // TODO(api): GET /api/library/stats — counts per kind across the stack.
    queryFn: () =>
      Promise.resolve<LibraryStatsShape>({
        movies: 0,
        tv: 0,
        tracks: 0,
        books: 0,
      }),
  });
}

export function useRecentAdditions(
  limit = 6,
): UseQueryResult<RecentAdditionsShape> {
  return useQuery({
    queryKey: [...KEYS.recentAdditions, limit] as const,
    // TODO(api): GET /api/library/recent?window=24h&limit=N
    queryFn: () => Promise.resolve<RecentAdditionsShape>({ items: [] }),
  });
}

export function useLogs(
  source: LogSource | undefined,
): UseQueryResult<LogStreamShape> {
  return useQuery({
    queryKey: KEYS.logs(source ?? ("controller" as LogSource)),
    // TODO(api): switch to SSE/EventSource once api.logs is wired server-side.
    queryFn: () => api.logs(source as LogSource),
    enabled: source !== undefined,
    refetchInterval: 5_000,
    retry: false,
  });
}

export function useRouting(): UseQueryResult<RoutingShape> {
  return useQuery({
    queryKey: KEYS.routing,
    // TODO(api): GET /api/routing — strategy + per-app health.
    queryFn: () =>
      Promise.resolve<RoutingShape>({
        strategy: {
          strategy: "subdomain",
          base_domain: "media.local",
          external_hostname: "media.local",
        },
        apps: [],
      }),
  });
}

export function useWebhooks(): UseQueryResult<WebhooksShape> {
  return useQuery({
    queryKey: KEYS.webhooks,
    // TODO(api): GET /api/webhooks
    queryFn: () => Promise.resolve<WebhooksShape>({ webhooks: [] }),
  });
}

export function useUsers(): UseQueryResult<UsersShape> {
  return useQuery({
    queryKey: KEYS.users,
    // TODO(api): GET /api/users — paginated user directory.
    queryFn: () =>
      Promise.resolve<UsersShape>({ users: [], admins: 0, pending_invites: 0 }),
  });
}

export function useOpsHealth(): UseQueryResult<OpsHealthShape> {
  return useQuery({
    queryKey: KEYS.opsHealth,
    // TODO(api): GET /api/ops/health — aggregated runtime stats.
    queryFn: () =>
      Promise.resolve<OpsHealthShape>({
        uptime_seconds: 0,
        containers: 0,
        disk_used_pct: 0,
        last_bootstrap_at: new Date(0).toISOString(),
      }),
  });
}

export function useMeProfile(): UseQueryResult<MeProfileShape> {
  return useQuery({
    queryKey: KEYS.meProfile,
    // TODO(api): GET /api/me — sessions, tokens, MFA state.
    queryFn: () =>
      Promise.resolve<MeProfileShape>({
        username: "you",
        display_name: "You",
        email: "",
        sessions: [],
        tokens: [],
        mfa: { enabled: false },
      }),
  });
}

export type OpsActionKey =
  | "refreshServices"
  | "rotateKeys"
  | "pullManifests"
  | "healthProbe";

/**
 * Dispatch an ops-tab action. Only `rotateKeys` POSTs to the
 * controller; the other three are read-side and resolve by
 * re-fetching the relevant queries (the controller has no
 * dedicated POST endpoint for them — earlier shapes 404'd).
 */
export function useOpsAction(
  action: OpsActionKey,
): UseMutationResult<unknown, Error, void> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      if (action === "rotateKeys") {
        return api.ops.rotateKeys();
      }
      if (action === "pullManifests") {
        return api.ops.pullManifests();
      }
      // refreshServices / healthProbe — invalidate ops/health queries
      // and let React Query refetch.
      await queryClient.invalidateQueries({ queryKey: KEYS.opsHealth });
      await queryClient.invalidateQueries({ queryKey: KEYS.health });
      return undefined;
    },
  });
}

export const queryKeys = KEYS;
