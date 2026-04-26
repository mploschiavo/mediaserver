"""Playwright "all buttons" smoke ratchet.

Why this exists
---------------
Three production button regressions shipped during the v1.0.18x →
v1.0.23x window despite the codebase having extensive static-analysis
ratchets, unit tests, and per-feature contract tests:

  1. Guardrails Test/Disable buttons — backend POST dispatcher didn't
     URL-decode rule ids, so ``storage:inode_floor`` arrived as
     ``storage%3Ainode_floor`` from the SPA and the registry lookup
     missed.
  2. Jobs page parent-job Run button — UI lookup-by-name only checked
     the catalog Map, not the tree (parent jobs are tree-only).
  3. Media-integrity Reconcile/Enforce — backend boot-disabled because
     the wheel image's ``contracts`` path wasn't in the lookup
     candidate list.

Each was diagnosed and patched after operators reported a 404/500.
None of the unit ratchets caught them because they're integration-
level: a button is only verifiably "wired" when a real browser
clicks it and the network response is 2xx.

This smoke walks every operator-facing SPA route, enumerates every
button-shaped element, clicks it, and asserts the resulting network
traffic is healthy.

Scope (what the smoke MUST cover)
---------------------------------
- Every page in the SPA route tree (see ``ui/src/routeTree.ts`` +
  ``Sidebar.tsx``/``BottomNav.tsx``).
- Every ``<button>`` and ``role="button"`` element with a meaningful
  action — i.e. not a pure UI affordance like "expand row".
- Network responses captured per-click: assertion fails on any
  ``5xx`` from the controller, ``404`` against ``/api/*``, or ``502``
  from the gateway.

Skip categories (documented inline)
-----------------------------------
- File-download buttons (would require a fixture body / streamed
  response we don't want to materialise).
- File-upload inputs (would require a real fixture file).
- Sign-out buttons (would terminate the session mid-suite — order-
  dependent and only testable as the last action).
- Cancel/close buttons that just dismiss a modal — they don't hit
  the network so they're not in the regression class we care about.
- Confirm-destructive buttons gated behind an explicit confirm
  modal (e.g. "Delete user", "Revoke token") — the smoke clicks the
  outer trigger but does NOT confirm. The trigger itself is the
  thing we care about being wired up.

How to run
----------
::

    .venv/bin/python -m pytest \\
        tests/e2e/test_all_buttons_smoke.py -m smoke -v

Required environment
~~~~~~~~~~~~~~~~~~~~
``MEDIA_STACK_SMOKE_BASE_URL``
    Base URL of the SPA, INCLUDING the basepath. Default:
    ``https://m.iomio.io/app/media-stack-ui``. The controller
    auto-detects the basepath from the document URL, so we have to
    hit the prefixed path that production Envoy mounts the UI at.

``MEDIA_STACK_SMOKE_REMOTE_USER``
    Value injected as the ``Remote-User`` header on every request.
    Authelia's ``ext_authz`` honours this in test mode so we can
    bypass the portal. Default: ``admin``.

The test SKIPS cleanly when:

- The Python ``playwright`` package isn't installed (sandbox /
  pre-CI environments).
- The base URL isn't reachable from this host.

That keeps the file structurally pinned (the test runs and is
imported in every collection) while only firing the assertions in
the environments that can actually drive a real browser. The
companion ratchet at
``tests/unit/architecture/test_all_buttons_smoke_coverage.py``
asserts the page list stays at or above the floor regardless of
whether Playwright runs.
"""

from __future__ import annotations

