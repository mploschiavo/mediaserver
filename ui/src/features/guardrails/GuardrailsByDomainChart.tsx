import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ShieldCheck } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useGuardrails } from "./hooks";

/**
 * Stacked bar of guardrail rule counts grouped by domain, split by
 * current ``last_status`` (ok / warning / critical / unknown). Lets
 * the operator scan-read which domain has the most active firings
 * without ploughing the row table.
 *
 * Empty-state pattern: card always renders. When the buffer is
 * unloaded or no rules are configured, we show a friendly caption
 * rather than hiding the chart.
 */
export function GuardrailsByDomainChart() {
  const query = useGuardrails();
  const data = useMemo(() => bucketByDomain(query.data), [query.data]);
  const hasAnyData = data.some(
    (row) => row.ok + row.warning + row.critical + row.unknown > 0,
  );

  return (
    <Card data-testid="guardrails-by-domain-chart">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck aria-hidden className="size-4" />
          Rules by domain &amp; status
        </CardTitle>
        <CardDescription>
          Count of guardrail rules per domain, stacked by current
          status. Tall red sections = a domain with multiple
          critical-firing rules right now.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <Skeleton className="h-48 w-full rounded-md" />
        ) : query.error ? (
          <p
            role="alert"
            className="text-sm text-danger"
            data-testid="guardrails-by-domain-chart-error"
          >
            Couldn't load guardrail rules:{" "}
            {(query.error as Error).message}
          </p>
        ) : !hasAnyData ? (
          <p
            className="text-sm text-fg-muted"
            data-testid="guardrails-by-domain-chart-empty"
          >
            No guardrail rules configured yet. Rules ship as YAML in
            ``contracts/guardrails/`` — they'll populate this chart
            once the controller loads them.
          </p>
        ) : (
          <div
            className="h-48 w-full"
            data-testid="guardrails-by-domain-chart-area"
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="domain"
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 10 }}
                />
                <YAxis
                  stroke="var(--fg-faint)"
                  tick={{ fontSize: 10 }}
                  allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{
                    background: "var(--bg-2)",
                    border: "1px solid var(--border)",
                    fontSize: 12,
                  }}
                />
                <Legend
                  iconSize={9}
                  wrapperStyle={{ fontSize: 11 }}
                />
                <Bar dataKey="ok" stackId="status" fill="#4ade80" name="ok" />
                <Bar
                  dataKey="warning"
                  stackId="status"
                  fill="#facc15"
                  name="warning"
                />
                <Bar
                  dataKey="critical"
                  stackId="status"
                  fill="#f87171"
                  name="critical"
                />
                <Bar
                  dataKey="unknown"
                  stackId="status"
                  fill="#94a3b8"
                  name="unknown"
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface DomainRow {
  domain: string;
  ok: number;
  warning: number;
  critical: number;
  unknown: number;
}

interface RuleRow {
  domain?: string;
  last_status?: string;
}

function bucketByDomain(data: unknown): DomainRow[] {
  if (!data || typeof data !== "object") return [];
  const raw = (data as { rules?: unknown; guardrails?: unknown }).rules ??
    (data as { guardrails?: unknown }).guardrails;
  let list: RuleRow[] = [];
  if (Array.isArray(raw)) {
    list = raw as RuleRow[];
  } else if (raw && typeof raw === "object") {
    list = Object.values(raw as Record<string, RuleRow>);
  }
  const buckets = new Map<string, DomainRow>();
  for (const rule of list) {
    if (!rule || typeof rule !== "object") continue;
    const domain =
      typeof rule.domain === "string" && rule.domain ? rule.domain : "other";
    const row = buckets.get(domain) ?? {
      domain,
      ok: 0,
      warning: 0,
      critical: 0,
      unknown: 0,
    };
    const status =
      typeof rule.last_status === "string" && rule.last_status
        ? rule.last_status
        : "unknown";
    if (status === "ok") row.ok += 1;
    else if (status === "warning") row.warning += 1;
    else if (status === "critical") row.critical += 1;
    else row.unknown += 1;
    buckets.set(domain, row);
  }
  return Array.from(buckets.values()).sort((a, b) =>
    a.domain.localeCompare(b.domain),
  );
}
