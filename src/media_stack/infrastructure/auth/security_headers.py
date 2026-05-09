"""Central security-header policy for every controller HTTP response.

Why extract this
----------------

Hardening headers were inlined in ``_AuthPolicy.emit_security_headers``
on ``server.py``. A single-file copy makes three things hard:

1. New endpoints may forget to call the helper — silent gap.
2. Per-endpoint variation (e.g. admin pages want stricter CSP than
   the legacy dashboard) has no clean extension point.
3. Testing the policy in isolation required loading ``server.py``
   (heavy imports, argon2, user store, etc.).

This module centralises the policy as a data object, exposes two
canonical presets (``STRICT_POLICY`` for new admin surfaces,
``LEGACY_DASHBOARD_POLICY`` for the inline-scripts dashboard), and
returns a ``SecurityHeaders`` instance whose ``apply`` method walks a
``BaseHTTPRequestHandler``. A ratchet
(``tests/unit/test_security_headers_ratchet.py``) enforces that every
route emits one of these presets so the gap in (1) fails CI.

Philosophy
----------

- **Defence in depth.** CSP alone is not enough; COOP/COEP/CORP
  reduce Spectre-class leaks, HSTS pins TLS, Referrer-Policy limits
  caller leaks, Permissions-Policy disables device APIs we never
  use.
- **Sensible escape hatches.** If a particular endpoint needs to
  relax a header (e.g. the legacy dashboard needs
  ``script-src 'unsafe-inline'`` until the inline JS is extracted),
  it picks the legacy preset — a visible, named opt-in, not a
  magic string.
- **No server fingerprinting.** We strip / rewrite ``Server`` and
  ``X-Powered-By`` via ``strip_server_banner``.

CIA / AAA alignment
-------------------

- **Confidentiality**: CSP + COOP/COEP + `Cache-Control: no-store`
  on auth-gated pages prevent cross-origin reads of sensitive data
  and defeat browser-cache disclosure.
- **Integrity**: X-Content-Type-Options prevents MIME confusion
  that would let a text response be re-interpreted as JS.
- **Availability**: these headers are cheap; they don't hurt perf
  (Lighthouse best-practices score requires them).
- **Authentication** / **Authorization** live at the auth layer.
- **Accounting**: every response's headers can be asserted in tests
  — `apply` is deterministic given the policy.

Module layout (ADR-0012)
------------------------
All loose helpers live as instance methods on
``SecurityHeaderEmitter`` (``apply_policy``, ``merged_headers``,
``append_directive``). The two policy types remain frozen dataclasses
because immutable value-object semantics are load-bearing for
``with_overrides`` and the test ratchets. A process-wide
``_INSTANCE`` exposes module-level aliases for every public + legacy
underscore name so callers and ``mock.patch`` targets keep working
without change.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping


# --------------------------------------------------------------------------
# Individual CSP directive helpers — kept strict by default.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CSPPolicy:
    """Content-Security-Policy as a structured object so tests can
    assert on individual directives without parsing strings."""

    default_src: tuple[str, ...] = ("'self'",)
    script_src: tuple[str, ...] = ("'self'",)
    style_src: tuple[str, ...] = ("'self'",)
    img_src: tuple[str, ...] = ("'self'", "data:")
    font_src: tuple[str, ...] = ("'self'",)
    connect_src: tuple[str, ...] = ("'self'",)
    frame_ancestors: tuple[str, ...] = ("'none'",)
    base_uri: tuple[str, ...] = ("'self'",)
    form_action: tuple[str, ...] = ("'self'",)
    object_src: tuple[str, ...] = ("'none'",)
    # When non-empty, requires 'script' to be loaded via a Trusted
    # Types policy — browser blocks raw DOM sinks. We turn this on
    # for STRICT_POLICY.
    require_trusted_types_for: tuple[str, ...] = ()

    def render(self) -> str:
        """Render directives in the canonical order, joined with ``; ``.

        Empty directive tuples are omitted entirely (matches browser
        tolerance: an absent directive falls back to ``default-src``).
        """
        parts: list[str] = []
        append = _INSTANCE.append_directive
        append(parts, "default-src", self.default_src)
        append(parts, "script-src", self.script_src)
        append(parts, "style-src", self.style_src)
        append(parts, "img-src", self.img_src)
        append(parts, "font-src", self.font_src)
        append(parts, "connect-src", self.connect_src)
        append(parts, "frame-ancestors", self.frame_ancestors)
        append(parts, "base-uri", self.base_uri)
        append(parts, "form-action", self.form_action)
        append(parts, "object-src", self.object_src)
        append(
            parts, "require-trusted-types-for",
            self.require_trusted_types_for,
        )
        return "; ".join(parts)


# --------------------------------------------------------------------------
# Full header policy.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SecurityHeaders:
    """Policy object whose ``apply`` sends every header to a handler.

    Immutable — call ``with_overrides(...)`` for per-endpoint tweaks.
    """

    csp: CSPPolicy = field(default_factory=CSPPolicy)
    hsts: str = "max-age=31536000; includeSubDomains"
    x_content_type_options: str = "nosniff"
    x_frame_options: str = "DENY"
    referrer_policy: str = "strict-origin-when-cross-origin"
    permissions_policy: str = (
        "geolocation=(), camera=(), microphone=(), "
        "payment=(), usb=(), accelerometer=(), gyroscope=(), "
        "magnetometer=(), interest-cohort=()"
    )
    cross_origin_opener_policy: str = "same-origin"
    cross_origin_embedder_policy: str = ""
    cross_origin_resource_policy: str = "same-origin"
    # Sensitive (auth-gated) responses MUST be no-store to prevent
    # browser / intermediate cache disclosure of another user's view.
    cache_control: str = "no-store, no-cache, must-revalidate, private"
    # When True, ``apply`` also overrides the default Server banner.
    strip_server_banner: bool = True

    def as_header_dict(self) -> dict[str, str]:
        """Return the full set as a plain ``{name: value}`` mapping.
        Headers with empty values are omitted entirely."""
        out: dict[str, str] = {}
        csp = self.csp.render()
        if csp:
            out["Content-Security-Policy"] = csp
        if self.hsts:
            out["Strict-Transport-Security"] = self.hsts
        if self.x_content_type_options:
            out["X-Content-Type-Options"] = self.x_content_type_options
        if self.x_frame_options:
            out["X-Frame-Options"] = self.x_frame_options
        if self.referrer_policy:
            out["Referrer-Policy"] = self.referrer_policy
        if self.permissions_policy:
            out["Permissions-Policy"] = self.permissions_policy
        if self.cross_origin_opener_policy:
            out["Cross-Origin-Opener-Policy"] = self.cross_origin_opener_policy
        if self.cross_origin_embedder_policy:
            out["Cross-Origin-Embedder-Policy"] = self.cross_origin_embedder_policy
        if self.cross_origin_resource_policy:
            out["Cross-Origin-Resource-Policy"] = self.cross_origin_resource_policy
        if self.cache_control:
            out["Cache-Control"] = self.cache_control
        if self.strip_server_banner:
            # Overwrites BaseHTTPRequestHandler's default server
            # banner. We use "media-stack" rather than empty because
            # empty values cause some proxies to drop the header +
            # log a warning.
            out["Server"] = "media-stack"
        return out

    def apply(self, handler: object) -> None:
        """Send every configured header on the given handler.

        ``handler`` is a ``http.server.BaseHTTPRequestHandler``; we
        type it as object + duck-typed because importing the stdlib
        class here creates a heavy-import chain in test fixtures.
        """
        send_header = getattr(handler, "send_header")
        for name, value in self.as_header_dict().items():
            send_header(name, value)

    def with_overrides(self, **changes: object) -> "SecurityHeaders":
        """Return a new policy with the given fields replaced.

        Use for per-endpoint tweaks (e.g. swap to
        ``LEGACY_DASHBOARD_POLICY`` for the big inline-JS dashboard)
        without mutating the shared defaults.
        """
        return replace(self, **changes)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Canonical presets.
# --------------------------------------------------------------------------


STRICT_POLICY = SecurityHeaders(
    csp=CSPPolicy(
        # No 'unsafe-inline' for scripts — admin pages extract their
        # JS into files that get served from /static/. Inline styles
        # are still permitted because the new tabs use small amounts
        # of per-element style injection for state indicators.
        style_src=("'self'", "'unsafe-inline'"),
        require_trusted_types_for=("'script'",),
    ),
    cross_origin_embedder_policy="require-corp",
)

LEGACY_DASHBOARD_POLICY = SecurityHeaders(
    csp=CSPPolicy(
        # dashboard.html carries inline scripts + styles until it's
        # fully extracted. Until that migration ships, the legacy
        # preset grants 'unsafe-inline' for both.
        script_src=("'self'", "'unsafe-inline'"),
        style_src=("'self'", "'unsafe-inline'"),
        # The legacy dashboard loads from external CDN-agnostic image
        # sources (poster art from media providers) — keep that
        # permissive while the UI is audited.
        img_src=("'self'", "data:", "https:"),
    ),
    # Auth-gated admin responses must NOT land in browser cache —
    # ``no-store`` prevents another user on a shared machine hitting
    # Back and seeing this user's dashboard. ``private`` is belt-and-
    # braces for intermediate caches that ignore no-store.
    cache_control="no-store, no-cache, must-revalidate, private",
    # Relax Referrer-Policy as per server.py's note on Envoy same-
    # origin routing (see commit history).
    referrer_policy="same-origin",
    cross_origin_embedder_policy="",
    # Assets may legitimately be fetched from same-site subdomains
    # (the apex vhost routes /app/jellyfin/* to the Jellyfin backend
    # on the same registrable domain). ``same-origin`` here would
    # block that. ``same-site`` gives us the cross-origin-read block
    # (the core Spectre mitigation) without breaking subdomain
    # serving.
    cross_origin_resource_policy="same-site",
)


# Public policy — emitted on every response by default. Swapped via
# handler-local ``with_overrides`` when a route needs something
# specific (e.g. public health endpoints don't need COEP).
DEFAULT_POLICY = LEGACY_DASHBOARD_POLICY


# --------------------------------------------------------------------------
# Module-level emission helpers — class-wrapped per ADR-0012.
# --------------------------------------------------------------------------


class SecurityHeaderEmitter:
    """Thin façade for the loose helpers this module used to expose.

    All methods are plain instance methods (no ``@staticmethod``);
    a single ``_INSTANCE`` is constructed at import time and every
    public + legacy underscore name is re-exported as a module-level
    alias so callers and ``mock.patch`` targets keep working.
    """

    def append_directive(
        self,
        parts: list[str],
        name: str,
        values: tuple[str, ...],
    ) -> None:
        """Push ``"<name> <values...>"`` onto ``parts``, skipping empty
        directive tuples. Used by ``CSPPolicy.render``."""
        if not values:
            return
        parts.append(f"{name} {' '.join(values)}")

    def apply_policy(
        self,
        handler: object,
        policy: SecurityHeaders | None = None,
    ) -> None:
        """Convenience: send the given policy (or DEFAULT_POLICY) on
        ``handler``. Keeps the call sites readable —
        ``apply_policy(self)`` at the top of a response path is cheaper
        to scan than a dozen individual ``send_header`` calls.
        """
        chosen = policy if policy is not None else DEFAULT_POLICY
        chosen.apply(handler)

    def merged_headers(
        self,
        policy: SecurityHeaders,
        overrides: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Render ``policy`` to a dict, then merge ``overrides`` on top.

        Used by handlers that need to add one or two extra headers
        (e.g. ``Content-Type``, ``Content-Length``) without mutating the
        shared policy object.
        """
        out = policy.as_header_dict()
        if overrides:
            out.update(overrides)
        return out


_INSTANCE = SecurityHeaderEmitter()

# Module-level aliases — every public + legacy underscore name is
# bound here so existing imports + ``mock.patch`` targets keep
# working unchanged.
_append = _INSTANCE.append_directive
apply_policy = _INSTANCE.apply_policy
merged_headers = _INSTANCE.merged_headers


__all__ = [
    "CSPPolicy",
    "DEFAULT_POLICY",
    "LEGACY_DASHBOARD_POLICY",
    "STRICT_POLICY",
    "SecurityHeaders",
    "SecurityHeaderEmitter",
    "apply_policy",
    "merged_headers",
]
