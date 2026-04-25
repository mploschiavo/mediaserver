"""Smoke tests for session_aggregator_singletons wiring.

Verifies the three security/providers session providers are
constructed and registered with the aggregator when the relevant
env vars are present, and that none of them block construction
when their backends are unreachable (the controller must still
boot in degraded environments).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class _FakeHttp:
    """HttpClient stand-in: every request raises (probe failure path).

    Zero-arg ``__init__`` so ``HttpClient()`` patched inside a
    provider module instantiates this directly.
    """

    def __init__(self, *_a, **_kw):
        pass

    def request(self, *_a, **_kw):
        raise RuntimeError("test stub: backends unreachable")


class _SilentHttp:
    """HttpClient stand-in: returns (200, [], '') so probes succeed."""

    def __init__(self, *_a, **_kw):
        pass

    def request(self, *_a, **_kw):
        return (200, [], "")


class SessionAggregatorSingletonsTests(unittest.TestCase):
    """The wiring layer must register all 3 providers under env."""

    def setUp(self):
        # Reset module-level state between tests.
        from media_stack.api import session_aggregator_singletons as mod
        mod.reset()
        self._mod = mod

    def tearDown(self):
        self._mod.reset()

    def test_build_security_session_providers_returns_three_when_env_set(
        self,
    ):
        env = {
            "AUTHELIA_URL": "http://authelia:9091",
            "JELLYFIN_URL": "http://jellyfin:8096",
            "JELLYFIN_API_KEY": "jf-key",
            "JELLYSEERR_URL": "http://jellyseerr:5055",
            "JELLYSEERR_API_KEY": "js-key",
        }
        with mock.patch.dict("os.environ", env, clear=False), \
             mock.patch(
                 "media_stack.services.security.providers."
                 "authelia_session_provider.HttpClient",
                 _FakeHttp,
             ), \
             mock.patch(
                 "media_stack.services.security.providers."
                 "jellyfin_session_provider.HttpClient",
                 _FakeHttp,
             ), \
             mock.patch(
                 "media_stack.services.security.providers."
                 "jellyseerr_session_provider.HttpClient",
                 _FakeHttp,
             ):
            providers = self._mod._build_security_session_providers()
        names = sorted(p.name for p in providers)
        self.assertEqual(names, ["authelia", "jellyfin", "jellyseerr"])

    def test_build_skips_jellyfin_jellyseerr_without_api_keys(self):
        # Only AUTHELIA_URL is set — Jellyfin and Jellyseerr return
        # None from their from_env factories.
        env = {"AUTHELIA_URL": "http://authelia:9091"}
        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch(
                 "media_stack.services.security.providers."
                 "authelia_session_provider.HttpClient",
                 _FakeHttp,
             ):
            providers = self._mod._build_security_session_providers()
        names = sorted(p.name for p in providers)
        self.assertEqual(names, ["authelia"])

    def test_build_session_aggregator_does_not_raise_with_no_env(self):
        # Reduced-footprint deploy: no env vars, backends unreachable.
        # Must not blow up controller boot.
        with mock.patch.dict("os.environ", {}, clear=True):
            agg = self._mod._build_session_aggregator()
        self.assertIsNotNone(agg)

    def test_known_usernames_from_user_store_returns_list(self):
        # Even if the user store path isn't writable, this must
        # return a list (possibly empty), never raise.
        with mock.patch.dict(
            "os.environ", {"CONFIG_ROOT": "/nonexistent"}, clear=False,
        ):
            out = self._mod._known_usernames_from_user_store()
        self.assertIsInstance(out, list)

    def test_aggregator_dedupes_provider_names_extras_win(self):
        """Extras (security/providers) shadow base providers by name.

        The legacy ``UserProvider`` instances also satisfy the
        ``SessionAdminProvider`` runtime check; the wiring layer
        must replace them with the username-keyed implementations.
        """

        class _LegacyJellyfin:
            name = "jellyfin"

            def list_sessions(self, _u):
                return []

            def revoke_sessions(self, _u):
                return None

            def revoke_session(self, _u, _s):
                return None

        class _NewJellyfin(_LegacyJellyfin):
            pass

        new_p = _NewJellyfin()
        with mock.patch.object(
            self._mod, "_optional_user_service_parts",
            return_value=([_LegacyJellyfin()], None),
        ), mock.patch.object(
            self._mod, "_build_security_session_providers",
            return_value=[new_p],
        ):
            agg = self._mod._build_session_aggregator()
        self.assertIs(agg._providers["jellyfin"], new_p)


if __name__ == "__main__":
    unittest.main()
