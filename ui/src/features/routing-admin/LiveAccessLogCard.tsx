import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, ShieldOff } from "lucide-react";
import { fetcher } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";
import { useAddIpBan } from "@/features/bans/hooks";

interface AccessLogRow {
  ts?: string;
  method?: string;
  path?: string;
  status?: number;
  upstream?: string;
  upstream_host?: string;
  duration_ms?: number;
  client_ip?: string;
  x_forwarded_for?: string;
  cf_connecting_ip?: string;
  x_real_ip?: string;
  host?: string;
  user_agent?: string;
  country?: string;
  flag?: string;
  raw?: string;
}

interface AccessLogResponse {
  rows: AccessLogRow[];
  limit: number;
}

const REFRESH_OPTIONS = [
  { label: "2s", ms: 2_000 },
  { label: "5s", ms: 5_000 },
  { label: "10s", ms: 10_000 },
  { label: "30s", ms: 30_000 },
  { label: "Off", ms: 0 },
] as const;

const HOST_FILTER_ALL = "__all__";

/**
 * Live request tail with full HTTP context — source IP + country
 * flag (when public), method, path, status, upstream cluster,
 * latency. Polls ``/api/envoy/access-log?limit=N`` at the
 * operator-selected interval (2s / 5s / 10s / 30s / Off).
 *
 * Two filter affordances:
 *   * Refresh-rate dropdown matches the Edge gateway summary card
 *     (Grafana-style cadence selector).
 *   * Per-host tab strip — quick "show only jf.iomio.io" without
 *     manually scanning the table. The "All" tab is the default;
 *     a host appears once it's been seen in the buffer.
 *
 * Per-row "Block IP" action lives on the right edge — one click
 * adds the IP to the existing /api/bans/ips list with a default
 * reason of "Blocked from live request tail at <timestamp>".
 */
