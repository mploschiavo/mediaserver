// Shared formatters for the Media Integrity feature surface.
// Kept tiny + pure so they're trivially testable and tree-shakable.

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"] as const;

/**
 * Render a byte count as a short human-readable string. Uses
 * binary prefixes (1024) — matches what the Servarr CLIs and
 * Jellyfin report. Output is always two significant figures
 * for sub-100 GB values, integer for >= 100 in the same unit.
 */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  const i = Math.min(
    Math.floor(Math.log(n) / Math.log(1024)),
    UNITS.length - 1,
  );
  const v = n / 1024 ** i;
  // Raw bytes (i === 0) always render with one decimal for visual
  // alignment with the larger units; thresholds inside a unit then
  // pick precision to keep the rendered string ~3-4 chars wide.
  const formatted =
    i === 0
      ? v.toFixed(1)
      : v < 10
        ? v.toFixed(2)
        : v < 100
          ? v.toFixed(1)
          : Math.round(v);
  return `${formatted} ${UNITS[i]}`;
}

/**
 * Compress an ISO-8601 timestamp into a short relative phrase
 * ("12m ago", "2h ago", "3d ago"). Returns "never" for empty
 * or unparseable input. Negative deltas (clock-skew) clamp to
 * "just now" so we don't render scary future labels.
 */
export function formatRelative(isoTs: string, now: number = Date.now()): string {
  if (!isoTs) return "never";
  const t = Date.parse(isoTs);
  if (!Number.isFinite(t)) return "never";
  const delta = Math.max(0, Math.floor((now - t) / 1000));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}
