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
import { fetcher } from "./client";
import type {
  AuditLogShape,
  BrandingShape,
  EnforceReportShape,
  HealthShape,
  IdentityShape,
  LogSource,
  LogStreamShape,
  MediaIntegrityProgressShape,
  MediaIntegrityStatusShape,
  OpsHealthShape,
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
  logs: (source: LogSource) => ["logs", source] as const,
  routing: ["routing"] as const,
  webhooks: ["webhooks"] as const,
  users: ["users"] as const,
  opsHealth: ["ops", "health"] as const,
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

// ---- Cross-tab hooks ------------------------------------------------------
// These hooks call the live controller and adapt the response into the
// UI shapes declared in `shapes.ts`. Earlier revisions stubbed several
// of them with `Promise.resolve(...)` placeholders pending endpoints;
// the stub-hook ratchet (`stub-hook-ratchet.test.ts`) now blocks
// regressions.
//
// `useLibraryStats`, `useRecentAdditions`, `useMeProfile` were stubbed
// AND had no consumers — deleted in the burn-down. The /me page reads
// from `features/me/hooks.ts` (which calls /api/me directly); the
// /content page reads `useLibraries` (already wired). Re-introduce
// these only when a new consumer needs them.

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

/**
 * Live `/api/routing` shape — flat config blob from the controller's
 * routing service. We adapt to the strategy-only `RoutingShape` here
 * because the per-app health column the UI envisioned doesn't have
 * a backing endpoint yet (consumers fall back to /api/health for
 * that). When `/api/routing/dashboard` lands, expand `apps`.
 */
interface RoutingApiResponse {
  strategy?: string;
  base_domain?: string;
  gateway_host?: string;
  internet_exposed?: boolean;
}

export function useRouting(): UseQueryResult<RoutingShape> {
  return useQuery({
    queryKey: KEYS.routing,
    queryFn: async () => {
      const raw = await fetcher<RoutingApiResponse>("api/routing");
      const strategy = (raw.strategy === "subdomain" || raw.strategy === "path")
        ? raw.strategy
        : "hybrid";
      const adapted: RoutingShape = {
        strategy: {
          strategy,
          base_domain: raw.base_domain || "",
          external_hostname: raw.gateway_host || raw.base_domain || "",
        },
        apps: [],
      };
      return adapted;
    },
  });
}

/**
 * `/api/webhooks` returns ``{webhook_urls: string[]}`` — a flat list
 * of registered URLs. The UI's `WebhookEntryShape` expects per-entry
 * id/events/last_fired_at; until the controller tracks those (no
 * persistent registry yet), we synthesize id/events from the URL
 * itself so the table renders something useful instead of empty.
 */
interface WebhooksApiResponse {
  webhook_urls?: readonly string[];
}

export function useWebhooks(): UseQueryResult<WebhooksShape> {
  return useQuery({
    queryKey: KEYS.webhooks,
    queryFn: async () => {
      const raw = await fetcher<WebhooksApiResponse>("api/webhooks");
      const urls = raw.webhook_urls ?? [];
      return {
        webhooks: urls.map((url, idx) => ({
          id: String(idx),
          url,
          events: [],
        })),
      } as WebhooksShape;
    },
  });
}

/**
 * `/api/users` emits the canonical user record (``state``,
 * ``role_slug``, ``provider_refs``...). The UI's `UserEntryShape`
 * uses `status` and a narrowed `role` enum — adapt here. Aggregate
 * counts (``admins``, ``pending_invites``) are derived client-side
 * because the controller doesn't roll them up server-side yet.
 */
interface UserApiRecord {
  id: string;
  username: string;
  state?: string;
  role_slug?: string;
  last_login_at?: string;
}

interface UsersApiResponse {
  users?: readonly UserApiRecord[];
}

export function useUsers(): UseQueryResult<UsersShape> {
  return useQuery({
    queryKey: KEYS.users,
    queryFn: async () => {
      const raw = await fetcher<UsersApiResponse>("api/users");
      const users = (raw.users ?? []).map((u) => {
        const role: "admin" | "operator" | "viewer" = u.role_slug === "superadmin"
          ? "admin"
          : u.role_slug === "operator" ? "operator" : "viewer";
        const status: "active" | "disabled" | "pending" = u.state === "disabled"
          ? "disabled"
          : u.state === "pending" ? "pending" : "active";
        return {
          id: u.id,
          username: u.username,
          role,
          status,
          last_login_at: u.last_login_at || undefined,
        };
      });
      const admins = users.filter((u) => u.role === "admin").length;
      const pending = users.filter((u) => u.status === "pending").length;
      return { users, admins, pending_invites: pending } as UsersShape;
    },
  });
}

export function useOpsHealth(): UseQueryResult<OpsHealthShape> {
  return useQuery({
    queryKey: KEYS.opsHealth,
    queryFn: () => fetcher<OpsHealthShape>("api/ops/health"),
    refetchInterval: 30_000,
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
