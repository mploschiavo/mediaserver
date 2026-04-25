import { useMemo } from "react";
import { Globe, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/layout/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { useDnsCheck, type DnsCheckEntry } from "./hooks";

interface DnsRow {
  hostname: string;
  ips: readonly string[];
  status: "ok" | "missing" | "conflict" | "unknown";
  error: string;
}

function strList(v: unknown): readonly string[] {
  if (!Array.isArray(v)) return [];
  const out: string[] = [];
  for (const x of v) {
    if (typeof x === "string") out.push(x);
  }
  return out;
}

function statusFrom(entry: DnsCheckEntry): DnsRow["status"] {
  const s = entry.status;
  if (s === "ok" || s === "missing" || s === "conflict") return s;
  // Heuristic fallback: missing IPs → "missing"; otherwise "ok".
  const ips = strList(entry.resolved ?? entry.ips);
  if (entry.error) return "missing";
  return ips.length === 0 ? "missing" : "ok";
}

function buildRow(entry: DnsCheckEntry): DnsRow {
  const hostname =
    typeof entry.hostname === "string"
      ? entry.hostname
      : typeof entry.host === "string"
        ? entry.host
        : "(unknown)";
  return {
    hostname,
    ips: strList(entry.resolved ?? entry.ips),
    status: statusFrom(entry),
    error: typeof entry.error === "string" ? entry.error : "",
  };
}

function rowsFromResult(result: unknown): DnsRow[] {
  if (!result || typeof result !== "object") return [];
  const obj = result as Record<string, unknown>;
  const candidates = [obj.entries, obj.results, obj.hostnames];
  for (const c of candidates) {
    if (Array.isArray(c)) {
      return c.map((e) => buildRow((e ?? {}) as DnsCheckEntry));
    }
  }
  // Fall back to map<hostname, entry>.
  const out: DnsRow[] = [];
  for (const [hostname, value] of Object.entries(obj)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const entry = value as DnsCheckEntry;
      if (!entry.hostname) entry.hostname = hostname;
      out.push(buildRow(entry));
    }
  }
  return out;
}

function statusVariant(status: DnsRow["status"]): "success" | "warning" | "danger" | "default" {
  switch (status) {
    case "ok":
      return "success";
    case "missing":
      return "warning";
    case "conflict":
      return "danger";
    default:
      return "default";
  }
}

export function DnsCheckCard() {
  const dns = useDnsCheck();
  const rows = useMemo(() => rowsFromResult(dns.data), [dns.data]);

  return (
    <Card data-testid="dns-check-card">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div>
          <CardTitle>DNS resolution</CardTitle>
          <CardDescription>
            Lightweight resolver probe across configured hostnames.
          </CardDescription>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void dns.refetch()}
          disabled={dns.isFetching}
          data-testid="dns-check-refresh"
        >
          <RefreshCw
            className={dns.isFetching ? "size-4 animate-spin" : "size-4"}
            aria-hidden
          />
          Re-check
        </Button>
      </CardHeader>
      <CardContent>
        {dns.error ? (
          <div
            role="alert"
            data-testid="dns-check-error"
            className="rounded-md border border-[color-mix(in_oklab,var(--color-danger)_30%,transparent)] bg-[color-mix(in_oklab,var(--color-danger)_10%,transparent)] p-3 text-sm text-danger"
          >
            <p className="font-medium">DNS check failed</p>
            <p className="mt-1 text-fg-muted">{dns.error.message}</p>
          </div>
        ) : dns.isLoading ? (
          <div className="space-y-2" data-testid="dns-check-loading">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={Globe}
            title="No hostnames configured"
            description="Configure a base domain in routing to populate the DNS-resolution table."
          />
        ) : (
          <ul
            className="flex flex-col divide-y divide-border rounded-md border border-border"
            data-testid="dns-check-rows"
          >
            {rows.map((row) => (
              <li
                key={row.hostname}
                className="flex flex-col gap-1 p-3 sm:flex-row sm:items-start sm:justify-between"
                data-testid={`dns-row-${row.hostname}`}
              >
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-sm text-fg">{row.hostname}</div>
                  {row.ips.length > 0 ? (
                    <div className="mt-1 flex flex-wrap gap-1 text-xs text-fg-muted">
                      {row.ips.map((ip) => (
                        <span key={ip} className="font-mono tabular-nums">
                          {ip}
                        </span>
                      ))}
                    </div>
                  ) : row.error ? (
                    <div className="mt-1 text-xs text-fg-muted">{row.error}</div>
                  ) : (
                    <div className="mt-1 text-xs text-fg-faint">no IPs returned</div>
                  )}
                </div>
                <div className="flex items-center justify-end">
                  <Badge variant={statusVariant(row.status)}>{row.status}</Badge>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
