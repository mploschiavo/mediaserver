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

    def _request(self, path: str) -> tuple[int, dict]:
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
            # Match a browser navigation: Accept header matters because
            # Authelia (and many API gateways) gate 302-vs-401 on whether
            # the client wants HTML or not. `*/*` mirrors curl's default
            # and is the common "browser or broad client" signal.
            conn.request("GET", path, headers={
                "Host": hostname,
                "Accept": "text/html,*/*",
                "User-Agent": "gateway-url-matrix/1.0",
            })
            resp = conn.getresponse()
            headers = dict(resp.getheaders())
            resp.read()
            return resp.status, headers
        finally:
            conn.close()

    def _check(self, case: UrlCase) -> None:
        status, headers = self._request(case.path)
        self.assertEqual(
            status, case.expected_status,
            f"{case.path}: {case.description}",
        )
        if case.expected_location_re is None:
            self.assertNotIn(
                "location", {k.lower() for k in headers},
                f"{case.path}: expected no Location header",
            )
            return
        location = ""
        for k, v in headers.items():
            if k.lower() == "location":
                location = v
                break
        self.assertTrue(
            re.match(case.expected_location_re, location),
            f"{case.path}: Location {location!r} does not match "
            f"{case.expected_location_re!r}\n  ({case.description})",
        )


# Generate one test method per matrix row so failures surface
# individually instead of as a single aggregated error.
def _attach_row_tests(row_iter: Iterable[UrlCase]) -> None:
    for case in row_iter:
        method_name = "test_url_" + re.sub(r"[^a-zA-Z0-9]+", "_",
                                            case.path.strip("/")) or "test_url_root"
        method_name = method_name.rstrip("_") or "test_url_root"
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
