import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { GuardrailDomainTab } from "./GuardrailDomainTab";
import { useGuardrails, type GuardrailDomain } from "./hooks";

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

  return (
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
  );
}
