"""Ratchet: Envoy redirect rules must not produce a path-prefix
loop when run through the Lua response-header normaliser.

The 2026-04-21 bug: hitting
``https://apps.media-stack.local/app/controller`` returned
``Location: /app/controller/app/media-stack-controller`` —
then following that redirect appended another
``/app/media-stack-controller``, and so on forever. Root cause:
the Lua ``envoy_on_response`` filter re-prepends the *request*
path's first segment (``/app/controller``) to any ``Location``
header that doesn't already start with that prefix. A redirect
that intentionally hops to a different service's prefix
(``/app/controller`` → ``/app/media-stack-controller``) gets the
request prefix stuck on the front of the new location.

This test simulates the Lua's decision for every explicit
``path_redirect`` in the generated Envoy config and asserts no
combination produces the loop shape ``<prefix>/<prefix-rewrite>``.
Two cases we care about:

1. A redirect FROM ``/app/<A>`` TO ``/app/<B>`` must emit
   ``Location: /app/<B>`` (not ``/app/<A>/app/<B>``).
2. A redirect FROM ``/app/<A>`` TO ``/app/<A>/`` (the common
   trailing-slash canonicaliser) is fine and must NOT be
   double-prefixed either.

If the Lua rule changes, update the simulation below. If the
Envoy config grows a new kind of redirect that this simulation
doesn't cover, add the case."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


_ENVOY_YAML = ROOT / "dist" / "config" / "envoy" / "envoy.yaml"


def _simulate_lua_location_rewrite(
    request_path: str,
    redirect_location: str,
) -> str:
    """Mirror ``envoy_on_response`` in envoy.runtime.base.yaml:
    extract the request's ``/app/<svc>`` prefix, then decide
    whether to prepend it to the redirect's Location header.

    Must stay in lockstep with the Lua in
    ``config/defaults/compose/envoy.runtime.base.yaml`` — any
    change there needs the equivalent change here."""
    # Extract prefix like the Lua does — parse /app/<service> off
    # the request path.
    m = re.match(r"^(/app/[^/]+)", request_path)
    if not m:
        return redirect_location
    prefix = m.group(1)
    normalized = redirect_location
    if not normalized:
        return normalized
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    already_app_prefixed = normalized.startswith("/app/")
    if not already_app_prefixed and not normalized.startswith(prefix):
        return prefix + normalized
    return normalized


def _extract_explicit_redirects() -> list[tuple[str, str]]:
    """Walk ``envoy.yaml`` and return every
    ``(matched_path, target_path)`` pair from ``path_redirect``
    rules. Ignores ``prefix_rewrite`` rules (those are path
    transforms on route matches, not Location-header redirects)."""
    pairs: list[tuple[str, str]] = []
    if not _ENVOY_YAML.is_file():
        return pairs
    text = _ENVOY_YAML.read_text(encoding="utf-8")
    # Very small ad-hoc parser: look for blocks of the form
    #   - match:
    #       path: <P>
    #     redirect:
    #       path_redirect: <T>
    block_re = re.compile(
        r"-\s*match:\s*\n\s*path:\s*([^\s\n]+)\s*\n\s*redirect:\s*\n\s*path_redirect:\s*([^\s\n]+)",
        re.MULTILINE,
    )
    for m in block_re.finditer(text):
        pairs.append((m.group(1).strip(), m.group(2).strip()))
    return pairs


class EnvoyRedirectNoLoopTests(unittest.TestCase):

    def test_no_path_redirect_produces_nested_prefix(self) -> None:
        """For every explicit ``path_redirect`` rule, simulate the
        Lua rewrite and assert the resulting Location does NOT
        contain the original prefix appended to a new prefix."""
        if not _ENVOY_YAML.is_file():
            self.skipTest(
                "dist/config/envoy/envoy.yaml not generated yet"
            )
        pairs = _extract_explicit_redirects()
        self.assertGreater(
            len(pairs), 5,
            "Redirect-rule extractor found fewer rules than "
            "expected — parser likely broken after an envoy "
            "config refactor.",
        )
        bad: list[str] = []
        for matched_path, target in pairs:
            simulated = _simulate_lua_location_rewrite(
                matched_path, target,
            )
            # Loop shape: the simulated Location starts with the
            # request path AND contains a second /app/ segment.
            starts_with_request = simulated.startswith(matched_path + "/app/")
            if starts_with_request:
                bad.append(
                    f"{matched_path} -> {target} "
                    f"=> simulated Location {simulated!r} "
                    "(loop shape — browser will request the "
                    "longer path, hit the same rule, and append "
                    "forever)"
                )
        self.assertFalse(
            bad,
            "Envoy redirect rules produce a path-prefix loop:\n  - "
            + "\n  - ".join(bad)
            + "\n\nFix: update the Lua envoy_on_response in "
              "config/defaults/compose/envoy.runtime.base.yaml "
              "so absolute /app/<svc>-prefixed Location headers "
              "aren't re-prefixed by the current request's prefix.",
        )


# Unit tests on the simulator itself — if the simulator drifts
# from the Lua, these pin the intended behaviour independently.
class LuaLocationRewriteSimulatorTests(unittest.TestCase):

    def test_absolute_different_app_prefix_not_rewritten(self) -> None:
        """The 2026-04-21 bug shape. Request hit ``/app/controller``,
        redirect target is ``/app/media-stack-controller``. The
        simulator MUST NOT prepend the request prefix."""
        self.assertEqual(
            _simulate_lua_location_rewrite(
                "/app/controller", "/app/media-stack-controller",
            ),
            "/app/media-stack-controller",
        )

    def test_relative_path_gets_prefix_prepended(self) -> None:
        """The legitimate case the Lua was written for: an
        upstream app under ``/app/jellyfin`` redirects to an
        absolute path ``/Login`` that's missing the prefix. The
        simulator prepends ``/app/jellyfin`` so the browser stays
        in the app's gateway scope."""
        self.assertEqual(
            _simulate_lua_location_rewrite(
                "/app/jellyfin/web", "/Login",
            ),
            "/app/jellyfin/Login",
        )

    def test_same_prefix_canonical_trailing_slash(self) -> None:
        """``/app/foo`` → ``/app/foo/`` (trailing-slash fixup)
        must not double-prefix."""
        self.assertEqual(
            _simulate_lua_location_rewrite(
                "/app/foo", "/app/foo/",
            ),
            "/app/foo/",
        )

    def test_request_not_under_app_prefix_passes_through(self) -> None:
        """Requests not under /app/ (e.g. /healthz) shouldn't get
        any prefix work."""
        self.assertEqual(
            _simulate_lua_location_rewrite("/healthz", "/elsewhere"),
            "/elsewhere",
        )


if __name__ == "__main__":
    unittest.main()
