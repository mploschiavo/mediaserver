import type { JSX } from "react";
import { Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { UpdateCleanupPolicyInput } from "./hooks";

/** Order strategies surfaced by `DiskGuardrailsService.enforce()` —
 *  see ADR-0008 §5 (Smart cleanup ordering). */
const ORDER_STRATEGIES: ReadonlyArray<{
  key: NonNullable<UpdateCleanupPolicyInput["order_strategy"]>;
  label: string;
  description: string;
}> = [
  {
    key: "oldest_first",
    label: "Oldest first",
    description: "FIFO by completion timestamp (default)",
  },
  {
    key: "largest_first",
    label: "Largest first",
    description: "Free disk fastest by deleting bulky completed torrents",
  },
  {
    key: "poor_ratio_first",
    label: "Poor ratio first",
    description: "Delete torrents whose seed ratio is well below the floor",
  },
  {
    key: "watched_first",
    label: "Watched first",
    description: "Prefer torrents whose mapped files Jellyfin shows as played",
  },
];

export type StrategyKey = NonNullable<
  UpdateCleanupPolicyInput["order_strategy"]
>;

/** Form-state bag — single object so the props interface stays
 *  under the 8-field cap; bundling avoids a 12-prop "Christmas tree"
 *  signature and makes the parent's setter wiring concrete. */
export interface CleanupPolicyFormState {
  categoriesText: string;
  setCategoriesText: (v: string) => void;
  minAgeHours: string;
  setMinAgeHours: (v: string) => void;
  minSeedingMinutes: string;
  setMinSeedingMinutes: (v: string) => void;
  minRatio: string;
  setMinRatio: (v: string) => void;
  maxDeletePerRun: string;
  setMaxDeletePerRun: (v: string) => void;
  orderStrategy: StrategyKey;
  setOrderStrategy: (v: StrategyKey) => void;
}

/** Save-action status — collects mutation flags so the form's
 *  props stay narrow. */
export interface CleanupPolicySaveStatus {
  onSave: () => void;
  isPending: boolean;
  errorMessage: string | null;
  isSuccess: boolean;
}

export interface StorageCleanupPolicyFormProps {
  state: CleanupPolicyFormState;
  status: CleanupPolicySaveStatus;
}

export function StorageCleanupPolicyForm({
  state,
  status,
}: StorageCleanupPolicyFormProps): JSX.Element {
  const {
    categoriesText,
    setCategoriesText,
    minAgeHours,
    setMinAgeHours,
    minSeedingMinutes,
    setMinSeedingMinutes,
    minRatio,
    setMinRatio,
    maxDeletePerRun,
    setMaxDeletePerRun,
    orderStrategy,
    setOrderStrategy,
  } = state;
  const { onSave, isPending, errorMessage, isSuccess } = status;
  const description =
    ORDER_STRATEGIES.find((o) => o.key === orderStrategy)?.description ??
    "Custom strategy";
  const categoriesEmpty =
    categoriesText
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean).length === 0;

  return (
    <div
      className="flex flex-col gap-3 border-t border-border px-3 py-3 text-sm"
      data-testid="storage-cleanup-policy-body"
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor="storage-cleanup-policy-categories-input">
            Categories
          </Label>
          <Input
            id="storage-cleanup-policy-categories-input"
            data-testid="storage-cleanup-policy-categories-input"
            value={categoriesText}
            onChange={(e) => setCategoriesText(e.target.value)}
            placeholder="tv-sonarr, movies-radarr"
          />
          {categoriesEmpty ? (
            <span
              className="text-xs text-fg-faint"
              data-testid="storage-cleanup-policy-categories-empty"
            >
              none configured (cleanup runs across all categories)
            </span>
          ) : null}
        </div>

        <div className="flex flex-col gap-1">
          <Label htmlFor="storage-cleanup-policy-order-select">
            Order strategy
          </Label>
          <select
            id="storage-cleanup-policy-order-select"
            data-testid="storage-cleanup-policy-order"
            className="rounded-md border border-border bg-bg-1 px-2 py-1 text-sm font-mono"
            value={orderStrategy}
            onChange={(e) =>
              setOrderStrategy(e.target.value as StrategyKey)
            }
          >
            {ORDER_STRATEGIES.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
          <span className="text-xs text-fg-muted">{description}</span>
        </div>

        <NumericField
          label="Min age (hours)"
          testId="storage-cleanup-policy-min-age"
          value={minAgeHours}
          setValue={setMinAgeHours}
        />
        <NumericField
          label="Min seeding time (minutes)"
          testId="storage-cleanup-policy-min-seeding"
          value={minSeedingMinutes}
          setValue={setMinSeedingMinutes}
        />
        <NumericField
          label="Min ratio"
          testId="storage-cleanup-policy-min-ratio"
          value={minRatio}
          setValue={setMinRatio}
          step="0.1"
        />
        <NumericField
          label="Max delete per run"
          testId="storage-cleanup-policy-max-delete"
          value={maxDeletePerRun}
          setValue={setMaxDeletePerRun}
          min={1}
          max={1000}
        />
      </div>

      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-fg-muted">
          Persists to{" "}
          <span className="font-mono">/srv-config/.controller/disk-cleanup-policy.json</span>
        </span>
        <Button
          size="sm"
          onClick={onSave}
          disabled={isPending}
          data-testid="storage-cleanup-policy-save"
        >
          <Save aria-hidden className="size-3.5" />
          {isPending ? "Saving…" : "Save policy"}
        </Button>
      </div>
      {errorMessage ? (
        <span
          className="text-xs text-red-500"
          data-testid="storage-cleanup-policy-error"
        >
          {errorMessage}
        </span>
      ) : null}
      {isSuccess ? (
        <span
          className="text-xs text-fg-muted"
          data-testid="storage-cleanup-policy-saved"
        >
          Saved.
        </span>
      ) : null}
    </div>
  );
}

interface NumericFieldProps {
  label: string;
  testId: string;
  value: string;
  setValue: (v: string) => void;
  step?: string;
  min?: number;
  max?: number;
}

function NumericField({
  label,
  testId,
  value,
  setValue,
  step,
  min = 0,
  max,
}: NumericFieldProps): JSX.Element {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={`${testId}-input`}>{label}</Label>
      <Input
        id={`${testId}-input`}
        data-testid={testId}
        type="number"
        step={step}
        min={min}
        max={max}
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
    </div>
  );
}
