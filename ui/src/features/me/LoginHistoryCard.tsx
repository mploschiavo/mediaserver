import { asArray } from "@/lib/coerce";
import { useCallback } from "react";
import { CheckCircle2, History, ShieldAlert, XCircle } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api";
import { ApiErrorTile } from "@/components/ApiErrorTile";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatRelative } from "@/features/media-integrity/format";
import {
  useMe,
  useMeLoginHistory,
  useThisWasntMe,
  type LoginHistoryEntry,
} from "./hooks";

/**
 * Confirmation prompt shown before posting "this wasn't me" so the
 * operator understands the consequence: the controller will revoke
 * every live session belonging to the caller (including the current
 * one, signing them out everywhere) and write an
 * ``ANOMALY_CREDENTIAL_STUFFING`` audit entry that the admin sees in
 * the security report.  Without this gate the click looks dead — the
 * operator gets logged out and has no idea why.  Module-level so
 * tests can stub it directly without re-rendering the component.
 */
const REPORT_CONFIRM_PROMPT =
  "Report this sign-in as suspicious? This will sign you out of every " +
  "device immediately and notify your admin via the audit log.";

function entryId(e: LoginHistoryEntry, i: number): string {
  return String(e.id ?? e.timestamp ?? e.ts ?? i);
}

function entryWhen(e: LoginHistoryEntry): string {
  return e.timestamp ?? e.ts ?? "";
}

function isFailed(e: LoginHistoryEntry): boolean {
  const result = typeof e.result === "string" ? e.result.toLowerCase() : "";
  const action = typeof e.action === "string" ? e.action.toLowerCase() : "";
  if (result.includes("fail") || result.includes("denied")) return true;
  if (action.includes("fail") || action.includes("denied")) return true;
  return false;
}

function locationOf(e: LoginHistoryEntry): string {
  if (typeof e.location === "string" && e.location) return e.location;
  const detail = e.detail;
  if (detail && typeof detail === "object") {
    const rec = detail as Record<string, unknown>;
    if (typeof rec.location === "string") return rec.location;
    if (typeof rec.country === "string") return rec.country;
    if (typeof rec.city === "string") return rec.city;
  }
  return "";
}

function deviceOf(e: LoginHistoryEntry): string {
  if (typeof e.user_agent === "string" && e.user_agent) return e.user_agent;
  const detail = e.detail;
  if (detail && typeof detail === "object") {
    const rec = detail as Record<string, unknown>;
    if (typeof rec.user_agent === "string") return rec.user_agent;
    if (typeof rec.device === "string") return rec.device;
    if (typeof rec.device_class === "string") return rec.device_class;
  }
  return "";
}

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return fallback;
}

/**
 * Mobile-friendly stack of recent login events. Each entry can be
 * flagged as "this wasn't me", which opens the audit-trail incident.
 * Renders as a vertical list (Card-per-row) rather than a dense table
 * so that small viewports don't need horizontal scroll.
 */
export function LoginHistoryCard() {
  const me = useMe();
  const userId = typeof me.data?.id === "string" ? me.data.id : undefined;
  const history = useMeLoginHistory(userId);
  const thisWasntMe = useThisWasntMe();

  const entries = asArray(history.data?.entries);

  const handleReport = useCallback(
    (entry: LoginHistoryEntry) => {
      if (thisWasntMe.isPending) return;
      // Ask before nuking every session — see REPORT_CONFIRM_PROMPT
      // for why this gate exists.  ``window.confirm`` is intentional:
      // a modal would be more polished but the action is rare and
      // the consequence is irreversible enough that a hard sync
      // prompt beats a passively-dismissable banner.  In headless
      // test envs ``window.confirm`` is mocked to ``true`` by
      // ``renderWithProviders``; in JSDOM it returns ``true``
      // unconditionally — both keep existing tests green.
      if (
        typeof window !== "undefined" &&
        typeof window.confirm === "function" &&
        !window.confirm(REPORT_CONFIRM_PROMPT)
      ) {
        return;
      }
      const when = entryWhen(entry);
      const body: {
        session_id?: string;
        audit_id?: string;
        login_timestamp?: string;
        flagged_ip?: string;
      } = {};
      if (typeof entry.id === "string") body.audit_id = entry.id;
      if (when) body.login_timestamp = when;
      if (typeof entry.ip === "string") body.flagged_ip = entry.ip;
      thisWasntMe.mutate(body, {
        onSuccess: () =>
          toast.success(
            "Reported. Signing you out of every device — your admin has been alerted.",
          ),
        onError: (err) => toast.error(errMsg(err, "Report failed")),
      });
    },
    [thisWasntMe],
  );

  return (
    <Card data-testid="login-history-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History aria-hidden className="size-4" />
          Login history
        </CardTitle>
        <CardDescription>Recent sign-ins on your account.</CardDescription>
      </CardHeader>
      <CardContent>
        {me.isLoading || history.isLoading ? (
          <div
            className="space-y-2"
            data-testid="login-history-card-loading"
          >
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : history.error ? (
          <div data-testid="login-history-card-error">
            <ApiErrorTile
              error={history.error}
              onRetry={() => void history.refetch()}
            />
          </div>
        ) : entries.length === 0 ? (
          <div
            className="flex flex-col gap-1 text-sm text-fg-muted"
            data-testid="login-history-card-empty"
          >
            <span>No recent logins recorded.</span>
            <span className="text-xs text-fg-faint">
              Note: this surface only records signin events captured
              by the controller's own audit log. Sessions established
              via Authelia (the default flow) aren't currently
              recorded here — tracked as a follow-up. Failed logins
              against the controller's own /api/login endpoint do
              show up.
            </span>
          </div>
        ) : (
          <ul className="flex flex-col gap-2" role="list">
            {entries.map((e, i) => {
              const id = entryId(e, i);
              const failed = isFailed(e);
              const when = entryWhen(e);
              const loc = locationOf(e);
              const device = deviceOf(e);
              return (
                <li key={id}>
                  <div
                    className="flex flex-col gap-2 rounded-md border border-border bg-bg-1 p-3 sm:flex-row sm:items-start sm:justify-between"
                    data-testid={`login-history-row-${id}`}
                  >
                    <div className="min-w-0 flex flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        {failed ? (
                          <Badge variant="danger" className="gap-1">
                            <XCircle aria-hidden className="size-3" />
                            Failed
                          </Badge>
                        ) : (
                          <Badge variant="success" className="gap-1">
                            <CheckCircle2 aria-hidden className="size-3" />
                            Success
                          </Badge>
                        )}
                        <span className="text-xs tabular-nums text-fg-muted">
                          {formatRelative(when)}
                        </span>
                      </div>
                      <div className="flex flex-col gap-0.5 text-xs text-fg-muted">
                        {e.ip ? (
                          <span className="font-mono">
                            {e.ip}
                            {loc ? ` · ${loc}` : ""}
                          </span>
                        ) : null}
                        {device ? (
                          <span className="truncate">{device}</span>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex items-center sm:pt-0.5">
                      <button
                        type="button"
                        onClick={() => handleReport(e)}
                        disabled={thisWasntMe.isPending}
                        className="text-xs text-danger underline-offset-2 [@media(hover:hover)]:hover:underline disabled:opacity-50"
                        data-testid={`login-history-wasnt-me-${id}`}
                        aria-label={`Report login at ${when || "unknown time"} as not mine — signs you out of every device`}
                        title="Signs you out of every device and alerts your admin via the audit log."
                      >
                        <span className="inline-flex items-center gap-1">
                          <ShieldAlert aria-hidden className="size-3.5" />
                          This wasn't me
                        </span>
                      </button>
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
