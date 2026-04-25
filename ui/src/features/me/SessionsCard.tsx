import { asArray } from "@/lib/coerce";
import { useCallback } from "react";
import { LogOut, ShieldAlert } from "lucide-react";
import { toast } from "sonner";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Device</TableHead>
                <TableHead>IP</TableHead>
                <TableHead>Last activity</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sessions.map((s) => {
                const id = sessionId(s);
                const isCurrent =
                  Boolean(s.current) || (currentId !== "" && id === currentId);
                return (
                  <TableRow key={id} data-testid={`session-row-${id}`}>
                    <TableCell>
                      <div className="flex flex-col gap-0.5">
                        <span className="flex items-center gap-2 font-medium text-fg">
                          {sessionDevice(s)}
                          {isCurrent ? (
                            <Badge variant="info">this session</Badge>
                          ) : null}
                        </span>
                        {s.provider ? (
                          <span className="text-xs text-fg-muted">
                            {s.provider}
                          </span>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs text-fg-muted">
                      {sessionIp(s)}
                    </TableCell>
                    <TableCell className="text-xs tabular-nums text-fg-muted">
                      {formatRelative(lastSeen(s))}
                    </TableCell>
                    <TableCell>
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
                            isCurrent ||
                            revokeOne.isPending ||
                            !userId ||
                            !id
                          }
                          data-testid={`session-signout-${id}`}
                        >
                          <LogOut aria-hidden className="size-3.5" />
                          Sign out
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
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
