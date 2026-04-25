// Feature-local hooks for the Custom Formats surface (per Servarr
// service). The OpenAPI spec exposes:
//   - GET  /api/custom-formats/{service}     (loose object response)
//   - POST /api/custom-formats/import        (loose body)
// We type defensively and use `asArray()` on every payload so a
// non-array response renders an empty list instead of throwing.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher } from "@/api/client";
import { asArray } from "@/lib/coerce";

export type CustomFormatService = "sonarr" | "radarr" | "lidarr" | "readarr";

export const CUSTOM_FORMAT_SERVICES: readonly CustomFormatService[] = [
  "sonarr",
  "radarr",
  "lidarr",
  "readarr",
] as const;

export interface CustomFormatEntry {
  id?: number;
  name?: string;
  /** TRaSH-Guides-style hash slug, surfaced when present. */
  trash_id?: string;
  [key: string]: unknown;
}

export interface CustomFormatsPayload {
  /** Some payloads return an array directly, others wrap in `formats`. */
  formats?: readonly CustomFormatEntry[];
  [key: string]: unknown;
}

const formatsKey = (service: CustomFormatService) =>
  ["custom-formats", service] as const;

const IMPORT_KEY = ["custom-formats", "import"] as const;

export function useCustomFormats(
  service: CustomFormatService,
): UseQueryResult<CustomFormatsPayload> {
  return useQuery({
    queryKey: formatsKey(service),
    queryFn: () =>
      fetcher<CustomFormatsPayload>(
        `api/custom-formats/${encodeURIComponent(service)}`,
      ),
    staleTime: 60_000,
  });
}

export interface ImportCustomFormatsInput {
  service: CustomFormatService;
  /** JSON paste body (TRaSH-Guides shape); the client `JSON.parse`s before send. */
  content: string;
}

export function useImportCustomFormats(): UseMutationResult<
  unknown,
  Error,
  ImportCustomFormatsInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: IMPORT_KEY,
    mutationFn: ({ service, content }) => {
      // The textarea holds raw JSON the user pasted. Parse here so a
      // bad paste fails fast (and surfaces as a mutation error) instead
      // of forwarding gibberish to the controller.
      const parsed = JSON.parse(content) as unknown;
      const body = {
        service,
        // Common upstream wrappings: `{ formats: [...] }` or a bare array.
        // Forward whichever shape the user pasted; the controller is
        // permissive (additionalProperties: true).
        ...(Array.isArray(parsed) ? { formats: parsed } : { payload: parsed }),
      };
      return fetcher<unknown>("api/custom-formats/import", {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
    onSuccess: (_data, variables) => {
      void qc.invalidateQueries({ queryKey: formatsKey(variables.service) });
    },
  });
}

/** Pull a usable list of format rows out of the loose payload. */
export function readFormats(
  payload: CustomFormatsPayload | undefined,
): CustomFormatEntry[] {
  if (!payload) return [];
  // Wrapper shape: { formats: [...] }
  const wrapped = asArray<CustomFormatEntry>(payload.formats);
  if (wrapped.length > 0) return [...wrapped];
  // Bare-array shape: the response itself is the array (rare but
  // documented in some controller revisions). asArray handles the
  // non-array case by returning [].
  const bare = asArray<CustomFormatEntry>(payload as unknown);
  return bare.filter(
    (f): f is CustomFormatEntry => f !== null && typeof f === "object",
  );
}

export const customFormatsQueryKeys = {
  list: formatsKey,
  import: IMPORT_KEY,
} as const;
