// Feature-local hooks for the Snapshots / Backup / Restore surface.
//
// Endpoints (verified against contracts/api/openapi.yaml):
//   GET    /api/snapshots                 -> list config snapshots
//   POST   /api/snapshot                  -> take a snapshot now
//   GET    /api/snapshots/{filename}      -> read one snapshot's content
//   GET    /api/snapshot-diff?a=&b=       -> diff two snapshots
//   GET    /api/backup                    -> download a JSON backup
//   POST   /api/restore                   -> restore from a backup payload
//
// Spec note: the OpenAPI spec models `/api/backup` as JSON (not a
// ZIP) and `/api/restore` as a JSON `{service_configs}` body (not a
// multipart upload). The brief described ZIP+multipart; we follow
// the spec verbatim. The restore mutation accepts the parsed file
// (a `File` from an `<input type=file>`) and we read+JSON.parse it
// client-side before posting.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { fetcher, getBaseUrl } from "@/api/client";

// ---- Shapes -------------------------------------------------------------

export interface SnapshotEntry {
  file: string;
  size: number;
  created: string;
}

export interface SnapshotsListShape {
  snapshots: readonly SnapshotEntry[];
  dir?: string;
}

export interface SnapshotContentShape {
  /** Map of relative config path -> file content (as a string). */
  snapshot: Record<string, string>;
  file: string;
}

export interface SnapshotDiffEntry {
  file: string;
  status: "changed" | "added" | "removed";
}

export interface SnapshotDiffShape {
  diffs: readonly SnapshotDiffEntry[];
  file_a?: string;
  file_b?: string;
  total_changes?: number;
}

export interface TakeSnapshotResult {
  status: "created";
  file: string;
  configs: number;
}

export interface RestoreResult {
  status: "ok" | "partial" | string;
  restored?: readonly string[];
  errors?: readonly string[];
  note?: string;
}

// ---- Query keys ---------------------------------------------------------

const KEYS = {
  list: ["snapshots"] as const,
  content: (filename: string) =>
    ["snapshots", "content", filename] as const,
  diff: (a: string, b: string) =>
    ["snapshots", "diff", a, b] as const,
};

// ---- Read hooks ---------------------------------------------------------

export function useSnapshots(): UseQueryResult<SnapshotsListShape> {
  return useQuery({
    queryKey: KEYS.list,
    queryFn: () => fetcher<SnapshotsListShape>("api/snapshots"),
    staleTime: 15_000,
  });
}

export function useSnapshotContent(
  filename: string | undefined,
): UseQueryResult<SnapshotContentShape> {
  return useQuery({
    queryKey: KEYS.content(filename ?? ""),
    queryFn: () =>
      fetcher<SnapshotContentShape>(
        `api/snapshots/${encodeURIComponent(filename as string)}`,
      ),
    enabled: typeof filename === "string" && filename.length > 0,
    staleTime: 5 * 60_000,
  });
}

export function useSnapshotDiff(
  a: string | undefined,
  b: string | undefined,
): UseQueryResult<SnapshotDiffShape> {
  return useQuery({
    queryKey: KEYS.diff(a ?? "", b ?? ""),
    queryFn: () =>
      fetcher<SnapshotDiffShape>(
        `api/snapshot-diff?a=${encodeURIComponent(a as string)}&b=${encodeURIComponent(b as string)}`,
      ),
    enabled:
      typeof a === "string" &&
      typeof b === "string" &&
      a.length > 0 &&
      b.length > 0 &&
      a !== b,
  });
}

// ---- Mutations ----------------------------------------------------------

export function useTakeSnapshot(): UseMutationResult<
  TakeSnapshotResult,
  Error,
  void
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      fetcher<TakeSnapshotResult>("api/snapshot", { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEYS.list });
    },
  });
}

/**
 * Browser-driven backup download. The controller emits the JSON
 * with a `Content-Disposition: attachment` header, so we trigger a
 * standard anchor click and let the browser handle naming.
 *
 * Modeled as a mutation (rather than a query) so it only fires on
 * an explicit click. The mutation does no React-Query side effects
 * — a download isn't cacheable.
 */
export function useDownloadBackup(): UseMutationResult<void, Error, void> {
  return useMutation({
    mutationFn: async () => {
      const base = getBaseUrl();
      const href = base
        ? `${base.replace(/\/+$/, "")}/api/backup`
        : "/api/backup";
      // Anchor click is the ergonomically-correct way to trigger
      // a save dialog. fetch+blob would also work but loses the
      // server-suggested filename from `Content-Disposition`.
      if (typeof document === "undefined") return;
      const a = document.createElement("a");
      a.href = href;
      a.rel = "noopener";
      // Empty download attribute lets the server choose the name.
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
  });
}

export interface RestoreInput {
  /** A `File` chosen from an `<input type=file>`. We read its text
   * client-side, JSON.parse it, and forward `service_configs` to
   * the controller. */
  file: File;
}

export function useRestoreBackup(): UseMutationResult<
  RestoreResult,
  Error,
  RestoreInput
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ file }) => {
      const text = await file.text();
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch (err) {
        throw new Error(
          `Backup file is not valid JSON: ${(err as Error).message}`,
        );
      }
      if (!parsed || typeof parsed !== "object") {
        throw new Error("Backup file is empty or not an object");
      }
      const rec = parsed as Record<string, unknown>;
      const serviceConfigs =
        rec.service_configs && typeof rec.service_configs === "object"
          ? rec.service_configs
          : null;
      if (!serviceConfigs) {
        throw new Error("Backup file is missing service_configs");
      }
      return fetcher<RestoreResult>("api/restore", {
        method: "POST",
        body: JSON.stringify({ service_configs: serviceConfigs }),
      });
    },
    onSuccess: () => {
      // Restore overwrites every config — every cached read is suspect.
      void qc.invalidateQueries();
    },
  });
}

export const snapshotsQueryKeys = KEYS;
