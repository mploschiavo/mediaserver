"""Unit tests for CLI command modules with zero prior coverage.

Covers: deploy_config_resolver, validate_controller_config_main,
        validate_controller_profile_main, deploy_verify_main,
        set_pvc_storage_class_main, generate_envoy_config_main, maintenance.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


# ===================================================================
# DeployHookConfigResolverService tests
#
# Replaces the pre-2026-05-11 ``deploy_config_resolver`` tests
# (ADR-0015 Phase 3 deleted that wrapper module). The wrapper
# implemented its own legacy semantics for ``edge.provider`` +
# ``EDGE_ROUTER_PROVIDER`` env fallback that were already
# duplicated by ``parse_deploy_stack_config`` in
# ``workflows/deploy_cli_config_service.py:392-394`` reading
# ``EDGE_ROUTER_PROVIDER`` into ``DeployStackConfig.edge_router_provider``.
# The tests below cover the canonical workflows-tier resolver
# instead. The ``DeployConfigService.edge_router_provider()`` path
# is exercised end-to-end via ``test_deploy_stack_main.py``.
# ===================================================================

from media_stack.cli.workflows.deploy_hook_config_resolver import (
    DeployHookConfigResolverService,
)


class TestWorkflowsEdgeRouterProvider(unittest.TestCase):
    """Direct tests for the workflows-tier hook resolver."""

    def setUp(self) -> None:
        self._resolver = DeployHookConfigResolverService()

    def test_returns_router_provider_from_edge_cfg(self) -> None:
        # Note: the workflows resolver takes the unwrapped ``edge``
        # subtree (not the outer adapter_hooks dict) and reads
        # ``router_provider`` (canonical key name post-ADR-0001).
        edge_cfg = {"router_provider": "traefik"}
        self.assertEqual(
            self._resolver.edge_router_provider(edge_cfg), "traefik",
        )

    def test_strips_and_lowercases(self) -> None:
        self.assertEqual(
            self._resolver.edge_router_provider({"router_provider": "  Envoy  "}),
            "envoy",
        )

    def test_returns_empty_when_missing(self) -> None:
        # The workflows resolver intentionally returns "" on missing
        # config — DeployConfigService.edge_router_provider() layers
        # the cfg-overlay + EDGE_ROUTER_PROVIDER env fallback on top.
        self.assertEqual(self._resolver.edge_router_provider({}), "")


class TestWorkflowsIngressClassPriority(unittest.TestCase):
    """Direct tests for the workflows-tier hook resolver."""

    def setUp(self) -> None:
        self._resolver = DeployHookConfigResolverService()

    def test_returns_tuple_from_edge_cfg(self) -> None:
        edge_cfg = {"ingress_class_priority": ["traefik", "nginx"]}
        self.assertEqual(
            self._resolver.ingress_class_priority(edge_cfg),
            ("traefik", "nginx"),
        )

    def test_returns_empty_tuple_when_missing(self) -> None:
        # Defaults live in the catalog YAML / DeployStackConfig, not
        # the hook resolver. ADR-0015 Phase 3 separated these.
        self.assertEqual(self._resolver.ingress_class_priority({}), ())

    def test_filters_empty_strings(self) -> None:
        edge_cfg = {"ingress_class_priority": ["nginx", "", "  ", "traefik"]}
        self.assertEqual(
            self._resolver.ingress_class_priority(edge_cfg),
            ("nginx", "traefik"),
        )


# ===================================================================
# validate_controller_config_main tests
# ===================================================================

from media_stack.cli.commands.validate_controller_config_main import (
    basic_checks,
    format_path,
    _validate_media_server_operation_plans,
)


class TestFormatPath(unittest.TestCase):
    """Tests for validate_controller_config_main.format_path."""

    def test_empty_path(self):
        self.assertEqual(format_path([]), "$")

    def test_string_parts(self):
        self.assertEqual(format_path(["foo", "bar"]), "$.foo.bar")

    def test_integer_parts(self):
        self.assertEqual(format_path([0, 1]), "$[0][1]")

    def test_mixed_parts(self):
        self.assertEqual(format_path(["items", 0, "name"]), "$.items[0].name")


class TestBasicChecks(unittest.TestCase):
    """Tests for validate_controller_config_main.basic_checks."""

    def test_non_dict_root(self):
        errors = basic_checks("not-a-dict")
        self.assertEqual(errors, ["$: config root must be an object"])

    def test_missing_technology_bindings(self):
        errors = basic_checks({})
        self.assertIn("$: missing required key 'technology_bindings'", errors)

    def test_invalid_config_version_type(self):
        cfg = {"technology_bindings": {"media_server": "jellyfin"}, "config_version": "abc"}
        errors = basic_checks(cfg)
        self.assertIn("$.config_version: must be an integer", errors)

    def test_unsupported_config_version(self):
        cfg = {"technology_bindings": {"media_server": "jellyfin"}, "config_version": 99}
        errors = basic_checks(cfg)
        self.assertIn("$.config_version: unsupported version (expected 2)", errors)

    def test_download_clients_must_be_object(self):
        cfg = {"technology_bindings": {"media_server": "jellyfin"}, "download_clients": "bad"}
        errors = basic_checks(cfg)
        self.assertIn("$.download_clients: must be an object", errors)

    def test_missing_media_server_binding(self):
        cfg = {"technology_bindings": {}}
        errors = basic_checks(cfg)
        self.assertIn("$.technology_bindings.media_server: required non-empty string", errors)

    def test_valid_minimal_config(self):
        cfg = {"technology_bindings": {"media_server": "jellyfin"}}
        errors = basic_checks(cfg)
        self.assertEqual(errors, [])


class TestValidateMediaServerOperationPlans(unittest.TestCase):
    """Tests for validate_controller_config_main._validate_media_server_operation_plans."""

    def test_none_plans_no_errors(self):
        errors = []
        _validate_media_server_operation_plans(None, "$.test", errors)
        self.assertEqual(errors, [])

    def test_non_dict_plans_adds_error(self):
        errors = []
        _validate_media_server_operation_plans("bad", "$.test", errors)
        self.assertIn("$.test: must be an object", errors)

    def test_non_dict_backend_adds_error(self):
        errors = []
        _validate_media_server_operation_plans({"jellyfin": "bad"}, "$.test", errors)
        self.assertIn("$.test.jellyfin: must be an object", errors)


# ===================================================================
# set_pvc_storage_class_main tests
# ===================================================================

from media_stack.cli.commands.set_pvc_storage_class_main import (
    _indent_width,
    _is_pvc_document,
    _find_spec_index,
    _remove_storage_class,
    split_yaml_documents,
    render_yaml_documents,
    transform_storage_class_manifest,
    SetStorageClassConfig,
)


class TestIndentWidth(unittest.TestCase):
    def test_no_indent(self):
        self.assertEqual(_indent_width("hello"), 0)

    def test_some_indent(self):
        self.assertEqual(_indent_width("  hello"), 2)


class TestIsPvcDocument(unittest.TestCase):
    def test_pvc_document(self):
        lines = ["apiVersion: v1", "kind: PersistentVolumeClaim", "metadata:"]
        self.assertTrue(_is_pvc_document(lines))

    def test_non_pvc_document(self):
        lines = ["apiVersion: v1", "kind: Service", "metadata:"]
        self.assertFalse(_is_pvc_document(lines))


class TestFindSpecIndex(unittest.TestCase):
    def test_finds_spec(self):
        lines = ["metadata:", "  name: test", "spec:", "  accessModes:"]
        self.assertEqual(_find_spec_index(lines), 2)

    def test_no_spec(self):
        lines = ["metadata:", "  name: test"]
        self.assertEqual(_find_spec_index(lines), -1)


class TestRemoveStorageClass(unittest.TestCase):
    def test_removes_storage_class_line(self):
        lines = ["spec:", "  storageClassName: fast", "  accessModes:"]
        result = _remove_storage_class(lines)
        self.assertEqual(result, ["spec:", "  accessModes:"])


class TestSplitYamlDocuments(unittest.TestCase):
    def test_single_document(self):
        result = split_yaml_documents("kind: Service\nmetadata:")
        self.assertEqual(len(result), 1)

    def test_multiple_documents(self):
        text = "kind: Service\n---\nkind: PersistentVolumeClaim"
        result = split_yaml_documents(text)
        self.assertEqual(len(result), 2)

    def test_empty_input(self):
        result = split_yaml_documents("")
        self.assertEqual(result, [""])


class TestRenderYamlDocuments(unittest.TestCase):
    def test_single_part(self):
        result = render_yaml_documents(["kind: Service"])
        self.assertEqual(result, "kind: Service\n")

    def test_multiple_parts(self):
        result = render_yaml_documents(["kind: Service", "kind: PVC"])
        self.assertEqual(result, "kind: Service\n---\nkind: PVC\n")


class TestTransformStorageClassManifest(unittest.TestCase):
    def test_set_storage_class_on_pvc(self):
        text = (
            "kind: PersistentVolumeClaim\n"
            "spec:\n"
            "  accessModes:\n"
            "    - ReadWriteOnce\n"
            "  resources:\n"
            "    requests:\n"
            "      storage: 10Gi\n"
        )
        result = transform_storage_class_manifest(text, "fast-ssd", clear_mode=False)
        self.assertIn("storageClassName: fast-ssd", result)

    def test_clear_storage_class(self):
        text = (
            "kind: PersistentVolumeClaim\n"
            "spec:\n"
            "  storageClassName: fast-ssd\n"
            "  accessModes:\n"
            "    - ReadWriteOnce\n"
        )
        result = transform_storage_class_manifest(text, "", clear_mode=True)
        self.assertNotIn("storageClassName", result)

    def test_non_pvc_document_unchanged(self):
        text = "kind: Service\nspec:\n  type: ClusterIP\n"
        result = transform_storage_class_manifest(text, "fast", clear_mode=False)
        self.assertNotIn("storageClassName", result)


# ===================================================================
# deploy_verify_main tests
# ===================================================================

from media_stack.cli.commands.deploy_verify_main import (
    build_arg_parser,
    ts as deploy_verify_ts,
)


class TestDeployVerifyArgParser(unittest.TestCase):
    """Tests for deploy_verify_main.build_arg_parser."""

    def test_default_namespace(self):
        with patch.dict(os.environ, {"NAMESPACE": "custom-ns"}, clear=False):
            parser = build_arg_parser()
            args = parser.parse_args(["10.0.0.1"])
        self.assertEqual(args.namespace, "custom-ns")

    def test_default_profile(self):
        parser = build_arg_parser()
        args = parser.parse_args(["10.0.0.1"])
        # Falls back to PROFILE env or "full"
        self.assertIn(args.profile, ("full", os.environ.get("PROFILE", "full")))

    def test_run_playwright_flag(self):
        parser = build_arg_parser()
        args = parser.parse_args(["10.0.0.1", "ns", "full", "--run-playwright"])
        self.assertTrue(args.run_playwright)


class TestDeployVerifyTs(unittest.TestCase):
    def test_returns_iso_like_string(self):
        result = deploy_verify_ts()
        self.assertIsInstance(result, str)
        self.assertIn("T", result)


# ===================================================================
# generate_envoy_config_main tests
# ===================================================================

from media_stack.services.edge.envoy_config_generator import (
    _csv,
    _load_bootstrap_edge_hooks,
    _load_profile,
)


class TestCsv(unittest.TestCase):
    """Tests for generate_envoy_config_main._csv."""

    def test_splits_comma_separated(self):
        self.assertEqual(_csv("a,b,c"), ("a", "b", "c"))

    def test_strips_whitespace(self):
        self.assertEqual(_csv(" foo , bar "), ("foo", "bar"))

    def test_filters_empty_items(self):
        self.assertEqual(_csv("a,,b,"), ("a", "b"))

    def test_empty_string(self):
        self.assertEqual(_csv(""), ())


class TestLoadBootstrapEdgeHooks(unittest.TestCase):
    def test_none_config_returns_empty(self):
        self.assertEqual(_load_bootstrap_edge_hooks(None), {})

    def test_nonexistent_file_returns_empty(self):
        self.assertEqual(_load_bootstrap_edge_hooks("/nonexistent/path.json"), {})

    def test_valid_config_returns_edge_hooks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"adapter_hooks": {"edge": {"provider": "envoy"}}}, f)
            f.flush()
            result = _load_bootstrap_edge_hooks(f.name)
        os.unlink(f.name)
        self.assertEqual(result, {"provider": "envoy"})

    def test_config_without_edge_returns_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"adapter_hooks": {}}, f)
            f.flush()
            result = _load_bootstrap_edge_hooks(f.name)
        os.unlink(f.name)
        self.assertEqual(result, {})


class TestLoadProfile(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(_load_profile(None), {})

    def test_nonexistent_returns_empty(self):
        self.assertEqual(_load_profile("/no/such/file.yaml"), {})


# ===================================================================
# maintenance tests
# ===================================================================

from media_stack.cli.commands.maintenance import take_config_snapshot, prune_stale_files


class TestTakeConfigSnapshot(unittest.TestCase):
    def test_creates_snapshot_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(config_root=tmpdir)
            take_config_snapshot(args)
            snapshot_dir = Path(tmpdir) / ".snapshots"
            self.assertTrue(snapshot_dir.exists())
            snapshots = list(snapshot_dir.glob("snapshot-*.json"))
            self.assertEqual(len(snapshots), 1)

    def test_snapshot_redacts_api_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sonarr_dir = Path(tmpdir) / "sonarr"
            sonarr_dir.mkdir()
            (sonarr_dir / "config.xml").write_text(
                "<Config><ApiKey>SECRET123</ApiKey></Config>"
            )
            args = argparse.Namespace(config_root=tmpdir)
            take_config_snapshot(args)
            snapshot_dir = Path(tmpdir) / ".snapshots"
            snapshot = list(snapshot_dir.glob("snapshot-*.json"))[0]
            content = json.loads(snapshot.read_text())
            self.assertNotIn("SECRET123", content.get("sonarr/config.xml", ""))
            self.assertIn("***", content.get("sonarr/config.xml", ""))

    def test_prunes_old_snapshots_beyond_24(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snapshot_dir = Path(tmpdir) / ".snapshots"
            snapshot_dir.mkdir(parents=True)
            # Create 30 fake snapshots
            for i in range(30):
                ts_str = f"20260101T{i:06d}"
                (snapshot_dir / f"snapshot-{ts_str}.json").write_text("{}")
            args = argparse.Namespace(config_root=tmpdir)
            take_config_snapshot(args)
            remaining = list(snapshot_dir.glob("snapshot-*.json"))
            # 24 kept + 1 new = 25 max after pruning (prunes down to 24 then writes new)
            self.assertLessEqual(len(remaining), 25)


class TestPruneStaleFiles(unittest.TestCase):
    def test_prunes_old_jellyfin_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jf_log_dir = Path(tmpdir) / "jellyfin" / "log"
            jf_log_dir.mkdir(parents=True)
            # Create 10 log files with different mtimes
            for i in range(10):
                logfile = jf_log_dir / f"log_{i}.log"
                logfile.write_text(f"log content {i}")
                os.utime(logfile, (1000000 + i * 100, 1000000 + i * 100))
            log_messages = []
            args = argparse.Namespace(config_root=tmpdir)
            prune_stale_files(args, log=lambda msg: log_messages.append(msg))
            remaining = list(jf_log_dir.glob("*.log"))
            self.assertLessEqual(len(remaining), 5)

    def test_no_crash_with_empty_config_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = argparse.Namespace(config_root=tmpdir)
            log_messages = []
            prune_stale_files(args, log=lambda msg: log_messages.append(msg))
            # Should complete without error
            self.assertIsInstance(log_messages, list)


if __name__ == "__main__":
    unittest.main()
