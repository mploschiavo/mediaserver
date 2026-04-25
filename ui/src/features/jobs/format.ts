// Tiny formatters for the Jobs operator surface. Re-exports
// `formatRelative` from media-integrity so timestamps stay consistent
// across operator panels.

export { formatRelative } from "@/features/media-integrity/format";

/**
 * Render a duration in seconds as a short human string. Designed for
 * the per-job `elapsed` field — values can be sub-millisecond (a
 * cached config probe) or multi-minute (a deep filesystem walk).
 *
 *   - 0 / undefined / NaN     → "—"
 *   - < 1s (and > 0)           → "45ms"
 *   - < 60s                    → "1.2s"
 *   - < 3600s                  → "2m 13s"
 *   - >= 3600s                 → "1h 04m"
 */
export function formatElapsed(seconds: number | undefined | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "—";
  if (seconds < 1) {
    const ms = Math.round(seconds * 1000);
    return `${ms}ms`;
  }
  if (seconds < 60) {
    // 1 decimal place keeps the cell ~4 chars wide.
    return `${seconds.toFixed(1)}s`;
  }
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}m ${s.toString().padStart(2, "0")}s`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m.toString().padStart(2, "0")}m`;
}

/**
 * Convert a numeric Unix epoch (seconds — that's what the controller's
 * history payload emits) into an ISO-8601 string for display via
 * `formatRelative`. Returns an empty string for missing/invalid input
 * so downstream formatters render their own "never" / "—" sentinel.
 */
export function epochToIso(seconds: number | undefined | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "";
  // Multiply to ms, defending against floating-point drift on very
  // large values by rounding to the nearest millisecond.
  const ms = Math.round(seconds * 1000);
  try {
    return new Date(ms).toISOString();
  } catch {
    return "";
  }
}

/**
 * Absolute timestamp formatter for tooltip text. Uses the browser
 * locale so the operator sees a string they recognize. Returns an
 * empty string when the input is missing/invalid.
 */
export function formatAbsolute(seconds: number | undefined | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "";
  const ms = Math.round(seconds * 1000);
  try {
    return new Date(ms).toLocaleString();
  } catch {
    return "";
  }
}

/**
 * Parse a single cron field that's either `*`, a single integer, or a
 * `*\/N` step expression. Returns the sorted list of valid values
 * within `[min, max]`, or null when the field doesn't match a shape
 * the helper supports (anything with commas, ranges, or named values
 * bails out).
 */
function expandCronField(
  raw: string,
  min: number,
  max: number,
): number[] | null {
  const trimmed = raw.trim();
  if (trimmed === "*") {
    const out: number[] = [];
    for (let i = min; i <= max; i++) out.push(i);
    return out;
  }
  const stepMatch = /^\*\/(\d+)$/.exec(trimmed);
  if (stepMatch) {
    const step = Number(stepMatch[1]);
    if (!Number.isFinite(step) || step <= 0) return null;
    const out: number[] = [];
    for (let i = min; i <= max; i += step) out.push(i);
    return out;
  }
  if (/^\d+$/.test(trimmed)) {
    const n = Number(trimmed);
    if (!Number.isFinite(n) || n < min || n > max) return null;
    return [n];
  }
  return null;
}

/**
 * Compute the next fire time for a cron expression of the shape
 * `m h * * *` — minute + hour, with the day/month/dow fields all
 * being `*`. The controller's three sidecar cronjobs use this
 * pattern (`0 *\/6 * * *`, `15 *\/6 * * *`, `45 *\/6 * * *`); anything
 * more elaborate returns `null` so the UI renders "—".
 *
 * Allowed minute / hour fields:
 *   - `*` (any),
 *   - a single integer (e.g. `15`),
 *   - a step expression (e.g. `*\/6`).
 *
 * Anything with commas, ranges (`1-5`), or named values is rejected.
 *
 * The function is timezone-naive: it computes against the host's
 * local clock. The cron sidecars run in UTC — operator clocks are
 * usually local — so the rendered "next run" can be off by the
 * tz offset. That tradeoff is fine for an at-a-glance hint; precise
 * scheduling lives server-side.
 */
export function nextCronFire(cron: string, after: Date): Date | null {
  if (typeof cron !== "string") return null;
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [m, h, dom, mon, dow] = parts;
  // Only support the `m h * * *` shape — bail otherwise.
  if (dom !== "*" || mon !== "*" || dow !== "*") return null;
  if (!m || !h) return null;
  const minutes = expandCronField(m, 0, 59);
  const hours = expandCronField(h, 0, 23);
  if (!minutes || minutes.length === 0) return null;
  if (!hours || hours.length === 0) return null;

  // Iterate forward from `after` (rounded up to the next minute) and
  // pick the first (hour, minute) that matches. Bounded to 2 days of
  // search so a malformed expression with no matches can't loop.
  const start = new Date(after.getTime());
  start.setSeconds(0, 0);
  start.setMinutes(start.getMinutes() + 1);

  const minuteSet = new Set(minutes);
  const hourSet = new Set(hours);

  for (let i = 0; i < 60 * 24 * 2; i++) {
    const candidate = new Date(start.getTime() + i * 60_000);
    if (
      hourSet.has(candidate.getHours()) &&
      minuteSet.has(candidate.getMinutes())
    ) {
      return candidate;
    }
  }
  return null;
}

/**
 * Phrase a Date into a short "in 12m" / "in 2h 14m" string from the
 * caller-supplied `now`. Returns "—" when delta is non-positive or
 * the input is invalid.
 */
export function formatUntil(target: Date | null, now: number = Date.now()): string {
  if (!target) return "—";
  const t = target.getTime();
  if (!Number.isFinite(t)) return "—";
  const delta = Math.floor((t - now) / 1000);
  if (delta <= 0) return "imminently";
  if (delta < 60) return `in ${delta}s`;
  if (delta < 3600) {
    const m = Math.floor(delta / 60);
    return `in ${m}m`;
  }
  if (delta < 86400) {
    const h = Math.floor(delta / 3600);
    const m = Math.floor((delta % 3600) / 60);
    return m === 0 ? `in ${h}h` : `in ${h}h ${m}m`;
  }
  const d = Math.floor(delta / 86400);
  return `in ${d}d`;
}
