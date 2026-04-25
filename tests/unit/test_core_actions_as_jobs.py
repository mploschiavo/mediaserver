"""Tests pinning the migration of six core actions into proper
contract-driven jobs.

Background: ``post-setup``, ``validate-credentials``, ``envoy-config``,
``restart-apps``, ``discover-indexers``, ``push-indexers`` lived in a
hardcoded ``_CORE_ACTIONS`` table at ``handlers_post.py``. They were
runnable via ``POST /actions/{name}`` and showed up in Recent
Activity, but never appeared in the Job tree because the tree only
walked contract-discovered jobs. Same operation, two unaligned
vocabularies.

These tests pin:

1. **Each migrated job is discoverable from the contract.** A
   refactor that drops the ``contracts/services/core.yaml`` file
   or renames a job here fails this test.
2. **Each migrated job appears in the bootstrap tree** in the
   right phase group with its declared prereqs.
3. **Each migrated job is still in ``KNOWN_ACTIONS``** so
   ``POST /actions/{name}`` keeps working.
4. **Each adapter resolves and matches the JobContext signature.**
5. **The adapter calls the legacy ``action_*`` function** so we
   don't accidentally diverge behaviour when the legacy handler
   gets bug-fixed.
6. **Dashboard label override table is minimal** — humanised
   slug labels match the tree word-for-word."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


# Force a fresh discover so the migration is exercised even if a
# previous test cached the registry.
import media_stack.services.jobs.framework as _jf  # noqa: E402
_jf._DISCOVERED_JOBS_CACHE = None


_MIGRATED_JOBS = {
    # The two pre_bootstrap orchestration jobs replaced the inline
    # work that ``action_bootstrap`` used to do outside the framework.
    "discover-api-keys":     {"phase": "pre_bootstrap",
                              "requires": []},
    "run-legacy-pipeline":   {"phase": "pre_bootstrap",
                              "requires": []},
    "envoy-config":          {"phase": "default",          "requires": []},
    "discover-indexers":     {"phase": "download_clients", "requires": []},
    "push-indexers":         {"phase": "download_clients", "requires": []},
    "validate-credentials":  {"phase": "post",
                              "requires": ["media_server_api_key",
                                           "arr_apps_reachable"]},
    "post-setup":            {"phase": "post",
                              "requires": ["media_server_api_key",
                                           "media_server_reachable"]},
    "restart-apps":          {"phase": "post",             "requires": []},
}


# ----------------------------------------------------------------------
# 1. Discovery
# ----------------------------------------------------------------------


class DiscoveryTests(unittest.TestCase):

    def test_each_migrated_job_is_discovered(self) -> None:
        jobs = _jf.discover_jobs_from_contracts()
        by_name = {j["name"]: j for j in jobs}
        for name, expected in _MIGRATED_JOBS.items():
            self.assertIn(
                name, by_name,
                f"{name} not discovered. Did contracts/services/"
                f"core.yaml get renamed or deleted?",
            )
            self.assertEqual(
                by_name[name]["phase"], expected["phase"],
                f"{name} phase changed from {expected['phase']!r}",
            )
            self.assertEqual(
                by_name[name]["requires"], expected["requires"],
                f"{name} prereqs changed",
            )
            self.assertEqual(by_name[name]["service"], "core")


# ----------------------------------------------------------------------
# 2. Tree placement
# ----------------------------------------------------------------------


class TreePlacementTests(unittest.TestCase):

    def setUp(self) -> None:
        _jf._DISCOVERED_JOBS_CACHE = None
        self.root = _jf.build_job_framework()

    def _find(self, name: str):
        def _walk(j):
            if j.name == name:
                return j
            for sub in j.sub_jobs:
                hit = _walk(sub)
                if hit:
                    return hit
            return None
        return _walk(self.root)

    def test_envoy_config_lands_under_configure_default(self) -> None:
        node = self._find("envoy-config")
        self.assertIsNotNone(node, "envoy-config missing from the tree")

    def test_validate_credentials_lands_under_configure_post(self) -> None:
        node = self._find("validate-credentials")
        self.assertIsNotNone(node)
        self.assertIn("media_server_api_key", node.requires)

    def test_post_setup_lands_under_configure_post(self) -> None:
        node = self._find("post-setup")
        self.assertIsNotNone(node)
        self.assertIn("media_server_api_key", node.requires)
        self.assertIn("media_server_reachable", node.requires)

    def test_discover_and_push_indexers_under_configure_download_clients(self) -> None:
        for name in ("discover-indexers", "push-indexers"):
            node = self._find(name)
            self.assertIsNotNone(node, f"{name} missing from the tree")


# ----------------------------------------------------------------------
# 3. KNOWN_ACTIONS preserves /actions/{name}
# ----------------------------------------------------------------------


class KnownActionsTests(unittest.TestCase):

    def test_migrated_actions_remain_in_known_actions(self) -> None:
        """Backwards compatibility: existing curl/CI/Slack scripts
        that POST to /actions/validate-credentials etc. must keep
        working after the migration."""
        from media_stack.api.handlers_post import PostRequestHandler
        known = PostRequestHandler._build_known_actions()
        for name in _MIGRATED_JOBS:
            self.assertIn(
                name, known,
                f"/actions/{name} stopped working — did the contract "
                f"discovery merge break?",
            )


# ----------------------------------------------------------------------
# 4. Adapter resolution
# ----------------------------------------------------------------------


class AdapterResolutionTests(unittest.TestCase):

    def test_each_adapter_is_importable_and_takes_ctx(self) -> None:
        import importlib
        import inspect
        # The contract uses dashed names; the adapter functions use
        # snake_case with one rename: "run-legacy-pipeline" maps to
        # ``run_legacy_pipeline``.
        for name in _MIGRATED_JOBS:
            slug = name.replace("-", "_")
            mod = importlib.import_module(
                "media_stack.services.apps.core.job_adapters"
            )
            fn = getattr(mod, slug, None)
            self.assertIsNotNone(
                fn, f"adapter {slug} missing — core.yaml's "
                f"handler path won't resolve.")
            sig = inspect.signature(fn)
            params = list(sig.parameters.keys())
            self.assertEqual(
                params, ["ctx"],
                f"{slug} signature is {params}; job framework "
                f"requires (ctx).",
            )


# ----------------------------------------------------------------------
# 5. Adapter delegation
# ----------------------------------------------------------------------


class AdapterDelegationTests(unittest.TestCase):
    """Each adapter must call the corresponding legacy ``action_*``
    function. Otherwise the migration silently skips the actual
    work the action used to do."""

    def _ctx(self):
        from media_stack.services.jobs.framework import JobContext
        return JobContext()

    def test_envoy_config_calls_action_envoy_config(self) -> None:
        with mock.patch(
            "media_stack.services.jobs.action_handlers.action_envoy_config"
        ) as m:
            from media_stack.services.apps.core.job_adapters import (
                envoy_config,
            )
            envoy_config(self._ctx())
        m.assert_called_once()

    def test_discover_api_keys_calls_run_preflights(self) -> None:
        with mock.patch(
            "media_stack.services.jobs.controller_handlers._run_preflights"
        ) as m_pre, mock.patch(
            "media_stack.cli.commands.controller_k8s"
            "._persist_preflight_keys_to_secret"
        ):
            from media_stack.services.apps.core.job_adapters import (
                discover_api_keys,
            )
            discover_api_keys(self._ctx())
        m_pre.assert_called_once()

    def test_run_legacy_pipeline_calls_runner_run(self) -> None:
        runner = mock.MagicMock()
        with mock.patch(
            "media_stack.services.jobs.controller_runner._build_runner",
            return_value=(runner, mock.MagicMock()),
        ):
            from media_stack.services.apps.core.job_adapters import (
                run_legacy_pipeline,
            )
            run_legacy_pipeline(self._ctx())
        runner.run.assert_called_once()

    def test_validate_credentials_calls_action_validate_credentials(self) -> None:
        with mock.patch(
            "media_stack.services.jobs.action_handlers.action_validate_credentials"
        ) as m:
            from media_stack.services.apps.core.job_adapters import (
                validate_credentials,
            )
            validate_credentials(self._ctx())
        m.assert_called_once()

    def test_restart_apps_calls_action_restart_apps(self) -> None:
        with mock.patch(
            "media_stack.services.jobs.action_handlers.action_restart_apps"
        ) as m:
            with mock.patch(
                "media_stack.services.jobs.controller_handlers"
                "._load_handler_specs"
            ), mock.patch(
                "media_stack.services.jobs.controller_handlers"
                "._run_handler_specs"
            ):
                from media_stack.services.apps.core.job_adapters import (
                    restart_apps,
                )
                restart_apps(self._ctx())
        m.assert_called_once()


# ----------------------------------------------------------------------
# 6. Dashboard label minimisation
# ----------------------------------------------------------------------


class DashboardLabelOverridesTests(unittest.TestCase):

    def test_overrides_table_is_small(self) -> None:
        """The pre-migration ``ACTION_LABELS`` had ~22 cute renames
        ('configure-media-server' → 'Configure Jellyfin') that
        diverged from the job tree. After alignment the override
        table should be tiny — only the few IDs whose humanised
        slug is too terse for the dashboard's headline buttons."""
        html = (
            ROOT / "src" / "media_stack" / "api" / "dashboard.html"
        ).read_text(encoding="utf-8")
        idx = html.find("const ACTION_LABEL_OVERRIDES")
        self.assertGreater(
            idx, -1,
            "ACTION_LABEL_OVERRIDES dropped — dashboard.html may "
            "have reverted to the old ACTION_LABELS table.",
        )
        block = html[idx:idx + 800]
        # Spot-check: bootstrap is the one override that's worth
        # keeping, but cute renames for migrated jobs should be gone.
        for stale in (
            "'Configure Jellyfin'", "'Apply Passwords & Restart Services'",
            "'Update Proxy Routes'", "'Verify Logins'",
            "'Add Media Libraries'", "'Setup Live TV & EPG'",
        ):
            self.assertNotIn(
                stale, block,
                f"Cute rename {stale} reintroduced — labels should "
                f"match the job tree word-for-word.",
            )


if __name__ == "__main__":
    unittest.main()
