import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useUpdateThresholds, type DiskGuardrailThresholds } from "./hooks";

/** Inclusive bounds: the registry rejects values <= 0 or > 100, and
 *  enforces release < lockdown. We mirror those rules client-side
 *  to surface validation errors before the round-trip. */
export interface ThresholdValues {
  watch: number;
  cleanup: number;
  lockdown: number;
  release: number;
}

interface StorageThresholdInputsProps {
  defaults: DiskGuardrailThresholds;
  /** When the operator is read-only, the form shows the values but
   *  disables the Save button. */
  readOnly?: boolean;
}

interface ValidationResult {
  ok: boolean;
  /** Human-readable error string when invalid; absent on success. */
  message?: string;
}

/** Centralised validation logic so the test can exercise it without
 *  rendering the form. */
export function validateThresholds(v: ThresholdValues): ValidationResult {
  const all = [v.watch, v.cleanup, v.lockdown, v.release];
  for (const n of all) {
    if (!Number.isFinite(n)) return { ok: false, message: "Values must be numbers" };
    if (n <= 0 || n > 100) {
      return { ok: false, message: "Values must be between 1 and 100" };
    }
  }
  if (!(v.watch <= v.cleanup)) {
    return {
      ok: false,
      message: "Watch must be ≤ Cleanup",
    };
  }
  if (!(v.cleanup <= v.lockdown)) {
    return {
      ok: false,
      message: "Cleanup must be ≤ Lockdown",
    };
  }
  if (!(v.release < v.lockdown)) {
    return {
      ok: false,
      message: "Release must be < Lockdown",
    };
  }
  return { ok: true };
}

const ROWS: ReadonlyArray<{
  key: keyof ThresholdValues;
  label: string;
  hint: string;
}> = [
  {
    key: "watch",
    label: "Watch",
    hint: "Surface a soft warning in the UI when any mount exceeds this percent.",
  },
  {
    key: "cleanup",
    label: "Cleanup",
    hint: "Trigger qBittorrent cleanup. Deletes oldest completed torrents per the configured policy.",
  },
  {
    key: "lockdown",
    label: "Lockdown",
    hint: "Pause every download client. AUTO trigger — releases automatically once mount drops below Release.",
  },
  {
    key: "release",
    label: "Release",
    hint: "Disk percent at which AUTO lockdown lifts. Must be lower than Lockdown to provide hysteresis.",
  },
];

function readDefault(
  defaults: DiskGuardrailThresholds,
  key: keyof ThresholdValues,
): number {
  if (key === "watch") {
    const v = defaults.watch_percent;
    return typeof v === "number" ? v : 50;
  }
  if (key === "cleanup") {
    const v = defaults.cleanup_percent;
    return typeof v === "number" ? v : 70;
  }
  if (key === "lockdown") {
    const v = defaults.lockdown_percent;
    return typeof v === "number" ? v : 75;
  }
  const v = defaults.release_percent;
  return typeof v === "number" ? v : 60;
}

export function StorageThresholdInputs({
  defaults,
  readOnly = false,
}: StorageThresholdInputsProps) {
  const [values, setValues] = useState<ThresholdValues>(() => ({
    watch: readDefault(defaults, "watch"),
    cleanup: readDefault(defaults, "cleanup"),
    lockdown: readDefault(defaults, "lockdown"),
    release: readDefault(defaults, "release"),
  }));
  // Re-seed state when the controller-side defaults change (a sibling
  // operator saved a new threshold; our poll just picked it up).
  // Tracked through a JSON-serialised key so deep-equal updates
  // re-seed without spurious renders.
  const defaultsKey = JSON.stringify([
    defaults.watch_percent,
    defaults.cleanup_percent,
    defaults.lockdown_percent,
    defaults.release_percent,
  ]);
  useEffect(() => {
    setValues({
      watch: readDefault(defaults, "watch"),
      cleanup: readDefault(defaults, "cleanup"),
      lockdown: readDefault(defaults, "lockdown"),
      release: readDefault(defaults, "release"),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultsKey]);

  const update = useUpdateThresholds();
  const validation = validateThresholds(values);
  const initial: ThresholdValues = {
    watch: readDefault(defaults, "watch"),
    cleanup: readDefault(defaults, "cleanup"),
    lockdown: readDefault(defaults, "lockdown"),
    release: readDefault(defaults, "release"),
  };
  const dirty =
    values.watch !== initial.watch ||
    values.cleanup !== initial.cleanup ||
    values.lockdown !== initial.lockdown ||
    values.release !== initial.release;

  const onChange = (k: keyof ThresholdValues) => (
    e: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const next = Number(e.target.value);
    setValues((prev) => ({ ...prev, [k]: next }));
  };

  const onSave = () => {
    update.mutate(
      {
        watchPercent: values.watch,
        cleanupPercent: values.cleanup,
        lockdownPercent: values.lockdown,
        releasePercent: values.release,
      },
      {
        onSuccess: () => toast.success("Thresholds saved"),
        onError: (err) => {
          const msg =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : "Save failed";
          toast.error(msg);
        },
      },
    );
  };

  const saveDisabled =
    readOnly || update.isPending || !validation.ok || !dirty;

  return (
    <div
      className="flex flex-col gap-3"
      data-testid="storage-threshold-inputs"
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {ROWS.map((row) => (
          <div key={row.key} className="flex flex-col gap-1">
            <Label htmlFor={`storage-threshold-${row.key}`}>{row.label}</Label>
            <Input
              id={`storage-threshold-${row.key}`}
              type="number"
              inputMode="numeric"
              min={1}
              max={100}
              step={1}
              value={Number.isFinite(values[row.key]) ? values[row.key] : ""}
              onChange={onChange(row.key)}
              disabled={readOnly}
              data-testid={`storage-threshold-${row.key}`}
            />
            <p className="text-xs text-fg-muted">{row.hint}</p>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between gap-3">
        {!validation.ok ? (
          <span
            role="alert"
            className="text-xs text-danger"
            data-tone="critical"
            data-testid="storage-threshold-validation"
          >
            {validation.message}
          </span>
        ) : (
          <span
            className="text-xs text-fg-faint"
            data-testid="storage-threshold-hint"
          >
            Range 1-100. Watch ≤ Cleanup ≤ Lockdown; Release &lt; Lockdown.
          </span>
        )}
        <Button
          type="button"
          variant="primary"
          onClick={onSave}
          loading={update.isPending}
          disabled={saveDisabled}
          data-testid="storage-threshold-save"
        >
          Save
        </Button>
      </div>
    </div>
  );
}
