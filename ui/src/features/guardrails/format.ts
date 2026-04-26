// Small format helpers used by the Guardrails feature surface.
// Kept tiny + side-effect-free so the route bundle stays parsimonious.

import type { GuardrailStatus } from "./hooks";

const STATUS_LABEL: Readonly<Record<GuardrailStatus, string>> = {
  ok: "OK",
  info: "Info",
  warning: "Warning",
  critical: "Critical",
  disabled: "Disabled",
  unknown: "Unknown",
};

const STATUS_VARIANT: Readonly<
  Record<GuardrailStatus, "success" | "warning" | "danger" | "info" | "outline" | "default">
> = {
  ok: "success",
  info: "info",
  warning: "warning",
  critical: "danger",
  disabled: "outline",
  unknown: "default",
};

export function statusLabel(status: GuardrailStatus | undefined): string {
  return STATUS_LABEL[status ?? "unknown"];
}

export function statusVariant(
  status: GuardrailStatus | undefined,
): "success" | "warning" | "danger" | "info" | "outline" | "default" {
  return STATUS_VARIANT[status ?? "unknown"];
}

/** Quick "5m ago"-style relative timestamp. ts is a unix-second
 *  number from the controller; missing / 0 renders as "never". */
export function formatRelative(ts: number | undefined): string {
  if (!ts || ts <= 0) return "never";
  const ms = Date.now() - ts * 1000;
  if (ms < 60_000) return "just now";
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return `${Math.floor(ms / 86_400_000)}d ago`;
}

/**
 * Render a guardrail's `current_value` (whatever the rule's evaluator
 * returned for the live reading) into operator-readable text. The
 * controller emits free-form shapes — sometimes a scalar, sometimes a
 * dict keyed by mount, sometimes a list. Without a rich formatter the
 * UI used to show only "Would trigger: warning" with no number, which
 * operators read as "the test didn't actually run". Show the values.
 *
 * Heuristics:
 *   - large bytes (>1 GiB) → "<n.n> GiB" / "<n.n> GB"
 *   - dict of {key: bytes} → "config 541 GiB · media 541 GiB · …"
 *   - other dicts          → JSON-encoded, capped
 *   - lists                → joined
 *   - primitives           → as-is
 */
export function formatCurrentValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return formatScalar(value);
  if (typeof value === "string") return value;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) {
    return value.map((v) => formatCurrentValue(v)).join(", ");
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return "{}";
    // If every value is a number that looks byte-sized, treat as a
    // mount-keyed bytes dict.
    const allBytes =
      entries.length <= 8 &&
      entries.every(
        ([, v]) => typeof v === "number" && (v as number) >= 1024 * 1024,
      );
    if (allBytes) {
      return entries
        .map(([k, v]) => `${k} ${formatBytes(v as number)}`)
        .join(" · ");
    }
    const compact = entries
      .map(([k, v]) => `${k}: ${formatCurrentValue(v)}`)
      .join(", ");
    return compact.length > 160 ? compact.slice(0, 157) + "…" : compact;
  }
  return String(value);
}

function formatScalar(n: number): string {
  if (n === 0) return "0";
  // Heuristic: numbers >= 1 GiB are probably bytes.
  if (Math.abs(n) >= 1024 * 1024 * 1024) return formatBytes(n);
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(2);
}

function formatBytes(b: number): string {
  const gib = b / (1024 * 1024 * 1024);
  if (gib >= 1024) return `${(gib / 1024).toFixed(2)} TiB`;
  if (gib >= 10) return `${gib.toFixed(0)} GiB`;
  return `${gib.toFixed(1)} GiB`;
}

/** Format a threshold object compactly for the test-result line. */
export function formatThreshold(t: Record<string, unknown>): string {
  const entries = Object.entries(t || {});
  if (entries.length === 0) return "{}";
  return entries
    .map(([k, v]) => `${k}=${formatCurrentValue(v)}`)
    .join(", ");
}
