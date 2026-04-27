import { LogIn, ShieldAlert, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/api/client";
import { authPortal } from "@/lib/auth-portal";

interface ApiErrorTileProps {
  /** The error from a TanStack Query / fetcher call. */
  error: unknown;
  /** Optional retry handler — wires up a "Try again" button. */
  onRetry?: () => void;
  /** Compact variant fits inside small tiles; default is full card. */
  variant?: "card" | "inline";
  /** Override the redirect target for the sign-in button. */
  signInPath?: string;
}

/**
 * Universal renderer for fetcher errors.
 *
 * Why this exists: half a dozen tiles across the dashboard render
 * raw `error.message` from a TanStack Query, which produces strings
 * like `"HTTP 404"` even when the underlying status was `401`
 * ("session expired"). Operators see "HTTP 404" and assume the
 * endpoint is broken — when actually they just need to sign in.
 *
 * The rule:
 *
 *   * `status == 401` → "Session expired" + Sign in button.
 *     Cookie cleared client-side before the redirect so a stale
 *     authelia_session cookie can't loop the portal.
 *   * `status == 403` → "Insufficient permissions" + Retry button.
 *   * `status == 404` → "Not found".
 *   * `status >= 500` → "Server error: ${message}" + Retry button.
 *   * everything else → message + Retry button (network / parse /
 *     timeout).
 *
 * Replaces ad-hoc `<div className="text-danger">{error.message}</div>`
 * patterns across the codebase. Search for those after this lands and
 * migrate them — the structural ratchet
 * (`tests/no-raw-api-error-display-ratchet`) flags new offenders.
 */
export function ApiErrorTile({
  error,
  onRetry,
  variant = "card",
  signInPath,
}: ApiErrorTileProps) {
  const status = error instanceof ApiError ? error.status : undefined;
  const message =
    error instanceof Error ? error.message : "Unknown error";

  if (status === 401) {
    return (
      <SessionExpiredCta variant={variant} signInPath={signInPath} />
    );
  }
  if (status === 403) {
    return (
      <Frame variant={variant} tone="warning" testid="api-error-tile-403">
        <ShieldAlert
          className="size-4 shrink-0 text-warning"
          aria-hidden
        />
        <div className="flex flex-col gap-1">
          <strong className="text-sm">Insufficient permissions</strong>
          <span className="text-xs text-fg-muted">
            Your account is signed in but not authorised for this
            view. Ask an admin to grant access.
          </span>
        </div>
        {onRetry ? <RetryButton onClick={onRetry} /> : null}
      </Frame>
    );
  }
  if (status === 404) {
    return (
      <Frame variant={variant} tone="muted" testid="api-error-tile-404">
        <div className="flex flex-col gap-1">
          <strong className="text-sm">Not found</strong>
          <span className="text-xs text-fg-muted">{message}</span>
        </div>
      </Frame>
    );
  }
  return (
    <Frame variant={variant} tone="danger" testid="api-error-tile-generic">
      <div className="flex flex-col gap-1">
        <strong className="text-sm">
          {status && status >= 500
            ? `Server error (${status})`
            : "Couldn't load"}
        </strong>
        <span className="text-xs text-fg-muted break-all">
          {message || "No detail provided."}
        </span>
      </div>
      {onRetry ? <RetryButton onClick={onRetry} /> : null}
    </Frame>
  );
}

function SessionExpiredCta({
  variant,
  signInPath,
}: {
  variant: "card" | "inline";
  signInPath?: string;
}) {
  const handleSignIn = () => {
    // ``authelia_session`` is HttpOnly so this clear is a no-op for
    // the load-bearing cookie. Kept only for the legacy
    // ``authelia_session_remember`` (non-HttpOnly).
    try {
      document.cookie =
        "authelia_session_remember=; Path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    } catch {
      // best-effort
    }
    // Pass the current URL as `?rd=` so the portal lands you back
    // here after re-auth. Default target is the dedicated portal
    // subdomain (auth.<base>) — matches the cookie's
    // ``Domain=<base>`` scope and avoids the path-prefix Lua filter
    // that mangles ``/app/authelia/...`` mounts.
    const here = window.location.pathname + window.location.search;
    const rd = encodeURIComponent(window.location.origin + here);
    const target = signInPath
      ? `${signInPath}?rd=${rd}`
      : `${authPortal()}/?rd=${rd}`;
    window.location.assign(target);
  };
  return (
    <Frame
      variant={variant}
      tone="info"
      testid="api-error-tile-401"
    >
      <LogIn className="size-4 shrink-0 text-info" aria-hidden />
      <div className="flex flex-col gap-1">
        <strong className="text-sm">Session expired</strong>
        <span className="text-xs text-fg-muted">
          You've been signed out. Sign in again to keep using the
          dashboard.
        </span>
      </div>
      <Button
        size="sm"
        onClick={handleSignIn}
        data-testid="api-error-tile-401-signin"
      >
        Sign in
      </Button>
    </Frame>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={onClick}
      data-testid="api-error-tile-retry"
    >
      <RefreshCw className="size-3" /> Try again
    </Button>
  );
}

function Frame({
  variant,
  tone,
  testid,
  children,
}: {
  variant: "card" | "inline";
  tone: "info" | "warning" | "danger" | "muted";
  testid: string;
  children: React.ReactNode;
}) {
  const toneClass = {
    info: "border-info/40 bg-info/10",
    warning: "border-warning/40 bg-warning/10",
    danger: "border-danger/40 bg-danger/10",
    muted: "border-border bg-bg-1/40",
  }[tone];
  if (variant === "inline") {
    return (
      <div
        className={`flex items-center gap-2 rounded-md border p-2 text-xs ${toneClass}`}
        role="alert"
        data-testid={testid}
      >
        {children}
      </div>
    );
  }
  return (
    <div
      className={`flex items-start gap-3 rounded-md border p-3 ${toneClass}`}
      role="alert"
      data-testid={testid}
    >
      {children}
    </div>
  );
}
