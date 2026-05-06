import { useState, type JSX } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  useUpdateCleanupPolicy,
  type UpdateCleanupPolicyInput,
} from "./hooks";
import {
  StorageCleanupPolicyForm,
  type StrategyKey,
} from "./StorageCleanupPolicyForm";

interface StorageCleanupPolicyProps {
  /** When the controller already exposes a writable cleanup-policy
   *  endpoint, the parent passes the merged config here. ADR-0008
   *  Phase 4 added the write surface — `useUpdateCleanupPolicy`
   *  POSTs to `/api/disk-guardrails/cleanup-policy` and persists the
   *  override JSON. */
  policy?: {
    categories?: readonly string[];
    min_age_hours?: number;
    min_seeding_time_minutes?: number;
    min_ratio?: number;
    max_delete_per_run?: number;
    order_strategy?: string;
  };
}

const DEFAULT_POLICY = {
  categories: ["tv-sonarr", "movies-radarr"],
  min_age_hours: 24,
  min_seeding_time_minutes: 1440,
  min_ratio: 1,
  max_delete_per_run: 25,
  order_strategy: "oldest_first" as const,
};

const STRATEGY_KEYS = new Set<string>([
  "oldest_first",
  "largest_first",
  "poor_ratio_first",
  "watched_first",
]);

function coerceStrategy(value: string): StrategyKey {
  return STRATEGY_KEYS.has(value) ? (value as StrategyKey) : "oldest_first";
}

export function StorageCleanupPolicy({
  policy,
}: StorageCleanupPolicyProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const merged = { ...DEFAULT_POLICY, ...(policy ?? {}) };
  const [categoriesText, setCategoriesText] = useState<string>(
    (merged.categories ?? []).join(", "),
  );
  const [minAgeHours, setMinAgeHours] = useState<string>(
    String(merged.min_age_hours ?? DEFAULT_POLICY.min_age_hours),
  );
  const [minSeedingMinutes, setMinSeedingMinutes] = useState<string>(
    String(
      merged.min_seeding_time_minutes ??
        DEFAULT_POLICY.min_seeding_time_minutes,
    ),
  );
  const [minRatio, setMinRatio] = useState<string>(
    String(merged.min_ratio ?? DEFAULT_POLICY.min_ratio),
  );
  const [maxDeletePerRun, setMaxDeletePerRun] = useState<string>(
    String(merged.max_delete_per_run ?? DEFAULT_POLICY.max_delete_per_run),
  );
  const [orderStrategy, setOrderStrategy] = useState<StrategyKey>(
    coerceStrategy(merged.order_strategy ?? DEFAULT_POLICY.order_strategy),
  );
  const updatePolicy = useUpdateCleanupPolicy();

  const onSave = () => {
    const parsedCategories = categoriesText
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean);
    const body: UpdateCleanupPolicyInput = {
      categories: parsedCategories,
      min_completion_age_hours: Number(minAgeHours) || 0,
      min_seeding_time_minutes: Number(minSeedingMinutes) || 0,
      min_ratio: Number(minRatio) || 0,
      max_delete_per_run: Number(maxDeletePerRun) || 1,
      order_strategy: orderStrategy,
    };
    updatePolicy.mutate(body);
  };

  return (
    <div
      className="rounded-md border border-border bg-bg-1/40"
      data-testid="storage-cleanup-policy"
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-sm text-fg [@media(hover:hover)]:hover:bg-bg-2"
        data-testid="storage-cleanup-policy-toggle"
      >
        <span className="flex items-center gap-2">
          {open ? (
            <ChevronDown aria-hidden className="size-4" />
          ) : (
            <ChevronRight aria-hidden className="size-4" />
          )}
          <span className="font-medium">Cleanup policy</span>
          <Badge variant="outline" className="text-xs">
            {orderStrategy}
          </Badge>
        </span>
        <span className="text-xs text-fg-faint">
          {maxDeletePerRun} max / run · ratio ≥ {minRatio}
        </span>
      </button>
      {open ? (
        <StorageCleanupPolicyForm
          state={{
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
          }}
          status={{
            onSave,
            isPending: updatePolicy.isPending,
            errorMessage: updatePolicy.isError
              ? (updatePolicy.error as Error)?.message ?? "Save failed"
              : null,
            isSuccess: updatePolicy.isSuccess,
          }}
        />
      ) : null}
    </div>
  );
}
