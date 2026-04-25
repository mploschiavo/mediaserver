import { useMemo } from "react";
import { CheckCircle2, AlertTriangle, AlertOctagon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfigIntegrity, type IntegrityEntry } from "./hooks";

type RollupStatus = "ok" | "drift" | "broken";

interface Rollup {
  status: RollupStatus;
  driftCount: number;
  brokenCount: number;
  lastChecked: string;
}

function formatEpoch(seconds: number | undefined): string {
  if (!seconds || !Number.isFinite(seconds)) return "—";
  return new Date(seconds * 1000).toLocaleString();
}

interface FlaggedEntry {
  service: string;
  status: string;
  reason: string;
  file: string;
}

function flaggedList(
  services: Record<string, IntegrityEntry>,
): readonly FlaggedEntry[] {
  // Ratchet for "1 broken · 16 drift" mystery: the controller emits
  // status enum `ok | missing | corrupt | unknown`. Earlier versions
  // bucketed `unknown` (= "no config file declared in the registry,
  // nothing to check") into "drift", which surfaced 16 services as
  // drifted when in reality nothing had drifted — those services
  // simply have no config file. Now we render only the entries that
  // are genuinely flagged: missing, corrupt, drift (if the controller
  // ever emits it), error, fail, invalid. `unknown` and `skipped` are
  // treated as not-applicable and excluded.
  const ACTIONABLE = new Set([
    "missing",
    "corrupt",
    "drift",
    "drifted",
    "error",
    "fail",
    "invalid",
  ]);
  const out: FlaggedEntry[] = [];
  for (const [service, entry] of Object.entries(services)) {
    const status = (entry.status ?? "").toLowerCase();
    if (!ACTIONABLE.has(status)) continue;
    out.push({
      service,
      status,
      reason: entry.reason ?? "",
      file: entry.file ?? "",
    });
  }
  // Order: broken first (missing/corrupt/error), then drift, then alpha.
  const sev: Record<string, number> = {
    corrupt: 0,
    error: 0,
    fail: 0,
    invalid: 0,
    missing: 1,
    drift: 2,
    drifted: 2,
  };
  out.sort(
    (a, b) =>
      (sev[a.status] ?? 9) - (sev[b.status] ?? 9) ||
      a.service.localeCompare(b.service),
  );
  return out;
}

function summarize(
  services: Record<string, IntegrityEntry>,
  checkedAt: number | undefined,
): Rollup {
  let drift = 0;
  let broken = 0;
  for (const entry of Object.values(services)) {
    const status = (entry.status ?? "").toLowerCase();
    // "corrupt" / "missing" / "error" / "fail" / "invalid" → broken (red).
    if (
      status === "corrupt" ||
      status === "missing" ||
      status === "error" ||
      status === "fail" ||
      status === "invalid"
    ) {
      broken += 1;
      continue;
    }
    // Only "drift" / "drifted" actually mean drift. `unknown`
    // (registry has no config file declared) and `skipped` are
    // not-applicable and excluded from the rollup so the operator
    // doesn't see a misleading "16 drift" count.
    if (status === "drift" || status === "drifted") {
      drift += 1;
    }
  }
  const status: RollupStatus =
    broken > 0 ? "broken" : drift > 0 ? "drift" : "ok";
  return { status, driftCount: drift, brokenCount: broken, lastChecked: formatEpoch(checkedAt) };
}

function statusMeta(status: RollupStatus): {
  variant: "success" | "warning" | "danger";
  icon: typeof CheckCircle2;
  label: string;
} {
  switch (status) {
    case "ok":
      return { variant: "success", icon: CheckCircle2, label: "ok" };
    case "drift":
      return { variant: "warning", icon: AlertTriangle, label: "drift" };
    case "broken":
      return { variant: "danger", icon: AlertOctagon, label: "broken" };
  }
}

export function ConfigIntegrityCard() {
  const query = useConfigIntegrity();

  const services = query.data?.services ?? {};
  const rollup = useMemo<Rollup | null>(() => {
    if (!query.data) return null;
    return summarize(services, query.data.checked_at);
  }, [query.data, services]);
  const flagged = useMemo(() => flaggedList(services), [services]);

  return (
    <Card data-testid="config-integrity-card">
      <CardHeader>
        <CardTitle>Config integrity</CardTitle>
        <CardDescription>
          Per-service config-file drift detection
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {query.isLoading ? (
          <div data-testid="config-integrity-loading">
            <Skeleton className="h-6 w-2/3" />
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="config-integrity-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : !rollup ? (
          <div className="text-sm text-fg-muted">No data.</div>
        ) : (
          <>
            <StatusLine rollup={rollup} />
            {flagged.length > 0 ? (
              <FlaggedList entries={flagged} />
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function FlaggedList({ entries }: { entries: readonly FlaggedEntry[] }) {
  return (
    <ul
      className="flex flex-col gap-1.5 border-t border-border pt-3 text-sm"
      data-testid="config-integrity-flagged"
    >
      {entries.map((e) => {
        const isBroken =
          e.status === "missing" ||
          e.status === "corrupt" ||
          e.status === "error" ||
          e.status === "fail" ||
          e.status === "invalid";
        return (
          <li
            key={e.service}
            className="flex flex-wrap items-baseline gap-2"
            data-testid={`config-integrity-row-${e.service}`}
          >
            <Badge variant={isBroken ? "danger" : "warning"}>
              {e.status}
            </Badge>
            <span className="font-mono text-xs text-fg">{e.service}</span>
            {e.file ? (
              <span
                className="font-mono text-xs text-fg-faint"
                title={e.file}
              >
                {e.file}
              </span>
            ) : null}
            {e.reason ? (
              <span
                className="basis-full text-xs text-fg-muted"
                data-testid={`config-integrity-reason-${e.service}`}
              >
                {e.reason}
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function StatusLine({ rollup }: { rollup: Rollup }) {
  const meta = statusMeta(rollup.status);
  const iconClass =
    meta.variant === "danger"
      ? "size-4 text-danger"
      : meta.variant === "warning"
        ? "size-4 text-warning"
        : "size-4 text-success";
  return (
    <div
      className="flex flex-wrap items-center gap-3 text-sm"
      data-testid="config-integrity-status"
    >
      <meta.icon aria-hidden className={iconClass} />
      <Badge variant={meta.variant}>{meta.label}</Badge>
      {rollup.status !== "ok" ? (
        <span className="tabular-nums text-fg-muted">
          {rollup.brokenCount > 0
            ? `${rollup.brokenCount} broken`
            : null}
          {rollup.brokenCount > 0 && rollup.driftCount > 0 ? " · " : null}
          {rollup.driftCount > 0
            ? `${rollup.driftCount} drift`
            : null}
        </span>
      ) : (
        <span className="text-fg-muted">all configs verified</span>
      )}
      <span className="ml-auto text-xs tabular-nums text-fg-muted">
        Last checked {rollup.lastChecked}
      </span>
    </div>
  );
}
