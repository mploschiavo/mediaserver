"""Double-submit cookie CSRF protection.

For state-changing requests (POST/PUT/DELETE), the client must send the
same token both as a cookie (``media_stack_csrf``) and as a header
(``X-CSRF-Token``). A cross-site request can't read the cookie, so it
can't populate the header — the server rejects mismatches.

The token is generated lazily on GET and set as a cookie so the SPA can
read it client-side and echo it in headers.
"""

from __future__ import annotations

import hmac
import secrets
from http.cookies import SimpleCookie

_COOKIE_NAME = "media_stack_csrf"
_HEADER_NAME = "X-CSRF-Token"
_TOKEN_BYTES = 24
_MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


class CsrfProtector:
    """Stateless double-submit check. No server-side session state."""

    cookie_name = _COOKIE_NAME
    header_name = _HEADER_NAME

    def is_mutating_method(self, method: str) -> bool:
        return str(method or "").upper() in _MUTATING_METHODS

    def issue_token(self) -> str:
        return secrets.token_urlsafe(_TOKEN_BYTES)

    def extract_cookie(self, cookie_header: str) -> str:
        if not cookie_header:
            return ""
        try:
            jar = SimpleCookie()
            jar.load(cookie_header)
            morsel = jar.get(_COOKIE_NAME)
            return morsel.value if morsel else ""
        except Exception:  # noqa: BLE001
            return ""

    def verify(self, *, cookie_header: str, header_value: str) -> bool:
        cookie_token = self.extract_cookie(cookie_header)
        if not cookie_token or not header_value:
            return False
        return hmac.compare_digest(cookie_token, header_value)

    def build_set_cookie(self, token: str, *, secure: bool = False) -> str:
        parts = [f"{_COOKIE_NAME}={token}", "Path=/", "SameSite=Strict"]
        if secure:
            parts.append("Secure")
        return "; ".join(parts)
