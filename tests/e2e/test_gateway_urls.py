"""End-to-end URL matrix for the Envoy gateway.

Purpose
-------
Catches the class of regression that just happened twice: a config
change produces an Envoy that LOOKS right in unit tests but actually
serves broken URLs. Unit tests assert structure; this suite asserts
BEHAVIOUR against a running stack.

Every expected URL behavior gets ONE row in URL_MATRIX. Each row is:
  (request_path, expected_status, expected_location_regex, description)

If a future commit rearranges routing or auth wiring, this entire
matrix runs and any drift — wrong status, wrong host, wrong path
component in the Location header — fails loudly.

Runs against whichever stack is reachable:
  GATEWAY_URL env var       (default https://apps.media-stack.local)
  GATEWAY_RESOLVE env var   (default apps.media-stack.local:443:127.0.0.1)
  AUTH_URL env var          (default https://auth.media-stack.local — used
                              to validate login redirects point somewhere
                              real, not a typo'd hostname)

Skips automatically when the gateway isn't reachable, so the same
file works in CI (no live stack → skip) and locally (running stack
→ full assertions).

Both the compose and K8s deployments serve the same Envoy config,
so this matrix is the SINGLE source of truth for "what URL should
do what" across both paths.
"""

from __future__ import annotations

import os
import re
import ssl
import unittest
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class UrlCase:
    path: str
    expected_status: int
    # Regex the Location header must match. None means "no Location
    # expected" (e.g. 200 responses).
    expected_location_re: str | None
    description: str
    method: str = "GET"
    # When set, the response body must parse as JSON (for /api/* POSTs
    # that must return JSON even when auth fails — text/plain "405"
    # leaked through once and broke the dashboard).
    require_json_body: bool = False


# -----------------------------------------------------------------------------
# THE MATRIX.
# Add a row here every time you wire a new public URL. If a commit
# rearranges routing, these assertions will fail loudly.
# -----------------------------------------------------------------------------
URL_MATRIX: tuple[UrlCase, ...] = (
    UrlCase(
        path="/",
        expected_status=302,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "Root redirects to Authelia login. The portal URL must be "
            "just https://auth.media-stack.local/ — any extra path "
            "segment (e.g. /app) means the Envoy rd-append bug is back."
        ),
    ),
    UrlCase(
        path="/app",
        expected_status=302,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "/app redirects cleanly to the Authelia portal root. "
            "Regression guard for the /api/verify?rd= path-append bug."
        ),
    ),
    UrlCase(
        path="/app/jellyfin",
        expected_status=302,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "Any /app/<service> redirects to the same Authelia portal "
            "root. The prefix stays in the rd= query, not in the path."
        ),
    ),
    UrlCase(
        path="/app/homepage",
        expected_status=302,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "Homepage service: same portal-root redirect as other "
            "Authelia-gated services."
        ),
    ),
    UrlCase(
        path="/nonexistent",
        expected_status=302,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "Even unknown paths get the ext_authz redirect to Authelia. "
            "Envoy runs auth BEFORE route matching, so a 404 would only "
            "be visible post-auth — documented explicitly here because "
            "this surprised the team once already."
        ),
    ),
    # -------------------------------------------------------------------
    # POST-method regression guards.
    # On 2026-04-19 a dashboard password reset hit Envoy's ext_authz,
    # which forwarded the POST to Authelia's /api/authz/forward-auth.
    # That endpoint rejects non-safe methods with 405 text/plain, which
    # Envoy propagated to the browser. The dashboard's fetch().json()
    # choked on "405 Method Not Allowed" at character 4 and the user
    # could not reset a password. The matrix below catches every
    # mutating HTTP method on the user-mgmt paths.
    # -------------------------------------------------------------------
    UrlCase(
        path="/api/users/deadbeef-dead-dead-dead-deadbeefdead/reset-password",
        method="POST",
        expected_status=303,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "Unauthenticated POST must redirect to Authelia — not 405. "
            "Regression guard for /api/authz/forward-auth (GET-only) "
            "accidentally being put back on the ext_authz path."
        ),
    ),
    UrlCase(
        path="/api/users/deadbeef-dead-dead-dead-deadbeefdead/delete",
        method="POST",
        expected_status=303,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "Delete-user POST must redirect cleanly through Authelia. "
            "Same 405-leak class as reset-password."
        ),
    ),
    UrlCase(
        path="/api/tokens",
        method="POST",
        expected_status=303,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "Token mint is a POST under /api/ — must also redirect to "
            "Authelia rather than 405 out through ext_authz."
        ),
    ),
    UrlCase(
        path="/api/rotate-keys",
        method="POST",
        expected_status=303,
        expected_location_re=(
            r"^https://auth\.media-stack\.local/\?rd="
        ),
        description=(
            "POST /api/rotate-keys (sensitive admin op) must flow "
            "through ext_authz redirect, not 405."
        ),
    ),
)


def _gateway_base() -> str:
    return os.environ.get("GATEWAY_URL", "https://apps.media-stack.local")


def _gateway_resolve() -> tuple[str, int, str] | None:
    raw = os.environ.get("GATEWAY_RESOLVE", "apps.media-stack.local:443:127.0.0.1")
    parts = raw.split(":")
    if len(parts) != 3:
        return None
    try:
        return parts[0], int(parts[1]), parts[2]
    except ValueError:
        return None


def _reachable(base_url: str, resolve: tuple[str, int, str] | None) -> bool:
    """True when the gateway answers any request. Uses a low-level
    socket probe so we don't care about 4xx/5xx — we only care that
    something is listening."""
    import socket
    host, port = (resolve[2], resolve[1]) if resolve else (
        base_url.split("://", 1)[1].split("/", 1)[0], 443,
    )
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


