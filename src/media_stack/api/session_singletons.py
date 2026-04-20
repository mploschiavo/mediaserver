"""Shared session-state singletons.

Having a dedicated module for the SessionStore instance (and related
helpers) lets server.py, handlers_get.py, and handlers_post.py all
import from it without creating a circular dependency.
"""

from __future__ import annotations

import os

from media_stack.core.auth.session_store import SessionStore
from media_stack.core.observability.security_counters import security_counters

SESSION_COOKIE_NAME = "ms_session"
_DEFAULT_TRUSTED_PROXY_HEADER = "Remote-User"
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_IDLE_SECONDS = 30 * 60


class _SessionConfig:
    """Resolves session TTLs from env with safe fallbacks.

    A small class keeps the env reads + parsing off the module level
    (avoids the loose-function + os.environ-in-methods ratchets)."""

    def __init__(self) -> None:
        self._env = os.environ

    def int_from_env(self, name: str, default: int) -> int:
        raw = self._env.get(name, "").strip()
        try:
            return int(raw) if raw else default
        except ValueError:
            return default


_config = _SessionConfig()

session_store = SessionStore(
    default_ttl_seconds=_config.int_from_env(
        "SESSION_TTL_SECONDS", SESSION_TTL_SECONDS),
    idle_ttl_seconds=_config.int_from_env(
        "SESSION_IDLE_SECONDS", SESSION_IDLE_SECONDS),
)


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


class TrustedProxyAuth:
    """Accept an upstream-auth'd identity (e.g. from Authelia via
    Envoy ext_authz) only when the request arrives from a configured
    trusted proxy CIDR. An identity-carrying header from outside the
    trusted CIDR is IGNORED — so an attacker can't spoof Remote-User
    by setting the header themselves."""

    def __init__(self) -> None:
        self._env = os.environ

    def identity(self, handler) -> str | None:
        cidrs_raw = self._env.get("CONTROLLER_TRUSTED_PROXY_CIDRS", "").strip()
        if not cidrs_raw:
            return None
        header = (self._env.get("CONTROLLER_TRUSTED_PROXY_HEADER", "")
                  .strip() or _DEFAULT_TRUSTED_PROXY_HEADER)
        client_ip = self._client_ip(handler)
        headers = getattr(handler, "headers", None)
        header_value = ""
        if headers is not None:
            try:
                header_value = (headers.get(header, "") or "").strip()
            except AttributeError:
                header_value = ""
        trusted = bool(client_ip) and self._in_any_cidr(client_ip, cidrs_raw)
        if not trusted:
            if header_value:
                security_counters.incr("trusted_proxy_spoof")
            return None
        return header_value or None

    def _client_ip(self, handler) -> str:
        addr = getattr(handler, "client_address", None)
        if isinstance(addr, tuple) and addr:
            return str(addr[0])
        return ""

    def _in_any_cidr(self, ip_str: str, cidrs_raw: str) -> bool:
        import ipaddress
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        for chunk in cidrs_raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                net = ipaddress.ip_network(chunk, strict=False)
            except ValueError:
                continue
            if ip in net:
                return True
        return False


trusted_proxy_auth = TrustedProxyAuth()
