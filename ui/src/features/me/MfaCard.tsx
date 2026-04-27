import { ExternalLink, ShieldCheck, ShieldOff } from "lucide-react";
import { ApiErrorTile } from "@/components/ApiErrorTile";
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
import { formatRelative } from "@/features/media-integrity/format";
import { authPortal } from "@/lib/auth-portal";
import { useMeMfaState, type MeMfaState } from "./hooks";

function isEnabled(state: MeMfaState | undefined): boolean {
  if (!state) return false;
  if (typeof state.enabled === "boolean") return state.enabled;
  if (typeof state.enrolled === "boolean") return state.enrolled;
  return false;
}

function methodsLabel(state: MeMfaState | undefined): string {
  if (!state) return "";
  const factors = state.factors ?? [];
  const fromFactors = factors
    .map((f) => (typeof f.type === "string" ? f.type : ""))
    .filter((s) => s.length > 0);
  if (fromFactors.length > 0) return fromFactors.join(", ").toUpperCase();
  const enrolled = state.enrolled_methods ?? [];
  if (enrolled.length > 0) return enrolled.join(", ").toUpperCase();
  return "";
}

/**
 * Two-factor card for the /me route. This is a display-only surface:
 * MFA is administered in Authelia, so the "Manage" button kicks over
 * to Authelia's settings page rather than opening an inline enroll
 * flow. The badge color reflects the boolean enabled state.
 */
export function MfaCard() {
  const mfa = useMeMfaState();
  const enabled = isEnabled(mfa.data);
  const methods = methodsLabel(mfa.data);

  return (
    <Card data-testid="mfa-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {enabled ? (
            <ShieldCheck aria-hidden className="size-4 text-success" />
          ) : (
            <ShieldOff aria-hidden className="size-4 text-fg-muted" />
          )}
          Two-factor
        </CardTitle>
        <CardDescription>
          {mfa.isLoading ? (
            "Checking…"
          ) : enabled ? (
            <>Enabled{methods ? ` · ${methods}` : ""}</>
          ) : (
            "Not enabled"
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {mfa.isLoading ? (
          <Skeleton className="h-6 w-24" data-testid="mfa-card-loading" />
        ) : mfa.error ? (
          <div data-testid="mfa-card-error">
            <ApiErrorTile
              error={mfa.error}
              onRetry={() => void mfa.refetch()}
            />
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Badge
              variant={enabled ? "success" : "warning"}
              data-testid="mfa-card-badge"
            >
              {enabled ? "Enabled" : "Disabled"}
            </Badge>
            {mfa.data?.last_used_at ? (
              <span className="text-xs text-fg-muted">
                Last used {formatRelative(mfa.data.last_used_at)}
              </span>
            ) : null}
          </div>
        )}
        <Button variant="secondary" asChild data-testid="mfa-manage">
          <a href={`${authPortal()}/settings`}>
            Manage
            <ExternalLink aria-hidden className="size-3.5" />
          </a>
        </Button>
      </CardContent>
    </Card>
  );
}
