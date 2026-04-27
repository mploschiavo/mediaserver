/**
 * Single source of truth for the Authelia portal URL.
 *
 * Two redirect targets use this module:
 *
 *   1. UserMenu "Sign out" → ``${portal}/logout?rd=<dashboard>``.
 *      Authelia's SPA handles ``/logout`` by POST-ing ``/api/logout``
 *      which invalidates the server-side session and emits the
 *      cookie-clearing ``Set-Cookie``.
 *   2. App.tsx 401 listener → ``${portal}/?rd=<original-url>``. Just
 *      sends the operator to the login form; no logout needed when
 *      the session is already expired.
 *   3. ApiErrorTile.SessionExpiredCta → same as App.tsx 401 listener.
 *   4. MfaCard / SessionsTable → ``${portal}/...`` for direct portal
 *      navigation (settings page, sessions list).
 *
 * Earlier code routed all of these through the path-prefix mount
 * (``/app/authelia/…``) and hard-coded the URL in 5+ places. The
 * Lua prefix filter that rewrites relative URLs for SPAs caught the
 * portal under the prefix and mangled its routing — operators
 * landed at ``/app/authelia/<some-route>`` with no working login
 * form. Routing through the dedicated portal host sidesteps the
 * rewrite entirely.
 *
 * The lookup convention is "auth.<base-domain>" — set in Authelia's
 * ``cookies.[].authelia_url`` so the session cookie's
 * ``Domain=<base>`` scope covers both the dashboard and the portal
 * subdomains. Operators who configured a non-standard portal name
 * can override via ``VITE_AUTH_PORTAL_URL`` at build time.
 *
 * The result is memoized — every consumer just calls
 * ``authPortal()`` and gets the same cached string. To reset (only
 * useful in tests) call ``__resetAuthPortalCache()``.
 */

let cached: string | null = null;

/** Internal — pure resolver. Exposed for the unit test that
 *  exercises hostname-derivation edge cases. */
export function resolveAuthPortalUrl(hostname: string): string {
  const override = (import.meta.env.VITE_AUTH_PORTAL_URL ?? "").trim();
  if (override) return override.replace(/\/$/, "");
  // Strip the leading subdomain ("m.iomio.io" → "iomio.io"). When the
  // hostname is bare or already starts with "auth." we fall back to
  // "auth.<as-is>" rather than slicing further.
  const parts = hostname.split(".");
  const base =
    parts.length >= 3 && parts[0] !== "auth"
      ? parts.slice(1).join(".")
      : hostname;
  const protocol =
    typeof window !== "undefined" && window.location?.protocol
      ? window.location.protocol
      : "https:";
  return `${protocol}//auth.${base}`;
}

/**
 * Get the Authelia portal URL (no trailing slash). Memoized — the
 * window's hostname doesn't change at runtime, so the first caller
 * pays the resolution cost and every subsequent caller hits the
 * cache. Callers that need a path off the portal append it
 * themselves: `${authPortal()}/logout`, `${authPortal()}/settings`.
 */
export function authPortal(): string {
  if (cached !== null) return cached;
  const hostname =
    typeof window !== "undefined" ? window.location.hostname : "localhost";
  cached = resolveAuthPortalUrl(hostname);
  return cached;
}

/** Test-only — reset the memoized value so per-test hostname
 *  overrides take effect. Production code never calls this. */
export function __resetAuthPortalCache(): void {
  cached = null;
}
