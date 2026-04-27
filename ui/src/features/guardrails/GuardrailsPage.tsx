import { useState } from "react";
import { toast } from "sonner";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { GuardrailDomainTab } from "./GuardrailDomainTab";
import { GuardrailsByDomainChart } from "./GuardrailsByDomainChart";
import {
  useGuardrails,
  useUpdateGuardrailsConfig,
  type GuardrailDomain,
} from "./hooks";

const DOMAIN_ORDER: ReadonlyArray<{ id: GuardrailDomain; label: string }> = [
  { id: "storage", label: "Storage" },
  { id: "bandwidth", label: "Bandwidth" },
  { id: "external_api", label: "API" },
  { id: "media_quality", label: "Media" },
  { id: "job_health", label: "Jobs" },
  { id: "auth", label: "Auth" },
  { id: "cost", label: "Cost" },
  { id: "dependency", label: "Deps" },
];

interface GuardrailsPageProps {
  /** Optional id from the ?focus= query param. The matching row is
   *  highlighted and the parent tab is auto-selected. */
  focusedId?: string | null;
}

/**
 * Tabbed view of every registered guardrail. The active tab is the
 * domain that owns the focused rule (when ?focus= is set), else
 * "storage" — that's where the most operationally-relevant rules
 * live for a fresh deployment.
 */
export function GuardrailsPage({ focusedId }: GuardrailsPageProps) {
  const query = useGuardrails();
  const rules = query.data?.guardrails ?? [];
  const focusedRule = focusedId
    ? rules.find((r) => r.id === focusedId)
    : undefined;
  const defaultTab: GuardrailDomain =
    focusedRule?.domain ?? "storage";

  if (query.isLoading) {
    return (
      <div className="flex flex-col gap-3" data-testid="guardrails-loading">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (query.error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-4 text-sm text-danger"
        data-testid="guardrails-error"
      >
        <p className="font-medium">Failed to load guardrails</p>
        <p className="mt-1 text-fg-muted">{(query.error as Error).message}</p>
      </div>
    );
  }

  // Empty-state — fires while the controller is booting (no rules
  // loaded yet) or in the rare case where an operator wiped the
  // contract directory. Always-rendered, never hidden, per the
  // empty-state-visibility feedback.
  const ruleCount = rules.length;
  if (ruleCount === 0) {
    return (
      <div
        className="flex flex-col items-center gap-2 rounded-lg border border-border bg-bg-1 p-6 text-center text-sm text-fg-muted"
        data-testid="guardrails-empty"
      >
        <p className="font-medium text-fg">No guardrail rules loaded</p>
        <p className="max-w-md">
          Rules ship as YAML in <code>contracts/guardrails/</code>.
          They populate this page once the controller finishes its
          initial bootstrap. If you're seeing this on a steady-state
          stack, check the controller logs for a load error.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <GuardrailsByDomainChart />
      <CadenceEditor
        currentSeconds={query.data?.evaluation_interval_seconds ?? 300}
      />
      <Tabs
      defaultValue={defaultTab}
      className="flex flex-col gap-4"
      data-testid="guardrails-tabs"
    >
      <TabsList className="flex flex-wrap gap-1">
        {DOMAIN_ORDER.map((d) => (
          <TabsTrigger
            key={d.id}
            value={d.id}
            data-testid={`guardrails-tab-${d.id}`}
          >
            {d.label}
          </TabsTrigger>
        ))}
      </TabsList>
      {DOMAIN_ORDER.map((d) => (
        <TabsContent key={d.id} value={d.id} className="flex flex-col gap-3">
          <GuardrailDomainTab
            domain={d.id}
            rules={rules}
            focusedId={focusedId}
          />
        </TabsContent>
      ))}
    </Tabs>
    </div>
  );
}

/**
 * Editable cadence input for the cross-domain guardrail evaluation
 * loop. Operators previously had no way to slow this down — every
 * minute the loop fired, polluting /api/jobs/history with one row
 * per already-firing rule. POST /api/guardrails/config persists a
 * new interval (floor 30s, ceiling 3600s).
 */
function CadenceEditor({ currentSeconds }: { currentSeconds: number }) {
  const update = useUpdateGuardrailsConfig();
  const [draft, setDraft] = useState(String(currentSeconds));
  const dirty = String(currentSeconds) !== draft;

  // Quick-set buttons: common operator choices. 60s is the legacy
  // "every minute" cadence (allowed, not recommended); 300s is the
  // new default; 1800s for slower stacks; 12h/24h for operators
  // drowning in always-firing rules who'd rather see a daily digest
  // than continuous noise.
  const presets = [60, 300, 1800, 43200, 86400];

  const onSave = () => {
    const n = Number.parseInt(draft, 10);
    if (!Number.isFinite(n) || n < 30 || n > 86400) {
      toast.error("Cadence must be between 30 and 86400 seconds.");
      return;
    }
    update.mutate(
      { evaluation_interval_seconds: n },
      {
        onSuccess: (res) => {
          toast.success(
            `Guardrail cadence set to ${res.evaluation_interval_seconds}s.`,
          );
        },
        onError: (err) => {
          toast.error(err instanceof Error ? err.message : "Failed to save");
        },
      },
    );
  };

  return (
    <div
      className="flex flex-col gap-2 rounded-lg border border-border bg-bg-1 p-3 sm:flex-row sm:items-end sm:gap-3"
      data-testid="guardrails-cadence-editor"
    >
      <div className="flex flex-col gap-1">
        <Label htmlFor="guardrail-cadence" className="text-xs text-fg-muted">
          Evaluation cadence (seconds)
        </Label>
        <Input
          id="guardrail-cadence"
          type="number"
          min={30}
          max={86400}
          step={10}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="w-32 tabular-nums"
          data-testid="guardrails-cadence-input"
        />
      </div>
      <div className="flex flex-wrap gap-1">
        {presets.map((p) => (
          <Button
            key={p}
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setDraft(String(p))}
            data-testid={`guardrails-cadence-preset-${p}`}
          >
            {p === 60
              ? "1m"
              : p === 300
                ? "5m"
                : p === 1800
                  ? "30m"
                  : p === 43200
                    ? "12h"
                    : p === 86400
                      ? "24h"
                      : `${p}s`}
          </Button>
        ))}
      </div>
      <Button
        type="button"
        size="sm"
        onClick={onSave}
        disabled={!dirty || update.isPending}
        loading={update.isPending}
        data-testid="guardrails-cadence-save"
      >
        Save cadence
      </Button>
      <p className="text-xs text-fg-muted sm:ml-auto">
        How often the registry re-evaluates every rule. Storage rules
        rarely need {"<"} 5 min. If everything is firing at once, drop
        to 12h or 24h while you tune the rules. Floor 30s, ceiling 24h.
      </p>
    </div>
  );
}
