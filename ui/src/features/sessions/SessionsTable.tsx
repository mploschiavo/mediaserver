import { asArray } from "@/lib/coerce";
import { authPortal } from "@/lib/auth-portal";
import { useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Activity, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/layout/EmptyState";
import {
  ResponsiveTable,
  type ResponsiveTableColumn,
} from "@/components/layout/ResponsiveTable";
import { SkeletonTable } from "@/components/layout/SkeletonTable";
import {
  useActiveSessions,
  useRevokeSession,
  type SessionShape,
} from "./hooks";

interface SessionRow {
  id: string;
  username: string;
  provider: string;
  ip: string;
  userAgent: string;
  created: string;
  lastSeen: string;
  revokable: boolean;
  firstSeenIp: boolean;
  raw: SessionShape;
}

function formatTimestamp(iso?: string): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const delta = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (delta < 5) return "just now";
  if (delta < 60) return `${delta}s ago`;
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function truncate(value: string, max = 60): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max - 1)}…`;
}

function providerVariant(
  provider: string,
): "info" | "success" | "warning" | "default" | "outline" {
  switch (provider.toLowerCase()) {
    case "authelia":
      return "info";
    case "jellyfin":
      return "success";
    case "jellyseerr":
      return "warning";
    case "native":
      return "default";
    default:
      return "outline";
  }
}

function toRow(s: SessionShape, fallbackId: number): SessionRow {
  const id = s.session_id ?? `row-${fallbackId}`;
  return {
    id,
    username: s.username ?? "(anonymous)",
    provider: s.provider ?? "unknown",
    ip: s.client_ip ?? "—",
    userAgent: s.user_agent ?? s.client ?? "",
    created: s.connected_since ?? s.started_at ?? "",
    lastSeen: s.last_activity ?? s.last_seen_at ?? "",
    revokable: s.revokable !== false,
    firstSeenIp: s.first_seen_ip === true,
    raw: s,
  };
}

export function SessionsTable() {
  const reduce = useReducedMotion();
  const query = useActiveSessions();
  const revoke = useRevokeSession();
  const [pending, setPending] = useState<SessionRow | null>(null);

  const rows = useMemo<SessionRow[]>(() => {
    const list = asArray(query.data?.sessions);
    return list.map((s, i) => toRow(s, i));
  }, [query.data]);

  if (query.isLoading) {
    return (
      <div data-testid="sessions-loading">
        <SkeletonTable rows={6} columns={7} />
      </div>
    );
  }

  if (query.error) {
    return (
      <Card
        role="alert"
        data-testid="sessions-error"
        className="border-[color-mix(in_oklab,var(--color-danger)_40%,transparent)]"
      >
        <CardContent className="flex flex-col gap-3 p-6">
          <div className="flex items-center gap-2 text-danger">
            <AlertTriangle aria-hidden className="size-4" />
            <span className="font-medium">Could not load sessions</span>
          </div>
          <p className="text-sm text-fg-muted">{query.error.message}</p>
          <div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void query.refetch()}
              data-testid="sessions-retry"
            >
              Retry
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (rows.length === 0) {
    // The previous copy claimed nobody was signed in across all four
    // providers, but the controller's `SessionAggregator` only
    // enumerates its own session store + provider impls that are
    // actually registered. Under Authelia SSO the controller never
    // issues its own sessions, and the Authelia / Jellyfin /
    // Jellyseerr `SessionAdminProvider` impls aren't all wired yet
    // (tracked in v1.0.179). Be honest about the gap and link out to
    // the providers' own admin UIs so the operator can still get a
    // live view until the aggregation lands.
    return (
      <div className="flex flex-col gap-4">
        <EmptyState
          icon={Activity}
          title="No live sessions surfaced"
          description="Provider session lookup is enabled (controller v1.0.179+) — Authelia / Jellyfin / Jellyseerr `SessionAdminProvider` impls run on every request. An empty list means the providers couldn't enumerate: Authelia 4.38 file-backend cookies aren't externally listable, Jellyfin needs `JELLYFIN_API_KEY` populated in `media-stack-secrets`, and Jellyseerr has no upstream session-list API yet. Use the deep-links below to verify directly in each provider's admin UI."
        />
        <Card data-testid="sessions-fallback-links">
          <CardHeader>
            <CardTitle>Provider admin UIs (live view)</CardTitle>
            <CardDescription>
              Until the aggregator lands, view sessions directly in
              each provider's admin surface.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <Button asChild variant="secondary" size="sm">
              <a
                href={`${authPortal()}/`}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="sessions-link-authelia"
              >
                Authelia portal
              </a>
            </Button>
            <Button asChild variant="secondary" size="sm">
              <a
                href="/app/jellyfin/web/index.html#!/dashboard/activity"
                target="_blank"
                rel="noopener noreferrer"
                data-testid="sessions-link-jellyfin"
              >
                Jellyfin sessions
              </a>
            </Button>
            <Button asChild variant="secondary" size="sm">
              <a
                href="/app/jellyseerr/admin/users"
                target="_blank"
                rel="noopener noreferrer"
                data-testid="sessions-link-jellyseerr"
              >
                Jellyseerr users
              </a>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const columns: ResponsiveTableColumn<SessionRow>[] = [
    {
      id: "user",
      header: "User",
      cell: (row) => (
        <span className="font-medium text-fg">{row.username}</span>
      ),
    },
    {
      id: "provider",
      header: "Provider",
      cell: (row) => (
        <Badge variant={providerVariant(row.provider)}>{row.provider}</Badge>
      ),
    },
    {
      id: "ip",
      header: "IP",
      cell: (row) => (
        <span className="flex items-center gap-2">
          <span className="font-mono tabular-nums text-fg-muted">{row.ip}</span>
          {row.firstSeenIp ? (
            <Badge variant="warning" title="First time seen at this IP">
              new
            </Badge>
          ) : null}
        </span>
      ),
    },
    {
      id: "user-agent",
      header: "User-agent",
      cell: (row) => (
        <span
          className="block max-w-[18rem] truncate text-xs text-fg-muted"
          title={row.userAgent}
        >
          {truncate(row.userAgent, 60)}
        </span>
      ),
    },
    {
      id: "created",
      header: "Created",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">
          {formatTimestamp(row.created)}
        </span>
      ),
    },
    {
      id: "last-seen",
      header: "Last seen",
      cell: (row) => (
        <span className="tabular-nums text-fg-muted">
          {formatTimestamp(row.lastSeen)}
        </span>
      ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      cell: (row) =>
        row.revokable ? (
          <Button
            variant="danger"
            size="sm"
            onClick={() => setPending(row)}
            aria-label={`Revoke session for ${row.username}`}
            data-testid={`revoke-${row.id}`}
          >
            Revoke
          </Button>
        ) : (
          <span
            className="text-xs text-fg-faint"
            title="Provider does not support session revoke"
          >
            read-only
          </span>
        ),
    },
  ];

  const onConfirm = () => {
    if (!pending) return;
    const row = pending;
    setPending(null);
    revoke.mutate(
      {
        user_id: row.username,
        session_id: row.id,
        provider: row.provider,
      },
      {
        onSuccess: () => {
          toast.success(
            `Session for ${row.username} on ${row.provider} revoked.`,
          );
        },
        onError: (err) => {
          toast.error(`Revoke failed: ${err.message}`);
        },
      },
    );
  };

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      data-testid="sessions-table"
    >
      <Card className="p-0">
        <ResponsiveTable
          rows={rows}
          rowKey={(r) => r.id}
          columns={columns}
          card={(row) => (
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-fg">{row.username}</span>
                <Badge variant={providerVariant(row.provider)}>
                  {row.provider}
                </Badge>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <span className="text-fg-muted">IP</span>
                <span className="text-right font-mono tabular-nums">
                  {row.ip}
                  {row.firstSeenIp ? (
                    <Badge variant="warning" className="ml-1">
                      new
                    </Badge>
                  ) : null}
                </span>
                <span className="text-fg-muted">Created</span>
                <span className="text-right tabular-nums">
                  {formatTimestamp(row.created)}
                </span>
                <span className="text-fg-muted">Last seen</span>
                <span className="text-right tabular-nums">
                  {formatTimestamp(row.lastSeen)}
                </span>
              </div>
              <p
                className="text-xs text-fg-muted"
                title={row.userAgent}
              >
                {truncate(row.userAgent, 80)}
              </p>
              {row.revokable ? (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={() => setPending(row)}
                  aria-label={`Revoke session for ${row.username}`}
                  data-testid={`revoke-mobile-${row.id}`}
                >
                  Revoke
                </Button>
              ) : (
                <span className="text-xs text-fg-faint">read-only</span>
              )}
            </div>
          )}
        />
      </Card>

      <Dialog open={pending !== null} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent data-testid="revoke-dialog">
          <DialogHeader>
            <DialogTitle>Revoke session?</DialogTitle>
            <DialogDescription>
              {pending
                ? `This will sign ${pending.username} out of ${pending.provider}. They will need to authenticate again.`
                : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setPending(null)}
              data-testid="revoke-cancel"
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={onConfirm}
              data-testid="revoke-confirm"
            >
              Revoke session
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </motion.div>
  );
}
