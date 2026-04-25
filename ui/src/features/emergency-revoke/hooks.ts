// Feature-local mutation hook for the emergency "revoke every
// session in the deployment" break-glass action. Lives here rather
// than in `src/api/hooks.ts` because the action is an isolated,
// security-surface concern and the parent api/hooks barrel is owned
// by sibling agents shipping sessions/bans/me wiring in parallel.
//
// The hook wraps `fetcher` from the shared client so it inherits:
//   - same-origin cookie threading,
//   - automatic Idempotency-Key generation on POST,
//   - 401 emission to the global auth event bus (the layout shell
//     listens and decides what to do — this file does not navigate).

import { useMutation, type UseMutationResult } from "@tanstack/react-query";
import { fetcher } from "@/api/client";

const EMERGENCY_REVOKE_PATH = "api/emergency-revoke-all";

export interface EmergencyRevokeAllInput {
  reason: string;
}

// The controller's response is opaque (`additionalProperties: true`)
// in the planned OpenAPI shape; consumers of the mutation only need
// to know whether the call succeeded, so we keep the type permissive.
export type EmergencyRevokeAllResult = Record<string, unknown>;

/**
 * `useEmergencyRevokeAll` — POSTs to `/api/emergency-revoke-all`
 * with a JSON body `{ reason }`. The server logs the reason in the
 * audit trail along with the operator's identity. There is no
 * cache invalidation on success because every signed-in user
 * (including the operator) is about to be bounced out — the next
 * page load will refetch everything from scratch.
 */
export function useEmergencyRevokeAll(): UseMutationResult<
  EmergencyRevokeAllResult,
  Error,
  EmergencyRevokeAllInput
> {
  return useMutation({
    mutationFn: ({ reason }) =>
      fetcher<EmergencyRevokeAllResult>(EMERGENCY_REVOKE_PATH, {
        method: "POST",
        body: JSON.stringify({ reason }),
      }),
  });
}
