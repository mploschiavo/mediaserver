import { GuardrailRow } from "./GuardrailRow";
import type { Guardrail, GuardrailDomain } from "./hooks";

interface Props {
  domain: GuardrailDomain;
  rules: readonly Guardrail[];
  focusedId?: string | null;
}

/**
 * Renders the rules for one domain tab. Empty-state pattern: when no
 * rules are registered for a domain (cost only, on the bare-metal
 * stack) we show a quiet placeholder so the tab body isn't blank.
 */
export function GuardrailDomainTab({ domain, rules, focusedId }: Props) {
  const filtered = rules.filter((r) => r.domain === domain);
  if (filtered.length === 0) {
    return (
      <div
        data-testid={`guardrail-domain-empty-${domain}`}
        className="rounded-md border border-dashed border-border bg-bg-1 p-6 text-center text-sm text-fg-muted"
      >
        No guardrails are registered for this domain on this deployment.
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3" data-testid={`guardrail-domain-${domain}`}>
      {filtered.map((rule) => (
        <GuardrailRow
          key={rule.id}
          rule={rule}
          focused={focusedId === rule.id}
        />
      ))}
    </div>
  );
}
