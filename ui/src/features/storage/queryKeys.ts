// Centralised TanStack Query keys for the disk-guardrails surface.
// One file so cache invalidation from sibling code (SSE bridge,
// route navigations) does not have to spread the literal "storage"
// magic-string across the codebase.

export const storageQueryKeys = {
  /** Root namespace — `qc.invalidateQueries({ queryKey: storageQueryKeys.root })`
   *  refreshes every storage query in one call. */
  root: ["storage"] as const,
  /** GET /api/disk-guardrails — merged status snapshot. */
  status: ["storage", "status"] as const,
} as const;