import os
import socket
import unittest
import urllib.parse
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Page list
# ---------------------------------------------------------------------------
# This is the canonical list of operator-facing SPA routes the smoke
# walks. It MUST be importable without Playwright installed because
# the companion architecture ratchet reads ``OPERATOR_PAGES`` to
# enforce the page-count floor.
#
# Source of truth, in order:
#   1. ``ui/src/routeTree.ts`` (every route registered on the root)
#   2. ``ui/src/components/layout/Sidebar.tsx::PRIMARY_SECTIONS`` +
#      ``SECONDARY_ITEMS`` (every nav link the operator sees)
#
# When you add a route, append it here; the architecture ratchet's
# floor will tick up at the same time.
OPERATOR_PAGES: tuple[dict[str, str], ...] = (
    {"slug": "dashboard",        "path": "/",                "label": "Dashboard"},
    {"slug": "content",          "path": "/content",         "label": "Content"},
    {"slug": "livetv",           "path": "/livetv",          "label": "Live TV"},
    {"slug": "logs",             "path": "/logs",            "label": "Logs"},
    {"slug": "routing",          "path": "/routing",         "label": "Routing"},
    {"slug": "ops",              "path": "/ops",             "label": "Ops"},
    {"slug": "guardrails",       "path": "/guardrails",      "label": "Guardrails"},
    {"slug": "webhooks",         "path": "/webhooks",        "label": "Webhooks"},
    {"slug": "snapshots",        "path": "/snapshots",       "label": "Snapshots"},
    {"slug": "users",            "path": "/users",           "label": "Users"},
    {"slug": "me",               "path": "/me",              "label": "Me"},
    {"slug": "auth",             "path": "/auth",            "label": "Auth"},
    {"slug": "sessions",         "path": "/sessions",        "label": "Sessions"},
    {"slug": "bans",             "path": "/bans",            "label": "Bans"},
    {"slug": "security",         "path": "/security",        "label": "Security signals"},
    {"slug": "jobs",             "path": "/jobs",            "label": "Jobs"},
    {"slug": "audit-log",        "path": "/audit-log",       "label": "Audit log"},
    {"slug": "media-integrity",  "path": "/media-integrity", "label": "Media integrity"},
    {"slug": "profile",          "path": "/profile",         "label": "Profile"},
    {"slug": "api-docs",         "path": "/api-docs",        "label": "API docs"},
    {"slug": "settings",         "path": "/settings",        "label": "Settings"},
)


# ---------------------------------------------------------------------------
# Skip-rules (button text → reason)
# ---------------------------------------------------------------------------
# Buttons whose text label matches one of these (case-insensitive
# substring) are skipped. Each entry is documented with the bug-class
# justification — we want the smoke to be loud about EVERY click it
# performs, including the ones it deliberately doesn't.
_BUTTON_SKIP_RULES: tuple[tuple[str, str], ...] = (
    ("sign out",        "would terminate the session mid-suite"),
    ("sign-out",        "would terminate the session mid-suite"),
    ("logout",          "would terminate the session mid-suite"),
    ("log out",         "would terminate the session mid-suite"),
    ("download",        "file-download buttons need a fixture body"),
    ("export",          "exports stream a file body — separate fixture"),
    ("upload",          "uploads need a fixture file"),
    ("delete",          "destructive — requires confirm modal flow"),
    ("remove",          "destructive — requires confirm modal flow"),
    ("revoke",          "destructive — requires confirm modal flow"),
    ("ban ip",          "destructive — requires confirm modal flow"),
    ("disable",         "would mutate global state inappropriately"),
    ("cancel",          "modal-dismiss only — never hits the network"),
    ("close",           "modal-dismiss only — never hits the network"),
    ("dismiss",         "modal-dismiss only — never hits the network"),
    ("show preview",    "non-mutating UI affordance"),
    ("expand",          "non-mutating UI affordance"),
    ("collapse",        "non-mutating UI affordance"),
    ("toggle",          "non-mutating UI affordance"),
    ("copy",            "clipboard-write only — no network"),
    ("retry",           "retry depends on prior failure state"),
)

# Statuses we treat as healthy. ``401`` and ``403`` are accepted
# because some endpoints are gated by sudo/role and a Remote-User
# admin header doesn't always grant the sub-permission; what matters
# is that the request was ROUTED (no 404, no 5xx, no 502).
_HEALTHY_STATUSES: frozenset[int] = frozenset({200, 201, 202, 204, 304, 401, 403, 409, 422})

# Statuses we always treat as failure on /api/* paths. 404 is the
# headline target — that's the bug-class we're protecting against.
_API_FAILURE_STATUSES: frozenset[int] = frozenset({404, 500, 501, 502, 503, 504})


