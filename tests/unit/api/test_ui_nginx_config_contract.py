"""Contract tests for the UI container's nginx configuration.

Verifies ``docker/ui-nginx.conf`` exposes the directives the runtime
contract depends on: 8080 listener, /healthz, /api/ reverse-proxy with
``${API_UPSTREAM}`` indirection, /assets/ long-cache (Vite hashed
output), security headers (CSP including ``frame-ancestors 'none'``,
X-Frame-Options DENY, nosniff, Referrer-Policy), gzip, SPA fallback to
``index.html``, and X-Real-IP/X-Forwarded-For propagation through the
API proxy.

The fileset agent for nginx may write the file as ``ui-nginx.conf`` or
``ui-nginx.conf.template`` (envsubst-rendered). We accept either; the
test resolves whichever exists and skips with a clear message if both
are missing.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import pytest

ROOT: Path = Path(__file__).resolve().parents[3]
CANDIDATE_PATHS: tuple[Path, ...] = (
    ROOT / "docker" / "ui-nginx.conf",
    ROOT / "docker" / "ui-nginx.conf.template",
)


def _resolve_config_path() -> Path:
    """Pick whichever candidate exists; skip cleanly if neither does."""

    for candidate in CANDIDATE_PATHS:
        if candidate.is_file():
            return candidate
    pytest.skip(
        "file docker/ui-nginx.conf (or .template) not yet created by "
        "parallel agent — re-run after that agent completes"
    )
    raise AssertionError("unreachable: pytest.skip raises")


def _read_config() -> tuple[Path, str]:
    path = _resolve_config_path()
    return path, path.read_text(encoding="utf-8")


def _block(text: str, header_regex: str) -> str | None:
    """Return the brace-balanced body of the first matching block.

    ``header_regex`` matches the directive line up to the opening ``{``,
    e.g. ``r"location\\s*=\\s*/healthz\\s*"``. Returns ``None`` when no
    such block exists.
    """

    match = re.search(header_regex + r"\{", text)
    if not match:
        return None
    start = match.end()  # position right after the opening '{'
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return None


class UiNginxConfigContractTests(unittest.TestCase):
    """Each failure cites the resolved config path and the missing directive."""

    def test_config_exists(self) -> None:
        # Resolves the path or skips; reaching this line means it exists.
        path, text = _read_config()
        self.assertTrue(
            path.is_file() and text.strip(),
            f"Expected non-empty nginx config at {path}.",
        )

    def test_listens_on_8080(self) -> None:
        path, text = _read_config()
        self.assertRegex(
            text,
            r"(?m)^\s*listen\s+8080\s*;",
            f"{path}: missing 'listen 8080;' — UI container binds to "
            "8080 unprivileged.",
        )

    def test_has_healthz_location(self) -> None:
        path, text = _read_config()
        body = _block(text, r"location\s*=\s*/healthz\s*")
        self.assertIsNotNone(
            body,
            f"{path}: missing 'location = /healthz {{ ... }}' block.",
        )
        assert body is not None  # for type checker
        self.assertRegex(
            body,
            r"return\s+200\b",
            f"{path}: '/healthz' block must 'return 200' so liveness/"
            "readiness probes pass.",
        )

    def test_has_api_proxy(self) -> None:
        path, text = _read_config()
        body = _block(text, r"location\s+/api/\s*")
        self.assertIsNotNone(
            body,
            f"{path}: missing 'location /api/ {{ ... }}' block — the UI "
            "container reverse-proxies /api/* to the controller.",
        )
        assert body is not None
        self.assertIn(
            "proxy_pass",
            body,
            f"{path}: '/api/' block must contain a 'proxy_pass' "
            "directive pointing at the controller upstream.",
        )

    def test_api_upstream_uses_env_var(self) -> None:
        path, text = _read_config()
        body = _block(text, r"location\s+/api/\s*")
        self.assertIsNotNone(
            body,
            f"{path}: '/api/' block missing — cannot validate upstream.",
        )
        assert body is not None
        self.assertRegex(
            body,
            r"proxy_pass\s+[^;]*\$\{?API_UPSTREAM\}?",
            f"{path}: '/api/' proxy_pass must reference '${{API_UPSTREAM}}' "
            "so the upstream Service is configurable per-environment "
            "(rendered by the nginx:alpine envsubst entrypoint).",
        )

    def test_has_assets_long_cache(self) -> None:
        path, text = _read_config()
        body = _block(text, r"location\s+/assets/\s*")
        self.assertIsNotNone(
            body,
            f"{path}: missing 'location /assets/ {{ ... }}' block — "
            "Vite emits hashed JS/CSS/font filenames under /assets/ "
            "and they need long-cache rules.",
        )
        assert body is not None
        self.assertRegex(
            body,
            r"(?m)^\s*expires\s+1y\s*;",
            f"{path}: '/assets/' block must declare 'expires 1y;' — "
            "Vite-hashed assets are content-addressed and safe to "
            "cache aggressively.",
        )
        cache_match = re.search(
            r"(?mi)^\s*add_header\s+Cache-Control\s+\"([^\"]+)\"",
            body,
        )
        self.assertIsNotNone(
            cache_match,
            f"{path}: '/assets/' block must set a Cache-Control header.",
        )
        assert cache_match is not None
        cache_value = cache_match.group(1)
        self.assertIn(
            "public",
            cache_value,
            f"{path}: '/assets/' Cache-Control must include 'public' "
            f"(got: {cache_value!r}).",
        )
        self.assertIn(
            "max-age=31536000",
            cache_value,
            f"{path}: '/assets/' Cache-Control must include "
            f"'max-age=31536000' (got: {cache_value!r}).",
        )
        self.assertIn(
            "immutable",
            cache_value,
            f"{path}: '/assets/' Cache-Control must include 'immutable' "
            f"(got: {cache_value!r}).",
        )

    def test_has_spa_fallback(self) -> None:
        path, text = _read_config()
        body = _block(text, r"location\s+/\s*")
        self.assertIsNotNone(
            body,
            f"{path}: missing 'location / {{ ... }}' block — required "
            "for SPA fallback to index.html.",
        )
        assert body is not None
        # try_files $uri $uri/ /index.html — every unknown route falls
        # through to index.html so the client-side router can take over.
        self.assertRegex(
            body,
            r"try_files\s+\$uri\s+\$uri/\s+/index\.html\s*;",
            f"{path}: 'location /' must declare "
            "'try_files $uri $uri/ /index.html;' so SPA routes resolve "
            "to the React shell.",
        )

    def test_has_csp_header(self) -> None:
        path, text = _read_config()
        self.assertRegex(
            text,
            r"(?mi)^\s*add_header\s+Content-Security-Policy\b",
            f"{path}: missing 'add_header Content-Security-Policy ...;' "
            "directive. The UI must ship a CSP equivalent to the policy "
            "the API container used to send.",
        )

    def test_csp_blocks_frame_ancestors(self) -> None:
        path, text = _read_config()
        match = re.search(
            r"(?mi)^\s*add_header\s+Content-Security-Policy\s+\"([^\"]+)\"",
            text,
        )
        self.assertIsNotNone(
            match,
            f"{path}: could not locate a quoted Content-Security-Policy "
            "value to inspect for frame-ancestors.",
        )
        assert match is not None
        csp_value = match.group(1)
        self.assertIn(
            "frame-ancestors 'none'",
            csp_value,
            f"{path}: CSP must include \"frame-ancestors 'none'\" "
            f"(got: {csp_value!r}). Required as a clickjacking guard.",
        )

    def test_csp_allows_jsdelivr_for_fonts(self) -> None:
        path, text = _read_config()
        match = re.search(
            r"(?mi)^\s*add_header\s+Content-Security-Policy\s+\"([^\"]+)\"",
            text,
        )
        self.assertIsNotNone(
            match,
            f"{path}: could not locate a quoted Content-Security-Policy "
            "value to inspect for jsdelivr allowance.",
        )
        assert match is not None
        csp_value = match.group(1)
        # Geist fonts (loaded by the design system) come from jsdelivr.
        # CSP must allow them in font-src and/or style-src.
        directives = {
            d.strip().split()[0]: d.strip()
            for d in csp_value.split(";")
            if d.strip()
        }
        font_src = directives.get("font-src", "")
        style_src = directives.get("style-src", "")
        self.assertTrue(
            "https://cdn.jsdelivr.net" in font_src
            or "https://cdn.jsdelivr.net" in style_src,
            f"{path}: CSP must allow 'https://cdn.jsdelivr.net' in "
            "font-src and/or style-src so the design system's Geist "
            f"fonts load (got font-src={font_src!r}, "
            f"style-src={style_src!r}).",
        )

    def test_has_x_frame_options_deny(self) -> None:
        path, text = _read_config()
        self.assertRegex(
            text,
            r"(?mi)^\s*add_header\s+X-Frame-Options\s+\"DENY\"",
            f"{path}: missing 'add_header X-Frame-Options \"DENY\" ...;'.",
        )

    def test_has_x_content_type_options_nosniff(self) -> None:
        path, text = _read_config()
        self.assertRegex(
            text,
            r"(?mi)^\s*add_header\s+X-Content-Type-Options\s+\"nosniff\"",
            f"{path}: missing 'add_header X-Content-Type-Options "
            "\"nosniff\" ...;'.",
        )

    def test_has_referrer_policy(self) -> None:
        path, text = _read_config()
        self.assertRegex(
            text,
            r"(?mi)^\s*add_header\s+Referrer-Policy\b",
            f"{path}: missing 'add_header Referrer-Policy ...;' header.",
        )

    def test_gzip_enabled(self) -> None:
        path, text = _read_config()
        self.assertRegex(
            text,
            r"(?m)^\s*gzip\s+on\s*;",
            f"{path}: missing 'gzip on;' — text/JS/CSS responses must "
            "be compressed.",
        )

    def test_root_no_cache(self) -> None:
        path, text = _read_config()
        # Match the bare 'location /' block (not /api/, not /assets/).
        body = _block(text, r"location\s+/\s*")
        self.assertIsNotNone(
            body,
            f"{path}: missing 'location / {{ ... }}' block — required "
            "for SPA fallback to index.html.",
        )
        assert body is not None
        self.assertRegex(
            body,
            r"(?mi)^\s*add_header\s+Cache-Control\s+\"no-cache\"",
            f"{path}: 'location /' must set 'Cache-Control \"no-cache\"' "
            "so dashboard updates take effect after deploy without users "
            "having to hard-refresh.",
        )

    def test_proxy_forwards_real_ip(self) -> None:
        path, text = _read_config()
        body = _block(text, r"location\s+/api/\s*")
        self.assertIsNotNone(
            body,
            f"{path}: '/api/' block missing — cannot validate proxy "
            "headers.",
        )
        assert body is not None
        self.assertRegex(
            body,
            r"(?m)^\s*proxy_set_header\s+X-Real-IP\b",
            f"{path}: '/api/' proxy must set 'proxy_set_header "
            "X-Real-IP ...;' so the controller sees the originating "
            "client IP.",
        )
        self.assertRegex(
            body,
            r"(?m)^\s*proxy_set_header\s+X-Forwarded-For\b",
            f"{path}: '/api/' proxy must set 'proxy_set_header "
            "X-Forwarded-For ...;' to preserve the client IP chain.",
        )


if __name__ == "__main__":
    unittest.main()
