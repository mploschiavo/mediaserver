import { useState } from "react";
import { ChevronDown, ChevronRight, Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";

/** Order strategies surfaced by `DiskGuardrailsService.enforce()` —
 *  see ADR-0008 §5 (Smart cleanup ordering). */
const ORDER_STRATEGIES: ReadonlyArray<{
  key: string;
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

interface StorageCleanupPolicyProps {
  /** When the controller already exposes a writable cleanup-policy
   *  endpoint, the parent passes the merged config here. The
   *  component renders the values whether or not a write surface
   *  exists; today, no write endpoint exists yet — so we render
   *  read-only with an "edit profile.yaml" hint per the brief. */
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
  order_strategy: "oldest_first",
};

export function StorageCleanupPolicy({
  policy,
}: StorageCleanupPolicyProps) {
  const [open, setOpen] = useState(false);
  const merged = { ...DEFAULT_POLICY, ...(policy ?? {}) };

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
            {merged.order_strategy}
          </Badge>
        </span>
        <span className="text-xs text-fg-faint">
          {merged.max_delete_per_run} max / run · ratio ≥ {merged.min_ratio}
        </span>
      </button>
      {open ? (
        <div
          className="flex flex-col gap-3 border-t border-border px-3 py-3 text-sm"
          data-testid="storage-cleanup-policy-body"
        >
          <div
            role="note"
            className="flex items-start gap-2 rounded-md border border-border bg-bg-2 p-2 text-xs text-fg-muted"
            data-testid="storage-cleanup-policy-readonly-note"
          >
            <Info aria-hidden className="mt-0.5 size-3.5 shrink-0" />
            <span>
              Read-only for now — edit{" "}
              <span className="font-mono">profile.yaml</span> →{" "}
              <span className="font-mono">disk_guardrails.qbit_cleanup</span>{" "}
              to change these values until the write endpoint lands.
            </span>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <Label>Categories</Label>
              <div className="flex flex-wrap gap-1">
                {merged.categories && merged.categories.length > 0 ? (
                  merged.categories.map((c) => (
                    <Badge key={c} variant="default">
                      {c}
                    </Badge>
                  ))
                ) : (
                  <span
                    className="text-xs text-fg-faint"
                    data-testid="storage-cleanup-policy-categories-empty"
                  >
                    none configured
                  </span>
                )}
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <Label>Order strategy</Label>
              <span
                className="text-sm font-mono"
                data-testid="storage-cleanup-policy-order"
              >
                {merged.order_strategy}
              </span>
              <span className="text-xs text-fg-muted">
                {ORDER_STRATEGIES.find((o) => o.key === merged.order_strategy)
                  ?.description ?? "Custom strategy"}
              </span>
            </div>

            <Field
              label="Min age (hours)"
              testId="storage-cleanup-policy-min-age"
              value={String(merged.min_age_hours)}
            />
            <Field
              label="Min seeding time (minutes)"
              testId="storage-cleanup-policy-min-seeding"
              value={String(merged.min_seeding_time_minutes)}
            />
            <Field
              label="Min ratio"
              testId="storage-cleanup-policy-min-ratio"
              value={String(merged.min_ratio)}
            />
            <Field
              label="Max delete per run"
              testId="storage-cleanup-policy-max-delete"
              value={String(merged.max_delete_per_run)}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}

interface FieldProps {
  label: string;
  testId: string;
  value: string;
}

function Field({ label, testId, value }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      <span className="font-mono text-sm" data-testid={testId}>
        {value}
      </span>
    </div>
  );
}
