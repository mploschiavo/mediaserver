"""Ratchet: audit-logging sites must use the trusted-proxy IP.

The old code read ``handler.client_address[0]`` directly in audit
paths, which put the Envoy-pod IP into every audit row. That broke
IP bans and made abuse untraceable. This ratchet AST-scans
``handlers_post.py`` and ``server.py`` for:

  * Calls to ``audit.append(...)`` / ``_audit.append(...)`` whose
    ``ip=`` keyword argument or ``detail`` map binds a client IP.
  * Any reference to ``handler.client_address`` in those modules.

Every such site must route through ``trusted_proxy_auth.client_ip``
(or a method whose body clearly does). A small allowlist covers
legitimate direct-source uses — local health probes and SSE/keepalive
bookkeeping where the direct-connect source is exactly what we want.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_HANDLERS_POST = ROOT / "src" / "media_stack" / "api" / "handlers_post.py"
_SERVER = ROOT / "src" / "media_stack" / "api" / "server.py"

# Explicit allowlist of ``client_address`` references in the scanned
# modules. Each entry is (file-basename, line-substring) — if the line
# content drifts the ratchet flags it and a human has to re-triage.
_CLIENT_ADDRESS_ALLOWLIST: tuple[tuple[str, str], ...] = ()
# All trusted-proxy-resolved IPs now flow through
# ``_trusted_proxy_auth.client_ip`` / the helper methods that delegate
# to it. Direct ``client_address`` reads belong only in
# ``session_singletons._direct_connect_ip`` (not scanned here).


# Audit sites keyed by identifier chain — anything calling
# ``svc._audit.append`` or ``audit.append`` in the scanned files.
_AUDIT_CALL_NAMES = frozenset({"append"})
_AUDIT_RECEIVER_TAILS = frozenset({"_audit", "audit"})


def _scan_audit_calls(tree: ast.AST) -> list[ast.Call]:
    """Yield every Call node that looks like ``<thing>._audit.append``."""
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        if fn.attr not in _AUDIT_CALL_NAMES:
            continue
        recv = fn.value
        if not isinstance(recv, ast.Attribute):
            continue
        if recv.attr not in _AUDIT_RECEIVER_TAILS:
            continue
        out.append(node)
    return out


def _call_mentions_trusted_proxy_ip(call: ast.Call) -> bool:
    """True if any keyword / sub-call in this audit site references
    ``trusted_proxy_auth.client_ip`` or a ``self._client_ip`` / a
    ``_client_ip`` helper (both of which now delegate)."""
    for child in ast.walk(call):
        if isinstance(child, ast.Attribute):
            if child.attr in ("client_ip", "_client_ip"):
                return True
    return False


def _audit_site_takes_no_ip(call: ast.Call) -> bool:
    """Audit calls that don't receive an IP at all (actor-only rows,
    e.g. role_update) are fine — the ratchet only fires when an IP is
    being recorded."""
    ip_keywords = {"ip", "client", "client_ip", "source_ip"}
    for kw in call.keywords:
        if kw.arg in ip_keywords:
            return False
        if kw.arg == "detail" and isinstance(kw.value, ast.Dict):
            keys = [
                getattr(k, "value", None) for k in kw.value.keys
            ]
            if any(k in ip_keywords for k in keys):
                return False
    return True


class AuditSiteRatchet(unittest.TestCase):
    """Every audit-append that binds an IP MUST route through the
    trusted-proxy resolver."""

    def _scan(self, path: Path) -> tuple[list[ast.Call], str]:
        src = path.read_text(encoding="utf-8")
        return _scan_audit_calls(ast.parse(src)), src

    def test_handlers_post_audit_sites_use_trusted_proxy(self):
        calls, src = self._scan(_HANDLERS_POST)
        self.assertTrue(calls, "expected at least one audit.append site "
                               "in handlers_post.py — refactor likely "
                               "missed a call.")
        for call in calls:
            if _audit_site_takes_no_ip(call):
                continue
            ok = _call_mentions_trusted_proxy_ip(call)
            # Fallback: the call references a method (e.g. self._client_ip)
            # whose implementation is known to delegate.
            if not ok:
                line = src.splitlines()[call.lineno - 1]
                self.fail(
                    f"handlers_post.py:{call.lineno}: audit.append() "
                    f"records an IP but does not route through "
                    f"trusted_proxy_auth.client_ip / _client_ip helper. "
                    f"Offending line: {line.strip()}"
                )

    def test_server_audit_sites_use_trusted_proxy(self):
        calls, src = self._scan(_SERVER)
        for call in calls:
            if _audit_site_takes_no_ip(call):
                continue
            ok = _call_mentions_trusted_proxy_ip(call)
            if not ok:
                line = src.splitlines()[call.lineno - 1]
                self.fail(
                    f"server.py:{call.lineno}: audit.append() records "
                    f"an IP without routing through trusted_proxy_auth."
                    f"client_ip. Offending line: {line.strip()}"
                )


class ClientAddressDirectReferenceRatchet(unittest.TestCase):
    """Only ``session_singletons`` should reach into
    ``handler.client_address`` directly. handlers_post.py / server.py
    must go through the resolver.
    """

    def _direct_refs(self, path: Path) -> list[int]:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        hits: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr != "client_address":
                continue
            hits.append(node.lineno)
        return hits

    def test_handlers_post_has_no_direct_client_address(self):
        hits = self._direct_refs(_HANDLERS_POST)
        allowed_lines = {
            ln for (f, sub) in _CLIENT_ADDRESS_ALLOWLIST
            if f == _HANDLERS_POST.name
            for ln in _find_lines(_HANDLERS_POST, sub)
        }
        unexpected = sorted(set(hits) - allowed_lines)
        self.assertFalse(
            unexpected,
            f"handlers_post.py still reads handler.client_address at "
            f"lines {unexpected} — migrate to trusted_proxy_auth."
            f"client_ip or add an explicit allowlist entry.",
        )

    def test_server_has_no_direct_client_address(self):
        hits = self._direct_refs(_SERVER)
        allowed_lines = {
            ln for (f, sub) in _CLIENT_ADDRESS_ALLOWLIST
            if f == _SERVER.name
            for ln in _find_lines(_SERVER, sub)
        }
        unexpected = sorted(set(hits) - allowed_lines)
        self.assertFalse(
            unexpected,
            f"server.py still reads handler.client_address at lines "
            f"{unexpected}.",
        )

    def test_trusted_proxy_public_api_exposed(self):
        """``client_ip`` must be a public method of TrustedProxyAuth so
        callers can stop reaching into the ``_client_ip`` private name.
        """
        from media_stack.api.session_singletons import TrustedProxyAuth
        self.assertTrue(callable(getattr(TrustedProxyAuth, "client_ip", None)))


def _find_lines(path: Path, needle: str) -> list[int]:
    lines: list[int] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            lines.append(i)
    return lines


if __name__ == "__main__":
    unittest.main()
