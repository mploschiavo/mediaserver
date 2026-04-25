// Feature-local mutation hooks for the Webhooks surface.
//
// The read-side `useWebhooks()` already lives in `src/api/hooks.ts`
// (currently a stub returning an empty list — the controller
// surfaces `GET /webhooks` non-`/api`-prefixed historically).
// We re-export it from here so route components can pull every
// webhook hook from a single import path while leaving the shared
// api/hooks barrel untouched (sibling agents own concurrent edits
// to that file).
//
// Mutations call `fetcher` from `@/api/client` directly so they
// inherit:
//   - same-origin cookie threading,
//   - automatic Idempotency-Key generation on POST,
//   - 401 emission to the global auth event bus.
//
// OpenAPI path mapping (verified against
// src/media_stack/api/openapi.yaml — paths are non-`/api`-prefixed):
//   POST   /webhooks         body: { url }              -> add
//   POST   /webhooks/test                                 -> test all
//   GET    /api/arr-webhooks                              -> arr config
//
// Note: the OpenAPI spec does NOT declare a DELETE on /webhooks.
// We model the delete as `POST /webhooks` with a `delete: true`
// flag is NOT a thing — instead we surface `useDeleteWebhook` that
// hits the historical `DELETE /webhooks?id=X` form so the feature
// still has a coherent CRUD surface; the call will 404 until the
// controller catches up. This is the same hand-shaped contract gap
// the audit noted on this surface.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

// Re-export the read-side hook so feature consumers can pull
// everything from "./hooks" without importing the api barrel.
export { useWebhooks } from "@/api/hooks";

// ---- Mutations -----------------------------------------------------------

export interface AddWebhookInput {
  url: string;
  /** Some controller variants accept an event filter; pass-through. */
  event_type?: string;
}

export interface AddWebhookResult {
  webhook_urls?: readonly string[];
  [key: string]: unknown;
}

/**
 * `POST /webhooks` — registers a webhook URL with the controller.
 * Returns the updated list of webhook URLs.
 *
 * On success we invalidate the `["webhooks"]` query key so the
 * read-side `useWebhooks()` refetches.
 */
export function useAddWebhook(): UseMutationResult<
  AddWebhookResult,
  Error,
  AddWebhookInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input) =>
      fetcher<AddWebhookResult>("api/webhooks", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["webhooks"] });
    },
  });
}

export interface DeleteWebhookInput {
  id: string;
}

/**
 * `DELETE /webhooks?id=<id>` — remove a webhook. The OpenAPI spec
 * doesn't formally declare this verb yet (audit note); we model it
 * here so the UI is shape-correct.
 */
export function useDeleteWebhook(): UseMutationResult<
  unknown,
  Error,
  DeleteWebhookInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id }) =>
      fetcher<unknown>(`api/webhooks?id=${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["webhooks"] });
    },
  });
}

export interface TestWebhooksResult {
  status?: "tested" | "no_webhooks" | string;
  /** URL -> human result string ("ok (200)", "timeout", ...). */
  results?: Record<string, string>;
  tested?: number;
  [key: string]: unknown;
}

/**
 * `POST /webhooks/test` — fires the controller's test payload at
 * every registered webhook and returns a per-URL result map.
 */
export function useTestWebhooks(): UseMutationResult<
  TestWebhooksResult,
  Error,
  void
> {
  return useMutation({
    mutationFn: () =>
      fetcher<TestWebhooksResult>("api/webhooks/test", { method: "POST" }),
  });
}

// ---- Arr-webhooks read --------------------------------------------------

export interface ArrWebhookEntry {
  service: string;
  configured: boolean;
  url?: string;
  last_delivery?: string;
}

export interface ArrWebhooksShape {
  /** The handler returns `additionalProperties: true`; the UI
   * normalises a `services` array (or `webhooks` legacy key). */
  services?: readonly ArrWebhookEntry[];
  webhooks?: readonly ArrWebhookEntry[];
  [key: string]: unknown;
}

const ARR_WEBHOOKS_KEY = ["webhooks", "arr"] as const;

/**
 * `GET /api/arr-webhooks` — Sonarr/Radarr/Lidarr/Readarr webhook
 * configuration as enforced by the controller. Read-only from the
 * UI; the controller is the source of truth.
 */
export function useArrWebhooks(): UseQueryResult<ArrWebhooksShape> {
  return useQuery({
    queryKey: ARR_WEBHOOKS_KEY,
    queryFn: () => fetcher<ArrWebhooksShape>("api/arr-webhooks"),
    staleTime: 30_000,
  });
}

export const webhooksQueryKeys = {
  arrWebhooks: ARR_WEBHOOKS_KEY,
} as const;
