"""POST paths exempt from the CSRF double-submit check.

Lifted from ``media_stack.api.handlers_post.PostRequestHandler.\
_CSRF_EXEMPT_POST_PATHS`` during ADR-0007 Phase 2 Phase E
(legacy-handler retirement).

Per ``bug_class_csrf_double_submit``: every POST outside this set
must echo ``X-CSRF-Token`` against the ``media_stack_csrf`` cookie.
The set lives in a dedicated module so the route-side CSRF gate
(``PostMutationGate``) and the contract tests pinning the legacy
exempt list have a single canonical site to consult.
"""

from __future__ import annotations


CSRF_EXEMPT_POST_PATHS: frozenset[str] = frozenset({
    # Arr webhook is from trusted internal services with a shared
    # secret elsewhere; it has no Cookie header and doesn't need CSRF.
    "/webhooks/arr",
    # Login establishes the session; before it runs there's no
    # cookie to compare against, so CSRF can't apply.
    "/api/auth/login",
    # Logout is idempotent (just revokes the cookie); same reason.
    "/api/auth/logout",
    # Refresh token itself is the credential; programmatic clients
    # won't have a Cookie header to CSRF against.
    "/api/tokens/refresh",
})


__all__ = ["CSRF_EXEMPT_POST_PATHS"]