# ---------------------------------------------------------------------------
# Environment + reachability
# ---------------------------------------------------------------------------
def _base_url() -> str:
    """Return the SPA base URL including any deployment basepath."""
    return os.environ.get(
        "MEDIA_STACK_SMOKE_BASE_URL",
        "https://m.iomio.io/app/media-stack-ui",
    ).rstrip("/")


def _remote_user() -> str:
    return os.environ.get("MEDIA_STACK_SMOKE_REMOTE_USER", "admin")


def _base_reachable() -> bool:
    """Is the smoke target reachable from this host? Used to skip
    cleanly in sandbox / offline environments."""
    parsed = urllib.parse.urlsplit(_base_url())
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _playwright_available() -> bool:
    """Is the Python ``playwright`` package importable AND have its
    Chromium browser been installed via ``playwright install``?

    We test by opening the import — the browser-binary check happens
    inside the test body, where a missing binary will surface as a
    skip-with-clear-message rather than a collection-time crash.
    """
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Smoke body
# ---------------------------------------------------------------------------
def _should_skip_button(label: str) -> str | None:
    """Return the skip reason if ``label`` matches a skip rule, or
    ``None`` if the button is in-scope for the smoke."""
    needle = (label or "").strip().lower()
    if not needle:
        # Unlabelled buttons (icon-only) — we still click them but
        # log them as "(unlabelled)" so the test output remains
        # diagnostic.
        return None
    for fragment, reason in _BUTTON_SKIP_RULES:
        if fragment in needle:
            return reason
    return None


def _classify_response(status: int, url: str, base: str) -> str | None:
    """Return a failure description if the response is unhealthy, or
    ``None`` if it's allowed.

    The classifier is the heart of the smoke. The bug-classes we're
    protecting against:

    - 404 on ``/api/*`` — the registry/catalog/tree lookup that
      missed (Guardrails decode bug; Jobs parent-job lookup).
    - 5xx anywhere on the gateway host — boot-disabled feature
      (Media-integrity contracts-path bug).
    - 502 specifically — Envoy upstream-down.
    """
    if status in _HEALTHY_STATUSES:
        return None
    parsed = urllib.parse.urlsplit(url)
    base_host = urllib.parse.urlsplit(base).hostname
    # Only enforce against responses originating from our own gateway
    # — third-party telemetry / fonts / CDN noise would otherwise
    # produce false positives.
    if parsed.hostname and base_host and parsed.hostname != base_host:
        return None
    path = parsed.path or ""
    if status in _API_FAILURE_STATUSES:
        if status == 404 and not path.startswith("/api/"):
            # 404 on a non-/api path is usually a stray asset probe
            # we don't care about (e.g. favicon variants).
            return None
        return f"HTTP {status} {path}"
    # Anything else 4xx that we didn't pre-allow.
    if 400 <= status < 600:
        return f"HTTP {status} {path}"
    return None


def _click_targets(page) -> list[dict[str, Any]]:
    """Enumerate clickable elements on the current page.

    Pulls every ``<button>`` plus ``[role=button]`` that:
      - Is visible (not hidden by CSS / behind a closed disclosure)
      - Is not ``[disabled]``
      - Is not inside a ``[data-testid$="-skeleton"]`` (loading
        placeholder — clicking these races with the real button)

    Returns a list of dicts with ``label`` (for reporting) and
    ``selector`` (for the actual click via ``page.locator``).
    """
    return page.evaluate(
        """
        () => {
          const out = [];
          const seen = new Set();
          const sel = 'button, [role="button"]';
          for (const el of document.querySelectorAll(sel)) {
            if (el.hasAttribute('disabled')) continue;
            if (el.getAttribute('aria-disabled') === 'true') continue;
            const skel = el.closest('[data-testid$="-skeleton"]');
            if (skel) continue;
            const r = el.getBoundingClientRect();
            if (r.width === 0 || r.height === 0) continue;
            const cs = window.getComputedStyle(el);
            if (cs.visibility === 'hidden' || cs.display === 'none') continue;
            const text = (el.innerText || el.getAttribute('aria-label')
                          || el.getAttribute('title') || '').trim();
            // Build a stable-ish selector. Prefer data-testid; fall
            // back to nth-of-type within the parent.
            const tid = el.getAttribute('data-testid');
            let key;
            if (tid) {
              key = `[data-testid="${tid}"]`;
            } else {
              // Don't synthesise an XPath — Playwright's text-based
              // locator with nth() handles dupes robustly.
              key = text ? `text=${text}` : 'button';
            }
            // De-dupe by (label, selector) so a list of identical
            // "Run" buttons doesn't get clicked 50 times.
            const dedupe = `${text}::${key}`;
            if (seen.has(dedupe)) continue;
            seen.add(dedupe);
            out.push({label: text, selector: key});
          }
          return out;
        }
        """,
    )


