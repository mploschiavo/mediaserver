import { useMemo, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/cn";
import {
  formatCurrentValue,
  formatRelative,
  formatThreshold,
  statusLabel,
  statusVariant,
} from "./format";
import {
  type Guardrail,
  useDisableGuardrail,
  useTestGuardrail,
  useUpdateGuardrail,
} from "./hooks";

interface GuardrailRowProps {
  rule: Guardrail;
  /** Optional anchor id used by the focused-rule deep-link from the
   *  TriggeredBanner. Renders the card with a subtle ring when set. */
  focused?: boolean;
}

/**
 * One card per guardrail. Renders:
 *   - name + description + status badge
 *   - threshold inputs (one number field per primitive key)
 *   - "Test" + "Disable" buttons
 *   - relative "last fired" timestamp
 *
 * The threshold editor is purposefully small: each key whose default
 * value is a number gets a number input; nested-object keys (per-mount
 * overrides, ceilings_gb) fall back to a JSON textarea so the operator
 * can still tune them without us re-implementing a generic schema
 * editor.
 */
export function GuardrailRow({ rule, focused }: GuardrailRowProps) {
  const update = useUpdateGuardrail(rule.id);
  const disable = useDisableGuardrail(rule.id);
  const test = useTestGuardrail(rule.id);

  const [draft, setDraft] = useState<Record<string, string>>(() =>
    serializeThreshold(rule.threshold),
  );

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(serializeThreshold(rule.threshold)),
    [draft, rule.threshold],
  );

  const status = rule.disabled ? "disabled" : (rule.last_status ?? "unknown");
  const lastFired = rule.last_triggered_at;
  const lastEvaluated = rule.last_evaluated_at;

  return (
    <Card
      data-testid={`guardrail-row-${rule.id}`}
      data-rule-id={rule.id}
      className={cn(
        "transition-shadow",
        focused
          ? "ring-2 ring-accent ring-offset-2 ring-offset-bg"
          : undefined,
      )}
    >
      <CardHeader className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <CardTitle className="font-mono text-sm">{rule.id}</CardTitle>
          <CardDescription>{rule.description}</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant={statusVariant(status)}
            data-testid={`guardrail-row-${rule.id}-status`}
          >
            {statusLabel(status)}
          </Badge>
          <div className="flex flex-col items-end gap-0.5 text-xs text-fg-muted">
            <span data-testid={`guardrail-row-${rule.id}-last-fired`}>
              last fired {formatRelative(lastFired)}
            </span>
            <span
              data-testid={`guardrail-row-${rule.id}-last-evaluated`}
              className="text-[11px] opacity-70"
            >
              last evaluated {formatRelative(lastEvaluated)}
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {Object.entries(draft).map(([key, value]) => (
            <div key={key} className="flex flex-col gap-1">
              <Label className="text-xs" htmlFor={`${rule.id}-${key}`}>
                {key}
              </Label>
              <Input
                id={`${rule.id}-${key}`}
                value={value}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, [key]: e.target.value }))
                }
                data-testid={`guardrail-input-${rule.id}-${key}`}
              />
            </div>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            disabled={!dirty || update.isPending}
            onClick={() => {
              const parsed = parseThreshold(draft, rule.threshold);
              update.mutate(parsed);
            }}
            data-testid={`guardrail-save-${rule.id}`}
          >
            {update.isPending ? "Saving…" : "Save"}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => test.mutate()}
            disabled={test.isPending}
            data-testid={`guardrail-test-${rule.id}`}
          >
            {test.isPending ? "Testing…" : "Test"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => disable.mutate(!rule.disabled)}
            disabled={disable.isPending}
            data-testid={`guardrail-disable-${rule.id}`}
          >
            {rule.disabled ? "Enable" : "Disable"}
          </Button>
        </div>
        {test.error ? (
          <div
            className="rounded-md border border-danger/40 bg-danger/10 p-2 text-xs text-danger"
            role="alert"
            data-testid={`guardrail-test-error-${rule.id}`}
          >
            Test failed: {test.error.message}
          </div>
        ) : null}
        {test.data ? (
          <div
            className={cn(
              "flex flex-col gap-1 rounded-md border p-2 text-xs",
              test.data.would_trigger
                ? "border-warning/40 bg-warning/10 text-fg"
                : "border-border bg-bg-1 text-fg-muted",
            )}
            data-testid={`guardrail-test-result-${rule.id}`}
          >
            <div className="font-medium">
              {test.data.would_trigger
                ? `Would trigger: ${test.data.severity ?? "warning"}`
                : "Would not trigger right now."}
            </div>
            <div className="font-mono">
              <span className="text-fg-muted">current: </span>
              {formatCurrentValue(test.data.current_value)}
            </div>
            <div className="font-mono">
              <span className="text-fg-muted">threshold: </span>
              {formatThreshold(test.data.threshold)}
            </div>
            {test.data.description ? (
              <div className="text-fg-muted">{test.data.description}</div>
            ) : null}
          </div>
        ) : null}
        {update.isSuccess && !dirty ? (
          <div
            className="rounded-md border border-success/40 bg-success/10 p-2 text-xs text-fg"
            data-testid={`guardrail-save-result-${rule.id}`}
          >
            Saved.
          </div>
        ) : null}
        {disable.isSuccess ? (
          <div
            className="rounded-md border border-border bg-bg-1 p-2 text-xs text-fg-muted"
            data-testid={`guardrail-disable-result-${rule.id}`}
          >
            {rule.disabled ? "Disabled." : "Enabled."}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

// ---- Helpers ------------------------------------------------------------

/** Convert a threshold object to a string-keyed string-valued draft.
 *  Nested values (objects, arrays) are JSON-encoded so the operator
 *  can edit them in place without us shipping a schema editor. */
function serializeThreshold(t: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(t || {})) {
    if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") {
      out[k] = String(v);
    } else {
      out[k] = JSON.stringify(v ?? null);
    }
  }
  return out;
}

/** Convert the draft back to a threshold object, preserving the
 *  original primitive types from the live threshold. */
function parseThreshold(
  draft: Record<string, string>,
  original: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, raw] of Object.entries(draft)) {
    const orig = original?.[k];
    if (typeof orig === "number") {
      const n = Number(raw);
      out[k] = Number.isFinite(n) ? n : orig;
    } else if (typeof orig === "boolean") {
      out[k] = raw === "true";
    } else if (typeof orig === "object" && orig !== null) {
      try {
        out[k] = JSON.parse(raw);
      } catch {
        out[k] = orig;
      }
    } else {
      out[k] = raw;
    }
  }
  return out;
}
