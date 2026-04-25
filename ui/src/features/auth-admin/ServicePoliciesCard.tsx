import { useEffect, useState } from "react";
import { Save, ShieldQuestion } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { asObjectMap } from "@/lib/coerce";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import {
  useServicePolicies,
  useUpdateAuthConfig,
  type ServicePolicy,
} from "./hooks";

function explain(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

const POLICY_OPTIONS: readonly { value: ServicePolicy; label: string }[] = [
  { value: "bypass", label: "bypass" },
  { value: "one_factor", label: "one_factor" },
  { value: "two_factor", label: "two_factor" },
  { value: "native", label: "native" },
];

function policyVariant(
  policy: ServicePolicy,
): "success" | "warning" | "info" | "default" {
  if (policy === "two_factor") return "success";
  if (policy === "one_factor") return "info";
  if (policy === "bypass") return "warning";
  return "default";
}

/** Coerce one entry of the `services` map into a string policy. The
 *  controller has shipped two shapes: a flat `{svc: "two_factor"}` map
 *  and a nested `{svc: {policy: "two_factor", ...}}` form. We accept
 *  both; anything else collapses to "native". */
function coercePolicy(value: unknown): ServicePolicy {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const p = (value as { policy?: unknown }).policy;
    if (typeof p === "string") return p;
  }
  return "native";
}

interface ServiceRow {
  service: string;
  policy: ServicePolicy;
}

function rowsFromResponse(
  raw: Record<string, unknown> | undefined,
): readonly ServiceRow[] {
  const obj = asObjectMap(raw);
  return Object.entries(obj)
    .map(([service, value]) => ({ service, policy: coercePolicy(value) }))
    .sort((a, b) => a.service.localeCompare(b.service));
}

/**
 * Per-service auth policy editor. The shape on the wire is opaque
 * (`{services: {<svc>: ...}}`), so we coerce into a flat `{svc, policy}`
 * row list, let the operator edit each row's policy, then bulk-flush
 * via `POST /api/auth/config` (which accepts a partial `service_policies`
 * map merge).
 */
export function ServicePoliciesCard() {
  const policies = useServicePolicies();
  const update = useUpdateAuthConfig();

  const [draft, setDraft] = useState<Record<string, ServicePolicy>>({});
  const [seedKey, setSeedKey] = useState<string>("");

  // Re-seed local edits whenever the server returns a new snapshot.
  // Using a string-key (sorted entries) instead of the raw object so
  // React's structural-equality dependency check fires only when the
  // policy values actually change.
  useEffect(() => {
    const rows = rowsFromResponse(
      policies.data?.services as Record<string, unknown> | undefined,
    );
    const key = rows.map((r) => `${r.service}:${r.policy}`).join("|");
    if (key !== seedKey) {
      const next: Record<string, ServicePolicy> = {};
      for (const row of rows) next[row.service] = row.policy;
      setDraft(next);
      setSeedKey(key);
    }
  }, [policies.data, seedKey]);

  const rows: readonly ServiceRow[] = Object.entries(draft)
    .map(([service, policy]) => ({ service, policy }))
    .sort((a, b) => a.service.localeCompare(b.service));

  const dirty = (() => {
    const baseline = rowsFromResponse(
      policies.data?.services as Record<string, unknown> | undefined,
    );
    if (baseline.length !== rows.length) return true;
    for (const r of baseline) {
      if (draft[r.service] !== r.policy) return true;
    }
    return false;
  })();

  const handleChange = (service: string, policy: ServicePolicy) => {
    setDraft((prev) => ({ ...prev, [service]: policy }));
  };

  const handleSave = () => {
    update.mutate(
      { service_policies: draft },
      {
        onSuccess: () => toast.success("Service policies saved"),
        onError: (err) =>
          toast.error(`Save failed: ${explain(err, "request failed")}`),
      },
    );
  };

  const columns: ResponsiveTableColumn<ServiceRow>[] = [
    {
      id: "service",
      header: "Service",
      cell: (row) => (
        <span className="font-mono text-fg" data-testid={`policy-svc-${row.service}`}>
          {row.service}
        </span>
      ),
    },
    {
      id: "current",
      header: "Current",
      cell: (row) => (
        <Badge variant={policyVariant(row.policy)}>{row.policy}</Badge>
      ),
    },
    {
      id: "policy",
      header: "Policy",
      cell: (row) => (
        <Select
          value={
            POLICY_OPTIONS.some((o) => o.value === row.policy)
              ? row.policy
              : "native"
          }
          onValueChange={(v) => handleChange(row.service, v as ServicePolicy)}
        >
          <SelectTrigger
            className="h-8 w-36 text-xs"
            aria-label={`Policy for ${row.service}`}
            data-testid={`policy-select-${row.service}`}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {POLICY_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ),
    },
  ];

  return (
    <Card data-testid="service-policies-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div className="flex flex-col gap-1.5">
          <CardTitle className="flex items-center gap-2">
            <ShieldQuestion aria-hidden className="size-4 text-fg-muted" />
            Service auth policies
          </CardTitle>
          <CardDescription>
            Per-service Authelia access-control policy. `native` means the
            service handles auth itself.
          </CardDescription>
        </div>
        <Button
          type="button"
          variant="primary"
          size="sm"
          onClick={handleSave}
          loading={update.isPending}
          disabled={!dirty || policies.isLoading}
          data-testid="policy-save"
        >
          <Save aria-hidden /> Save
        </Button>
      </CardHeader>
      <CardContent className="p-0">
        {policies.isLoading ? (
          <div className="space-y-2 p-6" data-testid="policies-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : policies.error ? (
          <p
            role="alert"
            className="px-6 py-4 text-sm text-danger"
            data-testid="policies-error"
          >
            {policies.error.message}
          </p>
        ) : rows.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={ShieldQuestion}
              title="No services registered"
              description="Service policies appear here once the controller publishes its registry."
            />
          </div>
        ) : (
          <ResponsiveTable
            rows={[...rows]}
            rowKey={(r) => r.service}
            columns={columns}
            card={(row) => (
              <div className="flex items-center justify-between gap-3">
                <div className="flex flex-col">
                  <span className="font-mono text-fg">{row.service}</span>
                  <Badge variant={policyVariant(row.policy)}>
                    {row.policy}
                  </Badge>
                </div>
                <Select
                  value={
                    POLICY_OPTIONS.some((o) => o.value === row.policy)
                      ? row.policy
                      : "native"
                  }
                  onValueChange={(v) =>
                    handleChange(row.service, v as ServicePolicy)
                  }
                >
                  <SelectTrigger
                    className="h-8 w-36 text-xs"
                    aria-label={`Policy for ${row.service}`}
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {POLICY_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          />
        )}
      </CardContent>
    </Card>
  );
}
