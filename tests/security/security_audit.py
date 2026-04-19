"""SecurityAuditRunner — reusable black-box security audit.

Point it at a live HTTP service, give it an admin credential, and it
runs the full baseline against the target. Used by
test_controller_security_baseline.py and the per-app suites.

The runner deliberately does NOT import any media-stack internal code —
it's a pure HTTP client so it also works against third-party apps
(Jellyfin, Sonarr, etc.) whose auth model differs from ours.

Each check returns an AuditResult so callers can aggregate a pass/fail
matrix. Checks that don't apply to a given service (e.g. CSRF on a
cookie-less API) are auto-skipped based on ``AuditTarget`` config.
"""

from __future__ import annotations

import base64
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


_BODY_PREVIEW_LEN = 120


@dataclass
class AuditResult:
    check: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status,
            "detail": self.detail,
            "target": self.target,
        }


@dataclass
class AuditTarget:
    """Describes the service under test."""
    base_url: str
    admin_user: str = ""
    admin_pass: str = ""
    bearer_token: str = ""
    read_bearer_token: str = ""
    revoked_bearer_token: str = ""
    public_paths: list[str] = field(default_factory=list)
    sensitive_paths: list[str] = field(default_factory=list)
    mutating_paths: list[str] = field(default_factory=list)
    webhook_post_paths: list[str] = field(default_factory=list)
    # Header the service uses for trusted-proxy forward-auth (if any).
    # When empty, the trusted-proxy spoof check is skipped.
    trusted_proxy_header: str = ""
    verify_tls: bool = True


@dataclass
class _HttpResponse:
    status: int
    body: str
    headers: dict[str, str]