export function LiveAccessLogCard() {
  const [intervalMs, setIntervalMs] = useState<number>(5_000);
  const [hostFilter, setHostFilter] = useState<string>(HOST_FILTER_ALL);

  const q = useQuery<AccessLogResponse>({
    queryKey: ["routing", "envoy", "access-log"],
    queryFn: () =>
      fetcher<AccessLogResponse>("api/envoy/access-log?limit=50"),
    refetchInterval: intervalMs > 0 ? intervalMs : false,
    staleTime: Math.max(1_000, Math.floor(intervalMs / 2) || 1_000),
  });

  const allRows = useMemo(
    () => [...(q.data?.rows ?? [])].reverse(),
    [q.data?.rows],
  );

  const knownHosts = useMemo(() => {
    const set = new Set<string>();
    for (const r of allRows) if (r.host) set.add(r.host);
    return [...set].sort();
  }, [allRows]);

  const rows = useMemo(() => {
    if (hostFilter === HOST_FILTER_ALL) return allRows;
    return allRows.filter((r) => r.host === hostFilter);
  }, [allRows, hostFilter]);

  if (q.isLoading) {
    return (
      <Card data-testid="live-access-log-loading">
        <CardHeader>
          <CardTitle>Live request tail</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full rounded-md" />
        </CardContent>
      </Card>
    );
  }

  if (q.error) {
    return (
      <Card data-testid="live-access-log-error" role="alert">
        <CardHeader>
          <CardTitle>Live request tail</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-danger">
            Couldn't read the access log:{" "}
            {(q.error as Error).message}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="live-access-log">
      <CardHeader className="flex flex-col gap-2">
        <div className="flex flex-row items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <CardTitle className="flex items-center gap-2">
              <Activity className="size-4 text-success" aria-hidden />
              Live request tail
              {intervalMs > 0 ? (
                <span
                  className="inline-block size-1.5 animate-pulse rounded-full bg-success"
                  aria-hidden
                />
              ) : null}
            </CardTitle>
            <CardDescription>
              Last {q.data?.limit ?? 50} HTTP requests from Envoy's
              access log. Source IP + country flag (when public),
              method, path, status, upstream cluster, latency.
            </CardDescription>
          </div>
          <RefreshSelector value={intervalMs} onChange={setIntervalMs} />
        </div>
        {knownHosts.length > 1 ? (
          <HostFilterTabs
            hosts={knownHosts}
            value={hostFilter}
            onChange={setHostFilter}
            counts={hostFilter === HOST_FILTER_ALL ? allRows.length : rows.length}
          />
        ) : null}
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState hostFilter={hostFilter} />
        ) : (
          <ul
            className="flex flex-col divide-y divide-border/40"
            data-testid="live-access-log-rows"
          >
            {rows.map((r, idx) => (
              <Row
                key={`${r.ts ?? ""}-${idx}`}
                row={r}
                isFirst={idx === 0}
                rowIdx={idx}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function RefreshSelector({
  value,
  onChange,
}: {
  value: number;
  onChange: (ms: number) => void;
}) {
  return (
    <label
      className="flex items-center gap-2 text-xs text-fg-muted"
      data-testid="live-access-log-refresh-selector"
    >
      <span className="hidden sm:inline">Refresh</span>
      <select
        className="rounded-md border border-border bg-bg-1 px-2 py-1 text-xs text-fg focus:outline-none focus:ring-2 focus:ring-ring"
        value={value}
        onChange={(e) => onChange(Number(e.currentTarget.value))}
        aria-label="Access log refresh interval"
      >
        {REFRESH_OPTIONS.map((opt) => (
          <option key={opt.label} value={opt.ms}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function HostFilterTabs({
  hosts,
  value,
  onChange,
  counts,
}: {
  hosts: string[];
  value: string;
  onChange: (host: string) => void;
  counts: number;
}) {
  return (
    <div
      className="flex flex-wrap gap-1"
      data-testid="live-access-log-host-tabs"
    >
      <HostTab
        active={value === HOST_FILTER_ALL}
        onClick={() => onChange(HOST_FILTER_ALL)}
        testid="live-access-log-host-all"
      >
        All ({counts})
      </HostTab>
      {hosts.map((h) => (
        <HostTab
          key={h}
          active={value === h}
          onClick={() => onChange(h)}
          testid={`live-access-log-host-${h}`}
        >
          {h}
        </HostTab>
      ))}
    </div>
  );
}

function HostTab({
  active,
  onClick,
  testid,
  children,
}: {
  active: boolean;
  onClick: () => void;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-2 py-0.5 text-[11px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-info bg-info/10 text-info"
          : "border-border bg-bg-1 text-fg-muted hover:bg-bg-2",
      )}
      data-testid={testid}
      aria-pressed={active}
    >
      {children}
    </button>
  );
}

function EmptyState({ hostFilter }: { hostFilter: string }) {
  if (hostFilter !== HOST_FILTER_ALL) {
    return (
      <div
        className="rounded-md border border-dashed border-border p-4 text-center text-sm text-fg-muted"
        data-testid="live-access-log-empty-host"
      >
        No requests for <code className="text-fg">{hostFilter}</code> in the
        current buffer.
      </div>
    );
  }
  return (
    <div
      className="rounded-md border border-dashed border-border p-4 text-center text-sm text-fg-muted"
      data-testid="live-access-log-empty"
    >
      No access-log entries available. The controller looked at
      <code className="mx-1 rounded bg-bg-2 px-1 py-0.5 text-xs">
        ENVOY_ACCESS_LOG_PATH
      </code>
      , the Kubernetes API, and{" "}
      <code className="mx-1 rounded bg-bg-2 px-1 py-0.5 text-xs">
        docker compose logs
      </code>{" "}
      and got nothing back. Either the log isn't enabled or access is
      denied.
    </div>
  );
}

function Row({
  row,
  isFirst,
  rowIdx,
}: {
  row: AccessLogRow;
  isFirst: boolean;
  rowIdx: number;
}) {
  if (row.raw && row.method === undefined) {
    return (
      <li
        className="py-1 font-mono text-[11px] leading-tight text-fg-faint"
        data-testid={`live-access-log-row-${rowIdx}`}
      >
        <span className="truncate">{row.raw}</span>
      </li>
    );
  }
  return (
    <li
      className={cn(
        "flex flex-wrap items-center gap-2 py-1 font-mono text-[11px] leading-tight",
        isFirst && "bg-success/5",
      )}
      data-testid={`live-access-log-row-${rowIdx}`}
    >
      <ClientIpCell row={row} rowIdx={rowIdx} />
      <Badge variant="outline" className="w-12 justify-center text-[10px]">
        {row.method ?? "?"}
      </Badge>
      <span
        className="flex-1 min-w-[140px] truncate text-fg"
        title={hostAndPath(row)}
      >
        {row.path ?? "—"}
      </span>
      <StatusBadge status={row.status} />
      <span className="w-32 truncate text-fg-muted" title={row.upstream}>
        → {prettyUpstream(row.upstream)}
      </span>
      <span className="w-16 text-right tabular-nums text-fg-muted">
        {row.duration_ms !== undefined && row.duration_ms !== null
          ? `${row.duration_ms}ms`
          : "—"}
      </span>
      <BlockIpAction ip={row.client_ip} host={row.host} />
    </li>
  );
}

function ClientIpCell({ row, rowIdx }: { row: AccessLogRow; rowIdx: number }) {
  const display =
    row.client_ip ?? row.cf_connecting_ip ?? row.x_real_ip ?? "—";
  const flag = row.flag ?? "";
  const country = row.country ?? "";
  const hasChain =
    row.x_forwarded_for ||
    row.cf_connecting_ip ||
    row.x_real_ip ||
    row.client_ip;
  const inner = (
    <span
      className="flex w-28 shrink-0 items-center gap-1 truncate text-fg-faint"
      data-testid={`live-access-log-client-ip-${rowIdx}`}
    >
      {flag ? (
        <span aria-label={`country ${country}`} title={country}>
          {flag}
        </span>
      ) : (
        <span className="text-fg-faint" aria-hidden>
          {/* fixed-width placeholder so the column stays aligned */}
          ·
        </span>
      )}
      <span className="truncate">{display}</span>
    </span>
  );
  if (!hasChain) return inner;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{inner}</TooltipTrigger>
      <TooltipContent>
        <div className="flex flex-col gap-0.5 font-mono text-[11px]">
          {country ? (
            <div>
              <span className="text-fg-faint">country:</span>{" "}
              <span className="text-fg">
                {flag} {country}
              </span>
            </div>
          ) : null}
          {row.cf_connecting_ip ? (
            <div>
              <span className="text-fg-faint">cf:</span>{" "}
              <span className="text-fg">{row.cf_connecting_ip}</span>
            </div>
          ) : null}
          {row.x_real_ip ? (
            <div>
              <span className="text-fg-faint">x-real-ip:</span>{" "}
              <span className="text-fg">{row.x_real_ip}</span>
            </div>
          ) : null}
          {row.x_forwarded_for ? (
            <div>
              <span className="text-fg-faint">x-forwarded-for:</span>{" "}
              <span className="text-fg">{row.x_forwarded_for}</span>
            </div>
          ) : null}
          {row.host ? (
            <div className="mt-1 border-t border-border pt-1">
              <span className="text-fg-faint">host:</span>{" "}
              <span className="text-fg">{row.host}</span>
            </div>
          ) : null}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function BlockIpAction({ ip, host }: { ip?: string; host?: string }) {
  const mut = useAddIpBan();
  const [confirming, setConfirming] = useState(false);

  // Don't offer the action for non-public IPs — banning your own LAN
  // is almost always a mistake.
  const canBan = ip && isPublicIpString(ip);
  if (!canBan) return <span className="w-7" aria-hidden />;

  const handleClick = () => {
    if (!confirming) {
      setConfirming(true);
      // Auto-cancel the confirm prompt after 3s if not clicked again.
      window.setTimeout(() => setConfirming(false), 3_000);
      return;
    }
    mut.mutate({
      cidr: `${ip}/32`,
      reason: `Blocked from live request tail${host ? ` (host=${host})` : ""}`,
    });
    setConfirming(false);
  };

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={handleClick}
          className={cn(
            "rounded p-1 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            confirming
              ? "bg-danger/20 text-danger"
              : "text-fg-faint hover:bg-bg-2 hover:text-danger",
          )}
          aria-label={`Block IP ${ip}`}
          disabled={mut.isPending}
          data-testid={`live-access-log-block-${ip}`}
        >
          <ShieldOff className="size-3" aria-hidden />
        </button>
      </TooltipTrigger>
      <TooltipContent>
        {confirming
          ? "Click again to confirm — adds to the IP ban list."
          : `Block ${ip} (CIDR /32)`}
      </TooltipContent>
    </Tooltip>
  );
}

function isPublicIpString(ip: string): boolean {
  // Cheap structural check: skip empty / multicast / private
  // ranges. The backend already filtered this for the country
  // lookup, but UI re-checks defensively so we don't ever offer
  // "Block 192.168.1.1" — that would self-DoS the operator.
  if (!ip) return false;
  const parts = ip.split(".").map((p) => Number(p));
  if (parts.length !== 4 || parts.some((p) => Number.isNaN(p) || p < 0 || p > 255)) {
    return false;
  }
  const a = parts[0] ?? 0;
  const b = parts[1] ?? 0;
  if (a === 10) return false;
  if (a === 127) return false;
  if (a === 169 && b === 254) return false;
  if (a === 172 && b >= 16 && b <= 31) return false;
  if (a === 192 && b === 168) return false;
  if (a === 0) return false;
  if (a >= 224) return false;
  return true;
}

function StatusBadge({ status }: { status?: number }) {
  if (status === undefined || status === null) {
    return (
      <Badge variant="outline" className="w-10 justify-center text-[10px]">
        —
      </Badge>
    );
  }
  const tone =
    status >= 500
      ? "danger"
      : status >= 400
        ? "warning"
        : status >= 300
          ? "info"
          : "success";
  return (
    <Badge
      variant="outline"
      data-tone={tone}
      className="w-10 justify-center tabular-nums text-[10px]"
      data-testid={`live-access-log-status-${status}`}
    >
      {status}
    </Badge>
  );
}

function prettyUpstream(name?: string): string {
  if (!name) return "—";
  if (name.startsWith("service_")) return name.slice("service_".length);
  return name;
}

function hostAndPath(row: AccessLogRow): string {
  const host = row.host ? `${row.host} ` : "";
  return `${host}${row.path ?? ""}`;
}
