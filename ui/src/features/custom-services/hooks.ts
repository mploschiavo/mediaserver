// Feature-local hooks for the Custom Services surface.
//
// `POST /api/custom-service` registers a non-standard service in the
// controller's registry. The OpenAPI request body is loose
// (`additionalProperties: true`) so we type defensively here and let
// extra fields pass through. There is no documented `GET` companion
// in the current spec; if one lands later it can plug in alongside.

import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";

const CUSTOM_SERVICES_KEY = ["custom-services"] as const;

/**
 * Body sent to `POST /api/custom-service`. The controller accepts
 * `additionalProperties: true`, but the dialog defines a stable
 * surface so callers pass the keys we render.
 */
export interface DefineCustomServiceInput {
  /** Lowercase slug, must match `[a-z0-9-]+`. */
  name: string;
  /** Container image reference, e.g. `linuxserver/foo:latest`. */
  image: string;
  /** Free-form port mapping like `8080:80` or `8080:80/tcp`. */
  ports?: string;
  /** Optional newline-separated `host:container` volume bindings. */
  volumes?: string;
  /** Optional newline-separated `KEY=value` env entries. */
  env?: string;
  /** Optional healthcheck command (passed through verbatim). */
  healthcheck?: string;
  [key: string]: unknown;
}

/**
 * Mutation: register a custom service. Invalidates the custom-services
 * cache key on success so a future read endpoint will repopulate.
 */
export function useDefineCustomService(): UseMutationResult<
  unknown,
  Error,
  DefineCustomServiceInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) =>
      fetcher<unknown>("api/custom-service", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: CUSTOM_SERVICES_KEY });
    },
  });
}

export const customServicesQueryKeys = {
  list: CUSTOM_SERVICES_KEY,
} as const;