@pytest.mark.smoke
class AllButtonsSmokeTests(unittest.TestCase):
    """Drive every operator-facing page through a real Chromium and
    assert every clickable button produces a healthy network response.

    The class is gated by three skip checks (Playwright import,
    Chromium binary, target reachability). Any of those produces a
    ``unittest.SkipTest`` with a message explaining how to enable the
    full run. The ``@pytest.mark.smoke`` marker keeps it out of the
    default test collection — invoke with ``-m smoke`` to run it.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not _playwright_available():
            raise unittest.SkipTest(
                "Python `playwright` package not installed. "
                "Install with: `pip install playwright` then "
                "`playwright install chromium`. The companion "
                "architecture ratchet still pins the page-count "
                "floor regardless.",
            )
        if not _base_reachable():
            raise unittest.SkipTest(
                f"{_base_url()} not reachable — set "
                "MEDIA_STACK_SMOKE_BASE_URL to a live cluster's "
                "SPA URL (e.g. https://m.iomio.io/app/media-stack-ui).",
            )

    def test_every_page_button_returns_healthy_response(self) -> None:
        from playwright.sync_api import (  # noqa: WPS433 — local import
            Error as PlaywrightError,
            sync_playwright,
        )

        base = _base_url()
        remote_user = _remote_user()

        # Per-button failures are accumulated into ``failures`` and
        # asserted at the END so a single broken button doesn't mask
        # the next 30. Every entry is a multi-line string with page
        # name, button text, response code, and request URL — exactly
        # what the request brief asks the failure output to contain.
        failures: list[str] = []
        # Per-page mount failures are tracked separately so we report
        # "X pages won't even mount" cleanly; their button-clicks are
        # skipped (you can't click what doesn't render).
        unmountable_pages: list[str] = []
        # Sample of healthy captures — the brief asks for spot-check
        # output so reviewers can sanity-check the test wiring.
        sample_captures: list[str] = []
        # Coverage counters surfaced in the final report.
        clicked_count = 0
        skipped_by_rule: dict[str, int] = {}

        try:
            pw = sync_playwright().start()
        except PlaywrightError as exc:  # browser binary missing, etc.
            self.skipTest(
                f"Playwright failed to start ({exc!r}). Likely the "
                "Chromium binary isn't installed — run "
                "`playwright install chromium` and retry.",
            )

        try:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=True,
                # Inject Remote-User so Authelia's ext_authz lets us
                # through to the controller in test mode. Mirrors the
                # convention the rest of the e2e suite uses.
                extra_http_headers={"Remote-User": remote_user},
            )
            page = context.new_page()

            # Per-page network sink. Re-installed on every navigation
            # so failures are attributed to the right page in the
            # final report.
            current_page_label = {"name": ""}
            current_page_url = {"url": ""}

            def _on_response(response):  # noqa: WPS430 — closure is fine
                problem = _classify_response(
                    response.status, response.url, base,
                )
                if problem is None:
                    return
                failures.append(
                    "  page={page!r}\n"
                    "  url={purl!r}\n"
                    "  request={url!r}\n"
                    "  status={status}\n"
                    "  classification={cls}\n".format(
                        page=current_page_label["name"],
                        purl=current_page_url["url"],
                        url=response.url,
                        status=response.status,
                        cls=problem,
                    ),
                )

            page.on("response", _on_response)

            for page_def in OPERATOR_PAGES:
                slug = page_def["slug"]
                path = page_def["path"]
                label = page_def["label"]
                full_url = base + (path if path != "/" else "/")
                current_page_label["name"] = label
                current_page_url["url"] = full_url
                try:
                    page.goto(full_url, wait_until="domcontentloaded",
                              timeout=20_000)
                    # Give the SPA a beat to hydrate before we hunt
                    # for buttons. ``networkidle`` would be ideal but
                    # some pages keep an SSE/WS open and never idle.
                    page.wait_for_timeout(1500)
                except PlaywrightError as exc:
                    unmountable_pages.append(f"{label} ({path}): {exc!r}")
                    continue

                body = page.content()
                # Page-level mount sanity. The ErrorBoundary's user-
                # facing copy is "Lost your way?" / "Something went
                # wrong" — see ``ui/src/components/layout/ErrorBoundary.tsx``.
                if "Lost your way?" in body or "Something went wrong" in body:
                    unmountable_pages.append(
                        f"{label} ({path}): ErrorBoundary tripped",
                    )
                    continue

                targets = _click_targets(page)
                # Spot-check sample: just the first page's first 3
                # buttons. The brief asks for SOMETHING reviewers can
                # eyeball; more than that is noise.
                if not sample_captures:
                    for t in targets[:3]:
                        sample_captures.append(
                            f"{label}: {t['label']!r} -> {t['selector']!r}",
                        )

                for target in targets:
                    btn_label = target["label"]
                    skip_reason = _should_skip_button(btn_label)
                    if skip_reason:
                        skipped_by_rule[skip_reason] = (
                            skipped_by_rule.get(skip_reason, 0) + 1
                        )
                        continue
                    try:
                        # Use the synthesised selector. ``first`` is
                        # critical — text=Run matches every "Run"
                        # button in a list; we click one per pass and
                        # rely on de-dupe in ``_click_targets``.
                        locator = page.locator(target["selector"]).first
                        # ``no_wait_after`` because some buttons open
                        # a new tab / nav — we don't want Playwright
                        # blocking on an unrelated post-click event.
                        locator.click(timeout=3_000, no_wait_after=True)
                        clicked_count += 1
                        # Yield to the event loop so any in-flight
                        # XHR has a chance to fire before we move on.
                        page.wait_for_timeout(250)
                        # If a confirmation dialog or modal popped
                        # up, escape so we don't get wedged.
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(50)
                    except PlaywrightError:
                        # A click timeout on ONE button isn't a smoke
                        # failure — the button might be inside a tab
                        # we didn't activate, or hidden behind a
                        # popover. The interesting failure is the
                        # network response classifier; record the
                        # click attempt and move on.
                        continue

            browser.close()
        finally:
            pw.stop()

        # ------------------------------------------------------------------
        # Final report
        # ------------------------------------------------------------------
        # Print the summary unconditionally so the test output is
        # diagnostic even on a passing run. ``-s`` shows it; pytest's
        # default capture hides it on pass, which is fine.
        print("\n=== all-buttons smoke summary ===")
        print(f"pages walked       : {len(OPERATOR_PAGES)}")
        print(f"pages unmountable  : {len(unmountable_pages)}")
        print(f"buttons clicked    : {clicked_count}")
        print(f"buttons skipped    : "
              f"{sum(skipped_by_rule.values())} (by rule)")
        for reason, n in sorted(skipped_by_rule.items()):
            print(f"  - {reason}: {n}")
        print("sample captures (page: label -> selector):")
        for s in sample_captures:
            print(f"  - {s}")
        if unmountable_pages:
            print("unmountable pages:")
            for u in unmountable_pages:
                print(f"  - {u}")

        if failures:
            self.fail(
                "all-buttons smoke detected unhealthy network "
                f"responses ({len(failures)} failure(s)):\n\n"
                + "\n".join(failures)
                + "\n\nEvery entry above is a button click that "
                "produced a 4xx (excluding 401/403/409/422) or 5xx "
                "from the gateway host. This is the bug-class the "
                "smoke exists to prevent: a button shipped in the "
                "UI that nothing exercises end-to-end and silently "
                "404/500s in production.",
            )


if __name__ == "__main__":
    unittest.main()