class GatewayUrlMatrixTests(unittest.TestCase):
    """Live URL-matrix check against the Envoy gateway.

    Runs once per row in URL_MATRIX. If the live gateway isn't
    reachable, every test is skipped so this file stays green in CI
    that doesn't spin up the stack.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = _gateway_base()
        cls.resolve = _gateway_resolve()
        if not _reachable(cls.base, cls.resolve):
            raise unittest.SkipTest(
                f"Gateway not reachable at {cls.base} "
                f"(resolve={cls.resolve}). Start the stack or set "
                "GATEWAY_URL/GATEWAY_RESOLVE.",
            )

    def _request(self, path: str, method: str = "GET") -> tuple[int, dict, bytes]:
        """Low-level HTTPS request that honors GATEWAY_RESOLVE without
        relying on urllib (which mangles the Host header when you
        mix resolve-overrides with Request.add_header)."""
        import http.client
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        hostname = self.base.split("://", 1)[1].split("/", 1)[0]
        if self.resolve is not None:
            _host, port, ip = self.resolve
            conn = http.client.HTTPSConnection(ip, port, context=ctx, timeout=5)
        else:
            host_port = hostname.split(":", 1)
            host = host_port[0]
            port = int(host_port[1]) if len(host_port) > 1 else 443
            conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=5)
        try:
            body = b"{}" if method in ("POST", "PUT", "PATCH") else None
            headers = {
                "Host": hostname,
                "Accept": "text/html,*/*",
                "User-Agent": "gateway-url-matrix/1.0",
            }
            if body is not None:
                headers["Content-Type"] = "application/json"
                headers["Content-Length"] = str(len(body))
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            hdrs = dict(resp.getheaders())
            raw = resp.read()
            return resp.status, hdrs, raw
        finally:
            conn.close()

    def _check(self, case: UrlCase) -> None:
        status, headers, body = self._request(case.path, method=case.method)
        self.assertEqual(
            status, case.expected_status,
            f"{case.method} {case.path}: got HTTP {status}. {case.description}\n"
            f"  body={body[:120]!r}",
        )
        if case.require_json_body:
            import json as _json
            try:
                _json.loads(body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError) as exc:
                self.fail(
                    f"{case.method} {case.path}: body is not JSON "
                    f"({exc}). body={body[:120]!r}. {case.description}",
                )
        if case.expected_location_re is None:
            self.assertNotIn(
                "location", {k.lower() for k in headers},
                f"{case.method} {case.path}: expected no Location header",
            )
            return
        location = ""
        for k, v in headers.items():
            if k.lower() == "location":
                location = v
                break
        self.assertTrue(
            re.match(case.expected_location_re, location),
            f"{case.method} {case.path}: Location {location!r} does not match "
            f"{case.expected_location_re!r}\n  ({case.description})",
        )


# Generate one test method per matrix row so failures surface
# individually instead of as a single aggregated error.
def _attach_row_tests(row_iter: Iterable[UrlCase]) -> None:
    for case in row_iter:
        verb = case.method.lower()
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", case.path.strip("/")) or "root"
        method_name = f"test_{verb}_{slug}".rstrip("_")
        # Ensure uniqueness if multiple rows share a normalized name.
        suffix = 1
        base_name = method_name
        while hasattr(GatewayUrlMatrixTests, method_name):
            suffix += 1
            method_name = f"{base_name}_{suffix}"

        def _make(c):
            def test(self):
                self._check(c)
            test.__doc__ = c.description
            return test

        setattr(GatewayUrlMatrixTests, method_name, _make(case))


_attach_row_tests(URL_MATRIX)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Bypass urllib's auto-redirect so we can assert the 302 directly."""

    def http_error_302(self, req, fp, code, msg, headers):  # noqa: D401
        # Raise an HTTPError so the caller handles it uniformly.
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class HostnameResolutionTests(unittest.TestCase):
    """Catches the /etc/hosts drift that breaks browser follow-through
    even when Envoy is serving all the right vhosts internally.

    If Envoy serves `auth.media-stack.local` but the operator's
    /etc/hosts only has `authelia.media-stack.local`, the login
    redirect lands on NXDOMAIN in the browser — despite every
    controller-side check passing. This test reads the live Envoy
    vhost list and asserts each hostname resolves on the host
    running the tests.

    Skips cleanly when the Envoy container isn't reachable.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import subprocess
        try:
            raw = subprocess.check_output(
                ["docker", "exec", "envoy", "grep", "-oE",
                 r"[a-z0-9-]+\.media-stack\.local", "/etc/envoy/envoy.yaml"],
                text=True, stderr=subprocess.DEVNULL, timeout=5,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
                FileNotFoundError):
            raise unittest.SkipTest("envoy container unreachable")
        cls.hostnames = sorted(set(raw.splitlines()))
        if not cls.hostnames:
            raise unittest.SkipTest("no hostnames found in envoy config")

    def test_every_envoy_hostname_resolves(self):
        import socket
        unresolved: list[str] = []
        for host in self.hostnames:
            try:
                socket.gethostbyname(host)
            except socket.gaierror:
                unresolved.append(host)
        self.assertFalse(
            unresolved,
            "Envoy serves these hostnames but they don't resolve on this "
            "machine — browser redirects will fail with NXDOMAIN. Run "
            "`bin/sync-etc-hosts.sh --apply`. Missing: "
            + ", ".join(unresolved),
        )


if __name__ == "__main__":
    unittest.main()
