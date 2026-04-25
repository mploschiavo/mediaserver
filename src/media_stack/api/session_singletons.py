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
# Default CIDRs that are considered reverse-proxy hops when no
# CONTROLLER_TRUSTED_PROXY_CIDRS env var is set. Covers the standard
# RFC1918 ranges + loopback, which is where every Envoy/ingress pod we
# ship actually lives. Operators running the controller behind a proxy
# on a public CIDR must set the env var explicitly.
_DEFAULT_TRUSTED_PROXY_CIDRS = (
    "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8"
)
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
    by setting the header themselves.

    Also resolves the *real* client IP through a trusted reverse
    proxy. When the direct-connect source (``client_address[0]``) is
    inside a trusted-proxy CIDR, we walk ``X-Forwarded-For`` from the
    rightmost hop toward the client and stop at the first address
    that is NOT itself in a trusted CIDR — that's the real client.
    When the direct-connect source is OUTSIDE the trusted CIDR list,
    the header is untrusted (anyone on the open internet can set it),
    so we fall back to the direct-connect source.

    Returning the proxy IP here broke IP bans: every abuse would
    look like it came from the Envoy-pod IP and one scanner could
    lock out the whole dashboard. The trusted-proxy-aware resolution
    is what makes per-client bans actually target the attacker.
    """

    def __init__(self) -> None:
        self._env = os.environ

    def identity(self, handler) -> str | None:
        cidrs_raw = self._env.get("CONTROLLER_TRUSTED_PROXY_CIDRS", "").strip()
        if not cidrs_raw:
            return None
        header = (self._env.get("CONTROLLER_TRUSTED_PROXY_HEADER", "")
                  .strip() or _DEFAULT_TRUSTED_PROXY_HEADER)
        direct_ip = self._direct_connect_ip(handler)
        headers = getattr(handler, "headers", None)
        header_value = ""
        if headers is not None:
            try:
                header_value = (headers.get(header, "") or "").strip()
            except AttributeError:
                header_value = ""
        trusted = bool(direct_ip) and self._in_any_cidr(direct_ip, cidrs_raw)
        if not trusted:
            if header_value:
                security_counters.incr("trusted_proxy_spoof")
            return None
        return header_value or None

    def client_ip(self, handler) -> str:
        """Return the real client IP, honoring X-Forwarded-For only
        when the direct-connect source is a trusted proxy.

        Contract:
          1. Read ``X-Forwarded-For`` from the handler (if present).
          2. If the direct-connect source is inside the configured
             trusted-proxy CIDR list, the first un-trusted address in
             the XFF chain (walking left from the closest proxy hop)
             is the authoritative client.
          3. Otherwise the direct-connect source is authoritative — an
             XFF header from a non-proxy origin is attacker-controlled
             and must be ignored.
          4. Strict fallback: if we take the trusted-proxy branch and
             cannot find a valid un-trusted address, return '' rather
             than the last proxy hop. Banning a proxy hop locks out
             every client behind it; banning '' is a no-op.
        """
        direct_ip = self._direct_connect_ip(handler)
        cidrs_raw = self._trusted_cidrs()
        if not direct_ip:
            return ""
        if not self._in_any_cidr(direct_ip, cidrs_raw):
            # Direct-connect is the authoritative source — ignore XFF.
            return direct_ip
        xff = self._xff_header(handler)
        if not xff:
            # Trusted proxy but no XFF: we know the proxy IP is not the
            # real client and we have no other signal. Strict fallback
            # refuses to return the proxy IP.
            return ""
        # Walk the XFF list from right (closest hop) to left (client).
        # The first entry that is NOT itself a trusted proxy is the
        # real client; everything to the right is a hop.
        for raw in reversed([c.strip() for c in xff.split(",")]):
            if not raw:
                continue
            if not self._is_ip(raw):
                # Malformed token in the chain — don't silently pick a
                # different hop, fail closed.
                return ""
            if self._in_any_cidr(raw, cidrs_raw):
                continue
            return raw
        # Every entry was a trusted proxy: we don't have a real client
        # IP we can trust. Strict fallback.
        return ""

    # Back-compat: the original private helper. Existing callers that
    # use ``handler.client_address`` semantics (health probes, SSE
    # bookkeeping) get the direct-connect source; migrated call sites
    # move to the public ``client_ip`` above.
    def _client_ip(self, handler) -> str:
        return self.client_ip(handler)

    def _direct_connect_ip(self, handler) -> str:
        addr = getattr(handler, "client_address", None)
        if isinstance(addr, tuple) and addr:
            return str(addr[0])
        return ""

    def _trusted_cidrs(self) -> str:
        raw = self._env.get("CONTROLLER_TRUSTED_PROXY_CIDRS", "").strip()
        return raw if raw else _DEFAULT_TRUSTED_PROXY_CIDRS

    def _xff_header(self, handler) -> str:
        headers = getattr(handler, "headers", None)
        if headers is None:
            return ""
        try:
            value = headers.get("X-Forwarded-For", "") or ""
        except AttributeError:
            return ""
        return value.strip()

    def _is_ip(self, ip_str: str) -> bool:
        import ipaddress
        try:
            ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return True

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
