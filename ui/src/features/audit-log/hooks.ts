// Feature-local hooks for the audit-log observability surface.
//
// `useAuditLog` already lives in the shared `src/api/hooks.ts` (the
// list endpoint is part of the original v1 controller surface). The
// chain-head + verify endpoints are wave-2 additions; they live here
// so the parent api/hooks barrel stays stable while concurrent agents
// land sibling features (sessions, bans, /me, ...).
//
// Both hooks call `fetcher` from `@/api/client` directly — they
// inherit same-origin cookie threading, automatic Idempotency-Key
// generation on mutations, and 401 emission to the global auth bus.

import {
  useMutation,
  useQuery,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// Re-export the list hook so feature consumers can import everything
// from "./hooks" without reaching into the shared api barrel.
export { useAuditLog } from "@/api/hooks";

/**
 * Snapshot returned by `GET /api/audit-log/head`. The controller's
 * actual handler returns `{height, hash, ts, ok}` (see
 * `media_stack.core.auth.users.audit_log.AuditLog.head`). The
 * OpenAPI schema is `additionalProperties: true`, so we keep the
 * type permissive at the edges and only document the fields the UI
 * actually reads.
 */
export interface AuditLogHeadShape {
  /** Number of entries currently in the chain. */
  height: number;
  /** Last entry's sha256 hash; "" for an empty log. */
  hash: string;
  /** ISO-8601 timestamp of the most recent entry. */
  ts?: string;
  /** Always true on success — full chain verify is a separate call. */
  ok?: boolean;
}

/**
 * Result returned by `GET /api/audit-log/verify`. The controller's
 * handler returns `{ok, detail}` (string detail), where detail is a
 * human phrase like "entry 12: hash mismatch" on failure. The task
 * brief mentioned `broken_at`/`message` keys; the live shape is
 * `detail` only — we surface that as `message` and parse the entry
 * index out of the prefix when available.
 */
export interface AuditLogVerifyShape {
  ok: boolean;
  /** Server-provided detail string (empty / "hash chain intact" on success). */
  detail?: string;
}

const HEAD_KEY = ["audit-log", "head"] as const;

/**
 * Live query for the audit-log chain head. Refetches on a quiet
 * cadence so the integrity banner shows fresh height + hash without
 * hammering the server.
 */
export function useAuditLogHead(): UseQueryResult<AuditLogHeadShape> {
  return useQuery({
    queryKey: HEAD_KEY,
    queryFn: () => fetcher<AuditLogHeadShape>("api/audit-log/head"),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

/**
 * Click-triggered chain verifier. Modeled as a mutation rather than
 * a query so it only runs when the operator presses "Verify chain".
 * The check is O(n) server-side, so we don't want it firing on
 * every page mount.
 */
export function useAuditLogVerify(): UseMutationResult<
  AuditLogVerifyShape,
  Error,
  void
> {
  return useMutation({
    mutationFn: () =>
      // GET endpoint — no Idempotency-Key needed, but `fetcher`
      // skips the header automatically for non-mutating methods.
      fetcher<AuditLogVerifyShape>("api/audit-log/verify"),
  });
}

export const auditLogQueryKeys = {
  head: HEAD_KEY,
} as const;
