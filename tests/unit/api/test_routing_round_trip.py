"""Round-trip tests for POST /api/routing → overrides file → GET reflects.

Covers the write → read → assert pattern for routing config so a
future refactor can't silently break the fundamental flow: admin
edits routing in the UI, saves, reloads the Routing tab, sees the
new value. The failure mode this guards against is "POST returned
200 but the file on disk was never updated" — same shape as the
CSRF-never-written bug.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config._profile import ProfileService  # noqa: E402
from media_stack.api.services.config._routing import (  # noqa: E402
    RoutingConfigService,
)


class _FakeProfile:
    """Minimal ProfileService stand-in backed by a tempfile."""

    def __init__(self, profile_path: Path):
        self._path = profile_path

    def load(self):
        if not self._path.is_file():
            return {}, None
        return yaml.safe_load(self._path.read_text()) or {}, self._path

    def media_server_id(self) -> str:
        return "jellyfin"


class RoutingEditRoundTripTests(unittest.TestCase):
    def setUp(self):
        import os
        self._orig_env = {
            k: os.environ.get(k)
            for k in ("BOOTSTRAP_PROFILE_FILE", "CONFIG_ROOT")
        }

    def tearDown(self):
        """Restore os.environ so we don't leak the per-test profile
        path into other tests in the suite. This is the 'env
        pollution' class of cross-test failure."""
        import os
        for k, v in self._orig_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _make(self, tmp: Path):
        profile_path = tmp / "profile.yaml"
        profile_path.write_text(yaml.safe_dump({
            "metadata": {"name": "media-stack"},
            "routing": {
                "base_domain": "local",
                "stack_subdomain": "media-stack",
                "gateway_host": "apps.media-stack.local",
                "gateway_port": 80,
                "app_path_prefix": "/app",
                "strategy": "hybrid",
            },
        }))
        import os
        os.environ["BOOTSTRAP_PROFILE_FILE"] = str(profile_path)
        os.environ["CONFIG_ROOT"] = str(tmp)
        svc = RoutingConfigService(_FakeProfile(profile_path))
        return svc, profile_path

    def test_write_then_read_returns_new_value(self):
        """Write via update_routing, read via get_routing — MUST match.
        Would have caught a silent write failure where POST returned
        success but the overrides file was never persisted."""
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self._make(Path(d))
            svc.update_routing({"gateway_port": 443})
            self.assertEqual(svc.get_routing()["gateway_port"], 443)

    def test_partial_update_preserves_other_fields(self):
        """Editing gateway_port must not wipe gateway_host. The
        overrides file merges into profile values — drop the merge
        and every existing value becomes its default on the first
        write."""
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self._make(Path(d))
            svc.update_routing({"gateway_port": 443})
            out = svc.get_routing()
            self.assertEqual(out["gateway_port"], 443)
            self.assertEqual(out["gateway_host"], "apps.media-stack.local")
            self.assertEqual(out["app_path_prefix"], "/app")

    def test_updating_subdomain_recomputes_gateway_host(self):
        """Changing stack_subdomain must derive a new gateway_host
        (apps.<subdomain>.<base>) UNLESS gateway_host was explicitly
        edited in the same call. A stale mismatch between the two
        breaks the browser-to-Envoy vhost lookup."""
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self._make(Path(d))
            svc.update_routing({"stack_subdomain": "other"})
            self.assertEqual(svc.get_routing()["gateway_host"],
                             "apps.other.local")

    def test_explicit_gateway_host_overrides_derivation(self):
        """When the admin types a custom gateway_host, the
        subdomain/base derivation must yield to it."""
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self._make(Path(d))
            svc.update_routing({"gateway_host": "admin.example.com"})
            out = svc.get_routing()
            self.assertEqual(out["gateway_host"], "admin.example.com")
            # Derived stack_subdomain / base_domain from the FQDN.
            self.assertEqual(out["stack_subdomain"], "example")

    def test_second_edit_reads_fresh_state_not_cache(self):
        """Two edits in a row must both land on disk. A stale cached
        copy in the service would make the second edit silently
        discard the first."""
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self._make(Path(d))
            svc.update_routing({"gateway_port": 443})
            svc.update_routing({"app_path_prefix": "/ui"})
            out = svc.get_routing()
            self.assertEqual(out["gateway_port"], 443)
            self.assertEqual(out["app_path_prefix"], "/ui")

    def test_action_trigger_fires_on_change(self):
        """Every routing mutation must queue an envoy-config action —
        without that, the stored value and the live Envoy listener
        drift apart."""
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self._make(Path(d))
            trigger = MagicMock()
            svc.update_routing({"gateway_port": 443},
                               action_trigger=trigger)
            calls = [c[0][0] for c in trigger.call_args_list]
            self.assertIn("envoy-config", calls,
                          "routing edit didn't queue envoy-config; "
                          "envoy.yaml will be stale until a manual "
                          "regen is triggered.")

    def test_no_change_does_not_fire_trigger(self):
        """Idempotent edits should not spam the action queue — an
        admin clicking Save on an unchanged form shouldn't kick off
        a redundant Envoy regen."""
        with tempfile.TemporaryDirectory() as d:
            svc, _ = self._make(Path(d))
            trigger = MagicMock()
            result = svc.update_routing(
                {"gateway_port": 80},  # unchanged
                action_trigger=trigger)
            self.assertEqual(result.get("status"), "no_changes")
            trigger.assert_not_called()


if __name__ == "__main__":
    unittest.main()
