"""Shared session-state singletons.

Having a dedicated module for the SessionStore instance (and related
helpers) lets server.py, handlers_get.py, and handlers_post.py all
import from it without creating a circular dependency.
"""

from __future__ import annotations

from media_stack.core.auth.session_store import SessionStore

SESSION_COOKIE_NAME = "ms_session"
SESSION_TTL_SECONDS = 8 * 60 * 60

session_store = SessionStore(default_ttl_seconds=SESSION_TTL_SECONDS)


class SessionCookieReader:
    """Extracts the session cookie from a handler and looks it up."""

    def username_for_handler(self, handler) -> str:
        """Return the owning username if a valid session cookie is
        present on the request, else ''."""
        headers = getattr(handler, "headers", None)
        if headers is None:
            return ""
        try:
            cookie_raw = headers.get("Cookie", "") or ""
        except AttributeError:
            return ""
        for chunk in cookie_raw.split(";"):
            if "=" not in chunk:
                continue
            k, _, v = chunk.strip().partition("=")
            if k != SESSION_COOKIE_NAME:
                continue
            sess = session_store.get(v.strip())
            if sess is not None:
                return sess.owner_username
        return ""


session_cookie_reader = SessionCookieReader()
