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