class SecurityAuditRunner:
    """Black-box security audit against a running HTTP service."""

    _EXPECTED_HEADERS = (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Content-Security-Policy",
        "Strict-Transport-Security",
    )
    _DEFAULT_TIMEOUT = 5.0
    _RATE_LIMIT_BURST = 60
    _BODY_SIZE_PROBE_MIB = 2
    _SECRET_PATTERNS = (
        "password", "secret", "api_key", "bearer ", "-----BEGIN",
    )
    # Endpoints that historically leaked admin creds; the audit probes
    # these authenticated and asserts no password pattern appears in
    # the response body. Callers add to target.sensitive_paths for the
    # generic check; this list is for extra-strict scrutiny.
    _CREDENTIAL_ENDPOINTS = ("/api/keys",)

    def __init__(self, target: AuditTarget,
                 timeout: float = _DEFAULT_TIMEOUT) -> None:
        self.target = target
        self.timeout = timeout
        self._results: list[AuditResult] = []
        ctx = ssl.create_default_context()
        if not target.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._ssl_ctx = ctx

    # ---- public entry points ------------------------------------------------

    def run_all(self) -> list[AuditResult]:
        """Execute every baseline check. Returns the full result list."""
        self._results = []
        self._check_public_endpoints_allow_unauth()
        self._check_sensitive_paths_require_auth()
        self._check_authenticated_access_succeeds()
        self._check_security_headers()
        self._check_hsts_value()
        self._check_csp_defines_default_src()
        self._check_basic_auth_wrong_creds_rejected()
        self._check_bearer_token_admin_works()
        self._check_bearer_token_read_blocks_mutation()
        self._check_revoked_bearer_rejected()
        self._check_csrf_blocks_cookie_request_without_token()
        self._check_rate_limit_triggers()
        self._check_body_size_cap()
        self._check_webhook_ssrf_block()
        self._check_no_secret_in_error_bodies()
        self._check_credential_endpoints_no_password_echo()
        self._check_trusted_proxy_spoof_rejected()
        self._check_trailing_slash_canonicalization()
        return list(self._results)

    def pass_fail_summary(self) -> dict[str, int]:
        summary = {"pass": 0, "fail": 0, "skip": 0}
        for r in self._results:
            summary[r.status] = summary.get(r.status, 0) + 1
        return summary

    def render_table(self) -> str:
        """Human-readable result table."""
        rows = [f"{'CHECK':<40} {'STATUS':<6} DETAIL"]
        for r in self._results:
            rows.append(f"{r.check:<40} {r.status:<6} {r.detail}")
        return "\n".join(rows)

    # ---- individual checks --------------------------------------------------

    def _check_public_endpoints_allow_unauth(self) -> None:
        if not self.target.public_paths:
            self._skip("public_endpoints_allow_unauth", "no public_paths")
            return
        bad: list[str] = []
        for path in self.target.public_paths:
            resp = self._request("GET", path)
            if resp.status != 200:
                bad.append(f"{path}={resp.status}")
        self._record(
            "public_endpoints_allow_unauth",
            "pass" if not bad else "fail",
            "; ".join(bad) or "all public paths returned 200",
        )

    def _check_sensitive_paths_require_auth(self) -> None:
        if not self.target.sensitive_paths:
            self._skip("sensitive_paths_require_auth", "no sensitive_paths")
            return
        leaks: list[str] = []
        for path in self.target.sensitive_paths:
            resp = self._request("GET", path)
            if resp.status != 401:
                leaks.append(f"{path}={resp.status}")
        self._record(
            "sensitive_paths_require_auth",
            "pass" if not leaks else "fail",
            "; ".join(leaks) or "all sensitive paths returned 401",
        )

    def _check_authenticated_access_succeeds(self) -> None:
        if not self._has_admin_creds():
            self._skip("authenticated_access_succeeds", "no admin creds")
            return
        failures: list[str] = []
        paths = self.target.sensitive_paths or self.target.public_paths
        for path in paths[:3]:
            resp = self._request("GET", path,
                                 auth=self._basic_auth_header())
            if resp.status != 200:
                failures.append(f"{path}={resp.status}")
        self._record(
            "authenticated_access_succeeds",
            "pass" if not failures else "fail",
            "; ".join(failures) or "authenticated reads returned 200",
        )

    def _check_security_headers(self) -> None:
        resp = self._probe_any_path()
        if resp is None:
            self._skip("security_headers", "no paths to probe")
            return
        missing = [
            h for h in self._EXPECTED_HEADERS
            if not self._header_present(resp.headers, h)
        ]
        self._record(
            "security_headers",
            "pass" if not missing else "fail",
            "missing: " + ", ".join(missing) if missing else
            f"all {len(self._EXPECTED_HEADERS)} headers present",
        )

    def _check_hsts_value(self) -> None:
        resp = self._probe_any_path()
        if resp is None:
            self._skip("hsts_value", "no paths to probe")
            return
        hsts = self._header_value(resp.headers, "Strict-Transport-Security")
        ok = bool(hsts) and "max-age=" in hsts and "includeSubDomains" in hsts
        self._record(
            "hsts_value",
            "pass" if ok else "fail",
            hsts or "(missing)",
        )

    def _check_csp_defines_default_src(self) -> None:
        resp = self._probe_any_path()
        if resp is None:
            self._skip("csp_default_src", "no paths to probe")
            return
        csp = self._header_value(resp.headers, "Content-Security-Policy")
        ok = bool(csp) and "default-src" in csp and "frame-ancestors" in csp
        self._record(
            "csp_default_src",
            "pass" if ok else "fail",
            (csp or "(missing)")[:_BODY_PREVIEW_LEN],
        )

    def _check_basic_auth_wrong_creds_rejected(self) -> None:
        if not self.target.sensitive_paths:
            self._skip("wrong_creds_rejected", "no sensitive path")
            return
        path = self.target.sensitive_paths[0]
        resp = self._request(
            "GET", path,
            auth=self._make_basic("nobody", "definitely-wrong"),
        )
        self._record(
            "wrong_creds_rejected",
            "pass" if resp.status == 401 else "fail",
            f"HTTP {resp.status}",
        )

    def _check_bearer_token_admin_works(self) -> None:
        if not self.target.bearer_token or not self.target.sensitive_paths:
            self._skip("bearer_admin_works", "no bearer token configured")
            return
        path = self.target.sensitive_paths[0]
        resp = self._request("GET", path,
                             auth=f"Bearer {self.target.bearer_token}")
        self._record(
            "bearer_admin_works",
            "pass" if resp.status == 200 else "fail",
            f"HTTP {resp.status}",
        )

    def _check_bearer_token_read_blocks_mutation(self) -> None:
        if (not self.target.read_bearer_token
                or not self.target.mutating_paths):
            self._skip("bearer_read_blocks_mutation",
                       "no read token or mutating paths")
            return
        path = self.target.mutating_paths[0]
        resp = self._request(
            "POST", path, body=b"{}",
            auth=f"Bearer {self.target.read_bearer_token}",
            content_type="application/json",
        )
        self._record(
            "bearer_read_blocks_mutation",
            "pass" if resp.status in (401, 403) else "fail",
            f"HTTP {resp.status}",
        )

    def _check_revoked_bearer_rejected(self) -> None:
        if (not self.target.revoked_bearer_token
                or not self.target.sensitive_paths):
            self._skip("revoked_bearer_rejected", "no revoked token")
            return
        path = self.target.sensitive_paths[0]
        resp = self._request(
            "GET", path,
            auth=f"Bearer {self.target.revoked_bearer_token}",
        )
        self._record(
            "revoked_bearer_rejected",
            "pass" if resp.status == 401 else "fail",
            f"HTTP {resp.status}",
        )

    def _check_csrf_blocks_cookie_request_without_token(self) -> None:
        if not self.target.mutating_paths or not self._has_admin_creds():
            self._skip("csrf_blocks_cookie_no_token",
                       "no mutating paths or creds")
            return
        path = self.target.mutating_paths[0]
        resp = self._request(
            "POST", path, body=b"{}",
            auth=self._basic_auth_header(),
            content_type="application/json",
            extra_headers={"Cookie": "ms_session=probe"},
        )
        self._record(
            "csrf_blocks_cookie_no_token",
            "pass" if resp.status in (401, 403) else "fail",
            f"HTTP {resp.status}",
        )

    def _check_rate_limit_triggers(self) -> None:
        if not self.target.mutating_paths or not self._has_admin_creds():
            self._skip("rate_limit_triggers", "no mutating paths or creds")
            return
        path = self.target.mutating_paths[0]
        codes: list[int] = []
        for _ in range(self._RATE_LIMIT_BURST):
            resp = self._request(
                "POST", path, body=b"{}",
                auth=self._basic_auth_header(),
                content_type="application/json",
            )
            codes.append(resp.status)
            if resp.status == 429:
                break
        self._record(
            "rate_limit_triggers",
            "pass" if 429 in codes else "fail",
            f"observed codes={sorted(set(codes))}",
        )

    def _check_body_size_cap(self) -> None:
        """Large body should be rejected (400/413) or error cleanly."""
        if not self.target.mutating_paths or not self._has_admin_creds():
            self._skip("body_size_cap", "no mutating path")
            return
        path = self.target.mutating_paths[0]
        big = (b"A" * (self._BODY_SIZE_PROBE_MIB * 1024 * 1024))
        try:
            resp = self._request(
                "POST", path, body=big,
                auth=self._basic_auth_header(),
                content_type="application/octet-stream",
            )
        except Exception as exc:  # noqa: BLE001
            self._record("body_size_cap", "pass",
                         f"oversized body errored: {type(exc).__name__}")
            return
        ok = resp.status in (400, 413)
        self._record(
            "body_size_cap",
            "pass" if ok else "fail",
            f"HTTP {resp.status} — expected 400 or 413",
        )

    def _check_webhook_ssrf_block(self) -> None:
        if (not self.target.webhook_post_paths
                or not self._has_admin_creds()):
            self._skip("webhook_ssrf_block", "no webhook path")
            return
        path = self.target.webhook_post_paths[0]
        resp = self._request(
            "POST", path,
            body=json.dumps({"url": "http://127.0.0.1:9100/cancel"}).encode(),
            auth=self._basic_auth_header(),
            content_type="application/json",
        )
        ok = resp.status == 400 and "blocked" in resp.body.lower()
        self._record(
            "webhook_ssrf_block",
            "pass" if ok else "fail",
            f"HTTP {resp.status} body={resp.body[:_BODY_PREVIEW_LEN]!r}",
        )

    def _check_no_secret_in_error_bodies(self) -> None:
        if not self.target.sensitive_paths:
            self._skip("no_secret_in_errors", "no sensitive path")
            return
        resp = self._request("GET", self.target.sensitive_paths[0])
        body_l = resp.body.lower()
        hits = [p for p in self._SECRET_PATTERNS if p in body_l]
        self._record(
            "no_secret_in_errors",
            "pass" if not hits else "fail",
            "leaked: " + ", ".join(hits) if hits else "clean",
        )

    def _check_credential_endpoints_no_password_echo(self) -> None:
        """Probe well-known creds endpoints with valid auth and assert
        no password-ish value is echoed. Historically /api/keys
        returned the plaintext admin password; we guard against the
        regression here.
        """
        if not self._has_admin_creds():
            self._skip("credential_endpoints_no_echo", "no admin creds")
            return
        pw = self.target.admin_pass
        leaks: list[str] = []
        for path in self._CREDENTIAL_ENDPOINTS:
            resp = self._request("GET", path, auth=self._basic_auth_header())
            if resp.status != 200:
                continue  # endpoint doesn't exist on this target
            body_l = resp.body.lower()
            if pw and pw.lower() in body_l:
                leaks.append(f"{path}: plaintext admin password found")
            if '"password":' in body_l:
                leaks.append(f"{path}: field 'password' present in body")
        self._record(
            "credential_endpoints_no_echo",
            "pass" if not leaks else "fail",
            "; ".join(leaks) or "no credential echo detected",
        )

    def _check_trusted_proxy_spoof_rejected(self) -> None:
        """Spoofing the trusted-proxy identity header from an un-trusted
        source MUST NOT authenticate the request.

        The audit is run from outside the trusted-proxy CIDR (test harness
        talks to localhost / the service directly), so setting the header
        should be silently ignored. If the spoof works, the service is
        honoring the header from any IP — a severe trust boundary bug.
        """
        header = self.target.trusted_proxy_header
        if not header or not self.target.sensitive_paths:
            self._skip("trusted_proxy_spoof_rejected",
                       "no trusted_proxy_header configured")
            return
        path = self.target.sensitive_paths[0]
        resp = self._request(
            "GET", path, extra_headers={header: "admin"},
        )
        self._record(
            "trusted_proxy_spoof_rejected",
            "pass" if resp.status == 401 else "fail",
            f"HTTP {resp.status}",
        )

    def _check_trailing_slash_canonicalization(self) -> None:
        if not self.target.sensitive_paths:
            self._skip("trailing_slash_canonical", "no path")
            return
        path = self.target.sensitive_paths[0]
        a = self._request("GET", path, auth=self._basic_auth_header())
        b = self._request("GET", path.rstrip("/") + "/",
                          auth=self._basic_auth_header())
        ok = a.status == b.status or (a.status == 200
                                      and b.status in (200, 301, 308))
        self._record(
            "trailing_slash_canonical",
            "pass" if ok else "fail",
            f"{path}={a.status}, {path.rstrip('/')+'/'}={b.status}",
        )

    # ---- HTTP helpers -------------------------------------------------------

    def _probe_any_path(self) -> _HttpResponse | None:
        paths = self.target.public_paths + self.target.sensitive_paths
        if not paths:
            return None
        return self._request("GET", paths[0], auth=self._basic_auth_header())

    def _basic_auth_header(self) -> str:
        if not self._has_admin_creds():
            return ""
        return self._make_basic(self.target.admin_user, self.target.admin_pass)

    @staticmethod
    def _make_basic(user: str, pw: str) -> str:
        raw = f"{user}:{pw}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def _has_admin_creds(self) -> bool:
        return bool(self.target.admin_user and self.target.admin_pass)

    def _request(self, method: str, path: str, *, body: bytes | None = None,
                 auth: str = "", content_type: str = "",
                 extra_headers: dict | None = None) -> _HttpResponse:
        url = self.target.base_url.rstrip("/") + path
        headers: dict[str, str] = dict(extra_headers or {})
        if auth:
            headers["Authorization"] = auth
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, method=method, headers=headers,
                                     data=body)
        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self._ssl_ctx,
            ) as resp:
                return _HttpResponse(
                    status=resp.status,
                    body=resp.read().decode("utf-8", errors="replace"),
                    headers=dict(resp.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            return _HttpResponse(
                status=exc.code,
                body=body,
                headers=dict(exc.headers.items()) if exc.headers else {},
            )
        except urllib.error.URLError as exc:
            return _HttpResponse(status=0, body=f"URLError: {exc}",
                                 headers={})

    # ---- header inspection --------------------------------------------------

    @staticmethod
    def _header_present(headers: dict, name: str) -> bool:
        name_l = name.lower()
        return any(k.lower() == name_l for k in headers)

    @staticmethod
    def _header_value(headers: dict, name: str) -> str:
        name_l = name.lower()
        for k, v in headers.items():
            if k.lower() == name_l:
                return v
        return ""

    def _record(self, check: str, status: str, detail: str = "") -> None:
        self._results.append(AuditResult(
            check=check, status=status, detail=detail,
            target=self.target.base_url,
        ))

    def _skip(self, check: str, reason: str) -> None:
        self._record(check, "skip", reason)
