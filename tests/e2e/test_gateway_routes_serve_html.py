"""End-to-end smoke — every ``/app/<slug>/`` route on the Envoy
gateway must return HTML with a non-trivial body.

2026-04-19 blank-page incident: Envoy forwarded
``/app/prowlarr/...`` to Prowlarr with the prefix intact, but
Prowlarr's ``<UrlBase>`` was empty. HTML loaded (200 OK), every
asset 404'd → blank page. A 200-status check alone would have
missed it — you need to assert the body is real HTML.

This test runs only when the Envoy gateway is reachable at
``https://apps.media-stack.local/`` (skip otherwise). Each
``/app/<slug>/`` route is probed twice:

1. With an authenticated session cookie (so ext_authz passes) —
   expect 200 and ``<html`` in the body.
2. Without the cookie — expect a redirect to the Authelia portal,
   NOT a 500 or 404.

Because we can't easily mint an Authelia session cookie from a
shell test, the authenticated probe is skipped unless the test
harness provides ``MEDIA_STACK_AUTHELIA_COOKIE`` in the env.
The unauthenticated probe still catches most symptoms because a
mis-routed prefix returns 404 from Envoy directly — before
ext_authz ever fires.
"""

from __future__ import annotations

import os
import socket
import ssl
import unittest
from http.client import HTTPSConnection


_GATEWAY_HOST = "apps.media-stack.local"
_GATEWAY_PORT = 443
# Canonical list of path-prefix slugs that SHOULD serve a UI.
# Keep in sync with _CONTRACTS_DIR when a new HTML-facing app
# joins the stack — the companion static audit test
# (test_all_path_prefix_routes_have_url_base_preflight) will
# break first if you forget.
_HTML_SLUGS = (
    "sonarr", "radarr", "lidarr", "readarr", "prowlarr",
    "bazarr", "sabnzbd", "jellyseerr", "homepage", "maintainerr",
    "tautulli",
)


def _gateway_reachable() -> bool:
    try:
        socket.gethostbyname(_GATEWAY_HOST)
    except socket.gaierror:
        return False
    try:
        with socket.create_connection(
            ("127.0.0.1", _GATEWAY_PORT), timeout=2,
        ):
            return True
    except OSError:
        return False


def _tls_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get(path: str, cookie: str = "") -> tuple[int, bytes, dict[str, str]]:
    conn = HTTPSConnection(_GATEWAY_HOST, _GATEWAY_PORT,
                           context=_tls_ctx(), timeout=5)
    try:
        headers = {"Host": _GATEWAY_HOST,
                   "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        if cookie:
            headers["Cookie"] = cookie
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        status = resp.status
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        return status, body, hdrs
    finally:
        conn.close()


class GatewayRoutesServeHtmlTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        if not _gateway_reachable():
            raise unittest.SkipTest(
                f"{_GATEWAY_HOST}:{_GATEWAY_PORT} not reachable — "
                f"e2e test only runs against a live stack.",
            )

    def test_unauth_redirects_to_authelia_not_500_or_404(self):
        """Without a session cookie, Envoy+Authelia should redirect
        every protected route to the Authelia portal. A 404 would
        mean the prefix isn't routed; a 500 means the upstream is
        broken. Both are regressions we want to catch before the
        browser does."""
        failures: list[str] = []
        for slug in _HTML_SLUGS:
            path = f"/app/{slug}/"
            status, body, headers = _get(path)
            if status not in (200, 301, 302, 303, 307, 308):
                failures.append(
                    f"{path}: HTTP {status} — expected 200 or a "
                    f"redirect. Body preview: {body[:120]!r}",
                )
                continue
            # 302s should carry a Location to auth.*; not a 500 page
            if 300 <= status < 400:
                location = headers.get("location", "")
                if "auth." not in location and "authelia" not in location.lower():
                    failures.append(
                        f"{path}: redirected to {location!r} — "
                        f"expected something pointing at Authelia.",
                    )
        self.assertFalse(failures, "\n".join(failures))

    def test_authenticated_routes_return_html_with_body(self):
        """The blank-page check: when a user IS authenticated, the
        response must be HTML with a non-trivial body. A UrlBase
        mismatch returns 200 but an empty or near-empty body (or a
        body with asset URLs that will all 404 on load).

        Requires an Authelia session cookie in
        ``MEDIA_STACK_AUTHELIA_COOKIE``. CI wires this from a
        dedicated test account. Local devs can paste their browser
        cookie value to run this locally."""
        cookie_val = os.environ.get("MEDIA_STACK_AUTHELIA_COOKIE", "").strip()
        if not cookie_val:
            self.skipTest(
                "MEDIA_STACK_AUTHELIA_COOKIE not set — set it to "
                "an Authelia session cookie value to enable this "
                "probe (e.g. 'authelia_session=...').",
            )
        failures: list[str] = []
        for slug in _HTML_SLUGS:
            path = f"/app/{slug}/"
            status, body, _hdrs = _get(path, cookie=cookie_val)
            if status != 200:
                failures.append(f"{path}: HTTP {status} "
                                f"body={body[:120]!r}")
                continue
            text = body.decode("utf-8", errors="replace")
            if "<html" not in text.lower():
                failures.append(
                    f"{path}: 200 but body missing <html — "
                    f"probably a UrlBase mismatch. First bytes: "
                    f"{body[:120]!r}",
                )
                continue
            # Asset sanity: HTML should reference at least one
            # script/link/stylesheet. A page that says <html>...
            # </html> with nothing in it is the exact blank-page
            # symptom.
            if len(text) < 500 and "<script" not in text.lower() \
                    and "<link" not in text.lower():
                failures.append(
                    f"{path}: HTML under 500 chars with no "
                    f"<script> or <link> — likely blank-page bug. "
                    f"Full body: {text[:300]!r}",
                )
        self.assertFalse(failures, "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
