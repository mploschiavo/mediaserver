import { useCallback, useState } from "react";
import {
  AlertOctagon,
  CheckCircle2,
  Hash,
  ShieldCheck,
} from "lucide-react";
import { ApiError } from "@/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useAuditLogHead,
  useAuditLogVerify,
  type AuditLogVerifyShape,
} from "./hooks";

/**
 * Abbreviate a sha256 hex digest for display: first 8 + last 4
 * chars joined by an ellipsis. Returns "—" for empty input. The
 * full value lives on a tooltip so operators can copy it.
 */
export function abbreviateHash(hash: string): string {
  if (!hash) return "—";
  if (hash.length <= 14) return hash;
  return `${hash.slice(0, 8)}…${hash.slice(-4)}`;
}

interface VerifyState {
  ok: boolean;
  detail: string;
  /** Parsed entry index from the server's "entry N: ..." prefix. */
  brokenAt?: number;
}

/**
 * Parse the server's free-form `detail` string into a structured
 * verify result. The controller emits messages like
 * `entry 12: hash mismatch`; we lift the integer when present so
 * the UI can highlight the broken entry index.
 */
function parseVerify(result: AuditLogVerifyShape): VerifyState {
  const detail = result.detail ?? "";
  const match = /^entry\s+(\d+)\s*:/i.exec(detail);
  return {
    ok: result.ok,
    detail,
    ...(match?.[1] !== undefined ? { brokenAt: Number(match[1]) } : {}),
  };
}

/**
 * Top-of-page banner for the audit-log surface. Surfaces the
 * current chain head (length + abbreviated hash) and exposes a
 * "Verify chain" action that calls the O(n) verifier on demand.
 *
 * Style mirrors `features/media-integrity/StatusOverview` —
 * quiet card chrome, lucide icon next to a label, semantic
 * badge for the verify result.
 */
export function IntegrityBanner() {
  const head = useAuditLogHead();
  const verify = useAuditLogVerify();
  const [lastResult, setLastResult] = useState<VerifyState | null>(null);

  const handleVerify = useCallback(() => {
    if (verify.isPending) return;
    verify.mutate(undefined, {
      onSuccess: (result) => {
        setLastResult(parseVerify(result));
      },
      onError: (err) => {
        const msg =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Verify failed";
        setLastResult({ ok: false, detail: msg });
      },
    });
  }, [verify]);

  const headData = head.data;
  const headHash = headData?.hash ?? "";
  const headHeight = headData?.height ?? 0;

  return (
    <Card data-testid="integrity-banner">
      <CardContent className="flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-6">
          <div className="flex items-center gap-2 text-sm text-fg-muted">
            <ShieldCheck className="size-4" aria-hidden />
            <span>Chain head</span>
          </div>

          {head.isLoading ? (
            <Skeleton className="h-6 w-48" data-testid="integrity-head-loading" />
          ) : head.error ? (
            <span className="text-sm text-danger" data-testid="integrity-head-error">
              {head.error.message}
            </span>
          ) : (
            <div className="flex items-center gap-3" data-testid="integrity-head">
              <Tooltip>
                <TooltipTrigger asChild>
                  <span
                    className="flex cursor-default items-center gap-1 font-mono text-xs text-fg"
                    data-testid="integrity-head-hash"
                  >
                    <Hash className="size-3 text-fg-muted" aria-hidden />
                    {abbreviateHash(headHash)}
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  <span className="font-mono text-xs">{headHash || "(empty)"}</span>
                </TooltipContent>
              </Tooltip>
              <Badge variant="outline" data-testid="integrity-head-height">
                {headHeight} entr{headHeight === 1 ? "y" : "ies"}
              </Badge>
            </div>
          )}
        </div>

        <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
          {lastResult ? (
            lastResult.ok ? (
              <span
                className="flex items-center gap-2 text-sm text-success"
                data-testid="integrity-result-ok"
                role="status"
              >
                <CheckCircle2 className="size-4" aria-hidden />
                Chain intact ({headHeight} entr{headHeight === 1 ? "y" : "ies"})
              </span>
            ) : (
              <span
                className="flex flex-col gap-0.5 text-sm text-danger sm:flex-row sm:items-center sm:gap-2"
                data-testid="integrity-result-broken"
                role="alert"
              >
                <span className="flex items-center gap-2">
                  <AlertOctagon className="size-4" aria-hidden />
                  {lastResult.brokenAt !== undefined
                    ? `Chain broken at entry ${lastResult.brokenAt}`
                    : "Chain broken"}
                </span>
                {lastResult.detail ? (
                  <span
                    className="text-xs text-fg-muted"
                    data-testid="integrity-result-detail"
                  >
                    {lastResult.detail}
                  </span>
                ) : null}
              </span>
            )
          ) : null}

          <Button
            variant="secondary"
            size="sm"
            onClick={handleVerify}
            disabled={verify.isPending}
            loading={verify.isPending}
            data-testid="integrity-verify"
          >
            Verify chain
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
