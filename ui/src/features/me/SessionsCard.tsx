import { asArray } from "@/lib/coerce";
import { useCallback, useMemo } from "react";
import { LogOut, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import type { ColumnDef } from "@tanstack/react-table";
import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { DataTable } from "@/components/data-table";
import { formatRelative } from "@/features/media-integrity/format";
import {
  useMe,
  useMeSessions,
  useRevokeMySession,
  useRevokeOthers,
  useThisWasntMe,
  type MeSession,
} from "./hooks";

function sessionId(s: MeSession): string {
  return String(s.session_id ?? s.id ?? "");
}

/**
 * The controller surfaces a synth caller-row when the cross-provider
 * aggregate is empty but the operator is clearly authenticated (the
 * SSO case — see ``_synth_caller_session`` in
 * ``security_get_handlers.py``). Synth rows have ``revokable: false``
 * AND ``session_id: ""`` because the controller didn't mint the
 * underlying cookie — Authelia did, and the operator revokes via the
 * Authelia portal (or the user-menu sign-out), not via this list.
 * Detecting this lets us hide the "Sign out" / "This wasn't me"
 * affordances that have no session_id to act on, instead of letting
 * the operator click dead buttons.
 */
function isSynthRow(s: MeSession): boolean {
  if (s.revokable === false) return true;
  return sessionId(s) === "";
}

function sessionIp(s: MeSession): string {
  return s.client_ip ?? s.ip ?? "—";
}

function sessionDevice(s: MeSession): string {
  if (s.device) return s.device;
  if (s.device_class) return s.device_class;
  if (s.client) return s.client;
  return s.user_agent ?? "Unknown device";
}

function lastSeen(s: MeSession): string {
  return s.last_activity ?? s.last_seen_at ?? "";
}

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

interface SessionRow {
  session: MeSession;
  rowKey: string;
  isCurrent: boolean;
  synth: boolean;
}

/**
 * Sessions card for the /me route. Each row has a "Sign out" button
 * (per-session revoke) and a "This wasn't me" link that flips the
 * session to the audit-trail incident flow. A footer button triggers
 * the global "revoke all others" action.
 */
export function SessionsCard() {
  const me = useMe();
  const sessionsQuery = useMeSessions();
  const revokeOne = useRevokeMySession();
  const revokeOthers = useRevokeOthers();
  const thisWasntMe = useThisWasntMe();

  const userId = typeof me.data?.id === "string" ? me.data.id : "";
  const currentId = sessionsQuery.data?.current_session_id ?? "";
  const sessions = asArray(sessionsQuery.data?.sessions);

  const handleRevokeOne = useCallback(
    (id: string) => {
      if (!userId || !id || revokeOne.isPending) return;
      revokeOne.mutate(
        { userId, sessionId: id },
        {
          onSuccess: () => toast.success("Session signed out"),
          onError: (err) => toast.error(errMsg(err, "Sign-out failed")),
        },
      );
    },
    [revokeOne, userId],
  );

  const handleRevokeOthers = useCallback(() => {
    if (revokeOthers.isPending) return;
    revokeOthers.mutate(undefined, {
      onSuccess: (res) => {
        const n = typeof res?.revoked === "number" ? res.revoked : 0;
        toast.success(
          n > 0
            ? `Signed out ${n} other session${n === 1 ? "" : "s"}`
            : "No other sessions to sign out",
        );
      },
      onError: (err) => toast.error(errMsg(err, "Revoke failed")),
    });
  }, [revokeOthers]);

  const handleThisWasntMe = useCallback(
    (s: MeSession) => {
      if (thisWasntMe.isPending) return;
      const id = sessionId(s);
      thisWasntMe.mutate(
        { session_id: id, flagged_ip: sessionIp(s) },
        {
          onSuccess: () =>
            toast.success(
              "Reported. You'll be signed out everywhere momentarily.",
            ),
          onError: (err) => toast.error(errMsg(err, "Report failed")),
        },
      );
    },
    [thisWasntMe],
  );

  // Wrap each session in a row descriptor — TanStack Table works with
  // homogeneous rows, and we need stable testid keys for synth rows
  // (which have empty session_id) so we precompute rowKey here.
  const rows = useMemo<SessionRow[]>(() => {
    return sessions.map((s, i) => {
      const id = sessionId(s);
      const synth = isSynthRow(s);
      return {
        session: s,
        rowKey: id || `synth-${i}`,
        isCurrent:
          Boolean(s.current) ||
          synth ||
          (currentId !== "" && id === currentId),
        synth,
      };
    });
  }, [sessions, currentId]);

  const columns = useMemo<ColumnDef<SessionRow>[]>(
    () => [
      {
        id: "device",
        accessorFn: (r) => sessionDevice(r.session),
        header: "Device",
        meta: { label: "Device" },
        cell: ({ row }) => {
          const s = row.original.session;
          return (
            <div className="flex flex-col gap-0.5">
              <span className="flex items-center gap-2 font-medium text-fg">
                {sessionDevice(s)}
                {row.original.isCurrent ? (
                  <Badge variant="info">this session</Badge>
                ) : null}
              </span>
              {s.provider ? (
                <span className="text-xs text-fg-muted">{s.provider}</span>
              ) : null}
            </div>
          );
        },
      },
      {
        id: "ip",
        accessorFn: (r) => sessionIp(r.session),
        header: "IP",
        meta: { label: "IP" },
        cell: ({ row }) => (
          <span className="font-mono text-xs text-fg-muted">
            {sessionIp(row.original.session)}
          </span>
        ),
      },
      {
        id: "last_activity",
        accessorFn: (r) => lastSeen(r.session),
        header: "Last activity",
        meta: { label: "Last activity" },
        enableColumnFilter: false,
        cell: ({ row }) => (
          <span className="text-xs tabular-nums text-fg-muted">
            {formatRelative(lastSeen(row.original.session))}
          </span>
        ),
      },
      {
        id: "actions",
        header: "Actions",
        meta: { label: "Actions" },
        enableSorting: false,
        enableColumnFilter: false,
        cell: ({ row }) => {
          const { session: s, rowKey, synth, isCurrent } = row.original;
          const id = sessionId(s);
          if (synth) {
            return (
              <div
                className="flex items-center justify-end text-xs text-fg-muted"
                data-testid={`session-synth-help-${rowKey}`}
              >
                Sign out from the user menu
              </div>
            );
          }
          return (
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => handleThisWasntMe(s)}
                disabled={thisWasntMe.isPending}
                className="text-xs text-danger underline-offset-2 [@media(hover:hover)]:hover:underline disabled:opacity-50"
                data-testid={`session-wasnt-me-${id}`}
                aria-label={`Report session ${id} as not mine`}
              >
                <span className="inline-flex items-center gap-1">
                  <ShieldAlert aria-hidden className="size-3.5" />
                  This wasn't me
                </span>
              </button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => handleRevokeOne(id)}
                disabled={
                  isCurrent || revokeOne.isPending || !userId || !id
                }
                data-testid={`session-signout-${id}`}
              >
                <LogOut aria-hidden className="size-3.5" />
                Sign out
              </Button>
            </div>
          );
        },
      },
    ],
    [
      handleRevokeOne,
      handleThisWasntMe,
      revokeOne.isPending,
      thisWasntMe.isPending,
      userId,
    ],
  );

  return (
    <Card data-testid="sessions-card">
      <CardHeader>
        <CardTitle>Sessions</CardTitle>
        <CardDescription>Where you're signed in right now.</CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        {sessionsQuery.isLoading ? (
          <div className="space-y-2 p-6" data-testid="sessions-card-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : sessionsQuery.error ? (
          <div
            role="alert"
            data-testid="sessions-card-error"
            className="px-6 py-4 text-sm text-danger"
          >
            {sessionsQuery.error.message}
          </div>
        ) : sessions.length === 0 ? (
          <p
            className="px-6 py-4 text-sm text-fg-muted"
            data-testid="sessions-card-empty"
          >
            No active sessions.
          </p>
        ) : (
          <div className="px-6 pb-6">
            <DataTable<SessionRow>
              testId="session"
              columns={columns}
              data={rows}
              getRowId={(r) => r.rowKey}
              caption={`${rows.length} session${rows.length === 1 ? "" : "s"}`}
              emptyState="No active sessions."
            />
          </div>
        )}
      </CardContent>
      <div className="flex items-center justify-end border-t border-border px-6 py-4">
        <Button
          variant="secondary"
          onClick={handleRevokeOthers}
          loading={revokeOthers.isPending}
          disabled={revokeOthers.isPending || sessions.length <= 1}
          data-testid="signout-everywhere"
        >
          Sign out everywhere else
        </Button>
      </div>
    </Card>
  );
}
