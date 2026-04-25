import { useMemo, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/layout/EmptyState";
import { useFailedServices, type FailedServiceEntry } from "./hooks";

interface FailedRow {
  id: string;
  service: string;
  reason: string;
  since: string;
}

function formatRelative(value: string | undefined, sinceTs?: number): string {
  let t: number;
  if (sinceTs && Number.isFinite(sinceTs)) {
    t = sinceTs * 1000;
  } else if (value) {
    t = Date.parse(value);
    if (!Number.isFinite(t)) return value;
  } else {
    return "—";
  }
  const delta = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function normalize(entry: FailedServiceEntry, idx: number): FailedRow {
  if (typeof entry === "string") {
    return {
      id: entry || `failed-${idx}`,
      service: entry,
      reason: "",
      since: "—",
    };
  }
  const id = entry.service_id ?? entry.service ?? `failed-${idx}`;
  return {
    id,
    service: entry.service_id ?? entry.service ?? id,
    reason: entry.reason ?? "",
    since: formatRelative(entry.since, entry.since_ts),
  };
}

const REASON_TRUNCATE = 80;

function FailedRowItem({ row }: { row: FailedRow }) {
  const [expanded, setExpanded] = useState(false);
  const truncated =
    row.reason.length > REASON_TRUNCATE
      ? `${row.reason.slice(0, REASON_TRUNCATE - 1)}…`
      : row.reason;
  const showToggle = row.reason.length > REASON_TRUNCATE;

  return (
    <li
      className="flex flex-col gap-1 rounded-md border border-border bg-bg-1/40 p-3"
      data-testid={`failed-${row.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <span className="font-medium text-fg">{row.service}</span>
        <span className="shrink-0 tabular-nums text-xs text-fg-muted">
          {row.since}
        </span>
      </div>
      {row.reason ? (
        <div className="flex items-start gap-1.5 text-xs text-fg-muted">
          {showToggle ? (
            <button
              type="button"
              className="mt-0.5 inline-flex shrink-0 items-center text-fg-muted hover:text-fg"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              data-testid={`failed-toggle-${row.id}`}
            >
              {expanded ? (
                <ChevronDown aria-hidden className="size-3" />
              ) : (
                <ChevronRight aria-hidden className="size-3" />
              )}
            </button>
          ) : null}
          <span
            className={
              expanded
                ? "whitespace-pre-wrap break-words"
                : "block truncate"
            }
            title={row.reason}
          >
            {expanded ? row.reason : truncated}
          </span>
        </div>
      ) : null}
    </li>
  );
}

export function FailedServicesCard() {
  const query = useFailedServices();

  const rows = useMemo<FailedRow[]>(() => {
    // The controller's `failed_services` payload has shifted shape
    // across versions: array of strings, array of objects, or an
    // object map keyed by service-id. Normalise to a sane list
    // before .map() so a re-fetch with a different shape doesn't
    // crash the panel mid-session.
    const raw: unknown = query.data?.failed_services;
    const list: unknown[] = Array.isArray(raw)
      ? raw
      : raw && typeof raw === "object"
        ? Object.entries(raw as Record<string, unknown>).map(
            ([service_id, value]) =>
              value && typeof value === "object"
                ? { service_id, ...(value as object) }
                : { service_id, reason: String(value ?? "") },
          )
        : [];
    return list.map((e, i) => normalize(e as Parameters<typeof normalize>[0], i));
  }, [query.data]);

  return (
    <Card data-testid="failed-services-card">
      <CardHeader>
        <CardTitle>Failed services</CardTitle>
        <CardDescription>
          Services past the failure threshold and the reason recorded by the
          controller
        </CardDescription>
      </CardHeader>
      <CardContent>
        {query.isLoading ? (
          <div
            className="flex flex-col gap-2"
            data-testid="failed-services-loading"
          >
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : query.error ? (
          <div
            role="alert"
            data-testid="failed-services-error"
            className="text-sm text-danger"
          >
            {query.error.message}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="No failed services"
            description="Every monitored service is below the failure threshold."
          />
        ) : (
          <ul
            className="flex flex-col gap-2"
            data-testid="failed-services-list"
          >
            {rows.map((row) => (
              <FailedRowItem key={row.id} row={row} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
