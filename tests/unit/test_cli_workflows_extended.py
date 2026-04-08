"""Unit tests for CLI workflow modules with zero prior coverage.

Covers: controller_component_resolver, deploy_hook_config_resolver,
        deploy_profile_defaults_service, controller_notification_service,
        run_controller_job_cli_config_service, deploy_cli_config_service,
        unit_test_runner_service, controller_manifest_service,
        controller_secret_reader_service.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


# ===================================================================
# controller_component_resolver tests
# ===================================================================

from media_stack.cli.workflows.controller_component_resolver import (
    normalize_technology_token,
    canonicalize_technology,
    normalize_flag_token,
    evaluate_phase_condition,
    _lookup_path,
    _coerce_technology_list,
    ControllerPhasePlanStep,
    _phase_plan_steps,
    resolve_role_bindings,
    resolve_technology_settings,
    resolve_pipeline_phase_plan,
    resolve_bootstrap_enable_components,
)
from media_stack.core.exceptions import ConfigError


class TestNormalizeTechnologyToken(unittest.TestCase):
    def test_basic_string(self):
        self.assertEqual(normalize_technology_token("Jellyfin"), "jellyfin")

    def test_strips_whitespace(self):
        self.assertEqual(normalize_technology_token("  sonarr  "), "sonarr")

    def test_replaces_special_chars_with_hyphen(self):
        self.assertEqual(normalize_technology_token("qBit_Torrent!"), "qbit-torrent")

    def test_empty_string(self):
        self.assertEqual(normalize_technology_token(""), "")

    def test_none_value(self):
        self.assertEqual(normalize_technology_token(None), "")


class TestCanonicalizeTechnology(unittest.TestCase):
    def test_known_alias(self):
        aliases = {"qbit": "qbittorrent", "qbittorrent": "qbittorrent"}
        self.assertEqual(canonicalize_technology("qbit", aliases), "qbittorrent")

    def test_unknown_passthrough(self):
        aliases = {"jellyfin": "jellyfin"}
        self.assertEqual(canonicalize_technology("sonarr", aliases), "sonarr")

    def test_empty_returns_empty(self):
        self.assertEqual(canonicalize_technology("", {}), "")


class TestNormalizeFlagToken(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(normalize_flag_token("skip_torrent"), "skip_torrent")

    def test_special_chars(self):
        self.assertEqual(normalize_flag_token("skip--torrent!!client"), "skip_torrent_client")

    def test_strips_underscores(self):
        self.assertEqual(normalize_flag_token("__test__"), "test")

    def test_empty(self):
        self.assertEqual(normalize_flag_token(""), "")

    def test_none(self):
        self.assertEqual(normalize_flag_token(None), "")


class TestLookupPath(unittest.TestCase):
    def test_simple_key(self):
        found, value = _lookup_path({"key": "val"}, "key")
        self.assertTrue(found)
        self.assertEqual(value, "val")

    def test_nested_key(self):
        found, value = _lookup_path({"a": {"b": {"c": 42}}}, "a.b.c")
        self.assertTrue(found)
        self.assertEqual(value, 42)

    def test_missing_key(self):
        found, value = _lookup_path({"a": 1}, "b")
        self.assertFalse(found)

    def test_empty_path(self):
        found, value = _lookup_path({"a": 1}, "")
        self.assertFalse(found)


class TestEvaluatePhaseCondition(unittest.TestCase):
    def test_none_returns_true(self):
        self.assertTrue(evaluate_phase_condition(None, context={}))

    def test_bool_true(self):
        self.assertTrue(evaluate_phase_condition(True, context={}))

    def test_bool_false(self):
        self.assertFalse(evaluate_phase_condition(False, context={}))

    def test_var_equals(self):
        ctx = {"platform": "k8s"}
        condition = {"var": "platform", "equals": "k8s"}
        self.assertTrue(evaluate_phase_condition(condition, context=ctx))

    def test_var_not_equals(self):
        ctx = {"platform": "compose"}
        condition = {"var": "platform", "not_equals": "k8s"}
        self.assertTrue(evaluate_phase_condition(condition, context=ctx))

    def test_var_in_list(self):
        ctx = {"profile": "full"}
        condition = {"var": "profile", "in": ["full", "standard"]}
        self.assertTrue(evaluate_phase_condition(condition, context=ctx))

    def test_var_not_in_list(self):
        ctx = {"profile": "minimal"}
        condition = {"var": "profile", "not_in": ["full", "standard"]}
        self.assertTrue(evaluate_phase_condition(condition, context=ctx))

    def test_all_of(self):
        ctx = {"a": True, "b": True}
        condition = {"all_of": [{"var": "a", "truthy": True}, {"var": "b", "truthy": True}]}
        self.assertTrue(evaluate_phase_condition(condition, context=ctx))

    def test_any_of(self):
        ctx = {"a": False, "b": True}
        condition = {"any_of": [{"var": "a", "truthy": True}, {"var": "b", "truthy": True}]}
        self.assertTrue(evaluate_phase_condition(condition, context=ctx))

    def test_not_condition(self):
        condition = {"not": False}
        self.assertTrue(evaluate_phase_condition(condition, context={}))

    def test_exists_true(self):
        ctx = {"key": "val"}
        condition = {"var": "key", "exists": True}
        self.assertTrue(evaluate_phase_condition(condition, context=ctx))

    def test_exists_false(self):
        condition = {"var": "missing_key", "exists": False}
        self.assertTrue(evaluate_phase_condition(condition, context={}))

    def test_list_condition_all_must_pass(self):
        condition = [True, True, True]
        self.assertTrue(evaluate_phase_condition(condition, context={}))

    def test_list_condition_one_false(self):
        condition = [True, False, True]
        self.assertFalse(evaluate_phase_condition(condition, context={}))


class TestCoerceTechnologyList(unittest.TestCase):
    def test_basic_list(self):
        aliases = {"jf": "jellyfin"}
        result = _coerce_technology_list(["jf", "sonarr"], aliases)
        self.assertEqual(result, ("jellyfin", "sonarr"))

    def test_deduplicates(self):
        aliases = {"jf": "jellyfin", "jellyfin": "jellyfin"}
        result = _coerce_technology_list(["jf", "jellyfin"], aliases)
        self.assertEqual(result, ("jellyfin",))

    def test_non_list_returns_empty(self):
        self.assertEqual(_coerce_technology_list("not-a-list", {}), ())


class TestResolveRoleBindings(unittest.TestCase):
    def test_resolves_bindings(self):
        cfg = {"technology_bindings": {"media_server": "Jellyfin", "torrent_client": "qBit"}}
        aliases = {"jellyfin": "jellyfin", "qbit": "qbittorrent"}
        result = resolve_role_bindings(cfg, aliases=aliases)
        self.assertEqual(result["media_server"], "jellyfin")
        self.assertEqual(result["torrent_client"], "qbittorrent")

    def test_missing_bindings_returns_empty(self):
        result = resolve_role_bindings({}, aliases={})
        self.assertEqual(result, {})


class TestResolveTechnologySettings(unittest.TestCase):
    def test_merges_download_client_settings(self):
        cfg = {"download_clients": {"qbittorrent": {"port": 8080}}}
        aliases = {"qbittorrent": "qbittorrent"}
        result = resolve_technology_settings(cfg, aliases=aliases)
        self.assertEqual(result["qbittorrent"]["port"], 8080)


class TestResolvePipelinePhaseplan(unittest.TestCase):
    def test_raises_when_pipeline_missing(self):
        with self.assertRaises(ConfigError):
            resolve_pipeline_phase_plan({}, pipeline="bootstrap_job")

    def test_allow_empty_returns_empty(self):
        result = resolve_pipeline_phase_plan({}, pipeline="bootstrap_job", allow_empty=True)
        self.assertEqual(result, ())

    def test_parses_string_operations(self):
        cfg = {"adapter_hooks": {"my_pipeline": {"phase_plan": ["step_one", "step_two"]}}}
        result = resolve_pipeline_phase_plan(cfg, pipeline="my_pipeline")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].operation, "step_one")
        self.assertEqual(result[1].operation, "step_two")


class TestResolveBootstrapEnableComponents(unittest.TestCase):
    def test_returns_components_from_config(self):
        cfg = {"adapter_hooks": {"bootstrap_all": {"enable_components": ["sonarr", "radarr"]}}}
        aliases = {"sonarr": "sonarr", "radarr": "radarr"}
        result = resolve_bootstrap_enable_components(cfg, aliases=aliases)
        self.assertEqual(result, ("sonarr", "radarr"))

    def test_no_bootstrap_all_returns_empty(self):
        result = resolve_bootstrap_enable_components({}, aliases={})
        self.assertEqual(result, ())


# ===================================================================
# deploy_hook_config_resolver tests
# ===================================================================

from media_stack.cli.workflows.deploy_hook_config_resolver import (
    bootstrap_job_hooks,
    edge_hooks,
    ingress_class_priority,
    edge_router_provider,
    edge_router_service_names,
    media_server_service_names,
    runtime_config_policy_handler_spec,
    runtime_config_policy_params,
    compose_passthrough_env_vars,
    auth_provider_middleware_defaults,
)


class TestBootstrapJobHooks(unittest.TestCase):
    def test_returns_bootstrap_job(self):
        cfg = {"adapter_hooks": {"bootstrap_job": {"timeout": 300}}}
        self.assertEqual(bootstrap_job_hooks(cfg), {"timeout": 300})

    def test_no_hooks_returns_empty(self):
        self.assertEqual(bootstrap_job_hooks({}), {})

    def test_non_dict_hooks_returns_empty(self):
        self.assertEqual(bootstrap_job_hooks({"adapter_hooks": "bad"}), {})


class TestEdgeHooks(unittest.TestCase):
    def test_returns_edge_config(self):
        cfg = {"adapter_hooks": {"edge": {"provider": "envoy"}}}
        self.assertEqual(edge_hooks(cfg), {"provider": "envoy"})

    def test_no_edge_returns_empty(self):
        self.assertEqual(edge_hooks({"adapter_hooks": {}}), {})


class TestIngressClassPriority(unittest.TestCase):
    def test_returns_tuple(self):
        self.assertEqual(ingress_class_priority({"ingress_class_priority": ["a", "b"]}), ("a", "b"))

    def test_non_list_returns_empty(self):
        self.assertEqual(ingress_class_priority({"ingress_class_priority": "bad"}), ())


class TestEdgeRouterProvider(unittest.TestCase):
    def test_returns_provider(self):
        self.assertEqual(edge_router_provider({"router_provider": "Envoy"}), "envoy")

    def test_empty_returns_empty(self):
        self.assertEqual(edge_router_provider({}), "")


class TestEdgeRouterServiceNames(unittest.TestCase):
    def test_returns_names(self):
        self.assertEqual(edge_router_service_names({"router_service_names": ["envoy"]}), ("envoy",))

    def test_non_list_returns_empty(self):
        self.assertEqual(edge_router_service_names({}), ())


class TestMediaServerServiceNames(unittest.TestCase):
    def test_returns_names(self):
        cfg = {"media_server_service_names": ["jellyfin", "plex"]}
        self.assertEqual(media_server_service_names(cfg), ("jellyfin", "plex"))


class TestRuntimeConfigPolicyHandlerSpec(unittest.TestCase):
    def test_returns_spec(self):
        cfg = {"runtime_config_policy_handler": "my.module:Handler"}
        self.assertEqual(runtime_config_policy_handler_spec(cfg), "my.module:Handler")

    def test_empty_returns_empty(self):
        self.assertEqual(runtime_config_policy_handler_spec({}), "")


class TestRuntimeConfigPolicyParams(unittest.TestCase):
    def test_returns_params(self):
        cfg = {"runtime_config_policy_params": {"key": "value"}}
        self.assertEqual(runtime_config_policy_params(cfg), {"key": "value"})

    def test_non_dict_returns_empty(self):
        self.assertEqual(runtime_config_policy_params({"runtime_config_policy_params": "bad"}), {})


class TestComposePassthroughEnvVars(unittest.TestCase):
    def test_returns_vars(self):
        cfg = {"compose_passthrough_env_vars": ["VAR_A", "VAR_B"]}
        self.assertEqual(compose_passthrough_env_vars(cfg), ("VAR_A", "VAR_B"))

    def test_non_list_returns_empty(self):
        self.assertEqual(compose_passthrough_env_vars({}), ())


class TestAuthProviderMiddlewareDefaults(unittest.TestCase):
    def test_returns_defaults(self):
        cfg = {"auth_provider_middleware_defaults": {"authelia": "authelia-auth"}}
        self.assertEqual(auth_provider_middleware_defaults(cfg), {"authelia": "authelia-auth"})

    def test_non_dict_returns_empty(self):
        self.assertEqual(auth_provider_middleware_defaults({}), {})


# ===================================================================
# deploy_profile_defaults_service tests
# ===================================================================

from media_stack.cli.workflows.deploy_profile_defaults_service import (
    DeployProfileDefaultsService,
    DeployProfileDefaultsResult,
)


class TestDeployProfileDefaultsService(unittest.TestCase):
    def setUp(self):
        self.svc = DeployProfileDefaultsService()

    def test_minimal_profile(self):
        result = self.svc.apply(
            profile="minimal", include_optional="", enable_components="", run_bootstrap=""
        )
        self.assertEqual(result.include_optional, "0")
        self.assertEqual(result.enable_components, "0")
        self.assertEqual(result.run_bootstrap, "1")

    def test_full_profile(self):
        result = self.svc.apply(
            profile="full", include_optional="", enable_components="", run_bootstrap=""
        )
        self.assertEqual(result.include_optional, "1")
        self.assertEqual(result.enable_components, "1")

    def test_public_demo_profile(self):
        result = self.svc.apply(
            profile="public-demo", include_optional="", enable_components="", run_bootstrap=""
        )
        self.assertEqual(result.run_bootstrap, "0")

    def test_unsupported_profile_raises(self):
        with self.assertRaises(RuntimeError):
            self.svc.apply(
                profile="nonexistent", include_optional="", enable_components="", run_bootstrap=""
            )

    def test_explicit_overrides_preserved(self):
        result = self.svc.apply(
            profile="minimal", include_optional="1", enable_components="1", run_bootstrap="0"
        )
        self.assertEqual(result.include_optional, "1")
        self.assertEqual(result.enable_components, "1")
        self.assertEqual(result.run_bootstrap, "0")


# ===================================================================
# controller_notification_service tests
# ===================================================================

from media_stack.cli.workflows.controller_notification_service import (
    ControllerNotificationConfig,
    ControllerNotificationService,
)


class TestControllerNotificationService(unittest.TestCase):
    def test_notify_does_nothing_when_no_url(self):
        svc = ControllerNotificationService(cfg=ControllerNotificationConfig(alert_webhook_url=""))
        # Should not raise
        svc.notify("ok", "test message")

    @patch("urllib.request.urlopen")
    def test_notify_sends_json_payload(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock()
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)
        svc = ControllerNotificationService(
            cfg=ControllerNotificationConfig(alert_webhook_url="https://example.com/hook")
        )
        svc.notify("ok", "deploy complete")
        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        self.assertEqual(request.method, "POST")
        payload = json.loads(request.data)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["message"], "deploy complete")


# ===================================================================
# run_controller_job_cli_config_service tests
# ===================================================================

from media_stack.cli.workflows.run_controller_job_cli_config_service import (
    env_bool,
    env_bool_candidates,
    RunBootstrapJobConfig,
)


class TestEnvBool(unittest.TestCase):
    def test_true_values(self):
        for val in ("1", "true", "yes", "on", "True", "YES"):
            with patch.dict(os.environ, {"TEST_VAR": val}):
                self.assertTrue(env_bool("TEST_VAR"))

    def test_false_values(self):
        for val in ("0", "false", "no", "off", ""):
            with patch.dict(os.environ, {"TEST_VAR": val}):
                self.assertFalse(env_bool("TEST_VAR"))

    def test_missing_uses_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(env_bool("MISSING_VAR"))
            self.assertTrue(env_bool("MISSING_VAR", default=True))


class TestEnvBoolCandidates(unittest.TestCase):
    def test_first_found_wins(self):
        with patch.dict(os.environ, {"A": "1", "B": "0"}):
            self.assertTrue(env_bool_candidates(("A", "B")))

    def test_skips_missing(self):
        with patch.dict(os.environ, {"B": "1"}, clear=True):
            self.assertTrue(env_bool_candidates(("MISSING", "B")))


class TestRunBootstrapJobConfigTimeout(unittest.TestCase):
    def _make_config(self, timeout_raw):
        return RunBootstrapJobConfig(
            namespace="test",
            timeout_raw=timeout_raw,
            heartbeat_interval=15,
            job_log_tail_lines=120,
            alert_webhook_url="",
            prepare_host_root="/srv",
            ingress_name="test",
            bootstrap_runner_image="img",
            root_dir=Path("/tmp"),
            config_file=Path("/tmp/config.json"),
        )

    def test_minutes_suffix(self):
        cfg = self._make_config("10m")
        self.assertEqual(cfg.timeout_seconds, 600)

    def test_seconds_suffix(self):
        cfg = self._make_config("300s")
        self.assertEqual(cfg.timeout_seconds, 300)

    def test_hours_suffix(self):
        cfg = self._make_config("1h")
        self.assertEqual(cfg.timeout_seconds, 3600)

    def test_no_suffix_treated_as_minutes(self):
        cfg = self._make_config("5")
        self.assertEqual(cfg.timeout_seconds, 300)

    def test_invalid_format_returns_default(self):
        cfg = self._make_config("abc")
        self.assertEqual(cfg.timeout_seconds, 600)


# ===================================================================
# deploy_cli_config_service helpers tests
# ===================================================================

from media_stack.cli.workflows.deploy_cli_config_service import (
    _pick,
    _normalize_path_prefix,
    _env_value,
)


class TestPick(unittest.TestCase):
    def test_returns_first_non_empty(self):
        self.assertEqual(_pick(None, "", "hello"), "hello")

    def test_returns_default(self):
        self.assertEqual(_pick(None, None, default="fallback"), "fallback")

    def test_first_value_wins(self):
        self.assertEqual(_pick("a", "b"), "a")


class TestNormalizePathPrefix(unittest.TestCase):
    def test_adds_leading_slash(self):
        self.assertEqual(_normalize_path_prefix("app"), "/app")

    def test_strips_trailing_slash(self):
        self.assertEqual(_normalize_path_prefix("/app/"), "/app")

    def test_empty_returns_default(self):
        self.assertEqual(_normalize_path_prefix(""), "/app")

    def test_preserves_valid_prefix(self):
        self.assertEqual(_normalize_path_prefix("/custom"), "/custom")


class TestEnvValue(unittest.TestCase):
    def test_returns_none_when_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_env_value("NONEXISTENT_VAR"))

    def test_returns_none_for_empty_string(self):
        with patch.dict(os.environ, {"EMPTY_VAR": "  "}):
            self.assertIsNone(_env_value("EMPTY_VAR"))

    def test_returns_stripped_value(self):
        with patch.dict(os.environ, {"MY_VAR": "  hello  "}):
            self.assertEqual(_env_value("MY_VAR"), "hello")


# ===================================================================
# unit_test_runner_service tests
# ===================================================================

from media_stack.cli.workflows.unit_test_runner_service import (
    UnitTestRunnerConfig,
    UnitTestTelemetryRecord,
    _format_mib,
    _memory_key,
)


class TestFormatMib(unittest.TestCase):
    def test_none_returns_na(self):
        self.assertEqual(_format_mib(None), "n/a")

    def test_zero(self):
        self.assertEqual(_format_mib(0), "0.0")

    def test_positive_value(self):
        self.assertEqual(_format_mib(1024), "1.0")


class TestMemoryKey(unittest.TestCase):
    def test_with_values(self):
        record = UnitTestTelemetryRecord(
            test_id="test1", status="ok", elapsed_seconds=1.0,
            rss_start_kib=100, rss_end_kib=200, rss_delta_kib=100,
            peak_rss_kib=300, peak_rss_delta_kib=50,
        )
        self.assertEqual(_memory_key(record), (50, 100))

    def test_with_none_values(self):
        record = UnitTestTelemetryRecord(
            test_id="test1", status="ok", elapsed_seconds=1.0,
            rss_start_kib=None, rss_end_kib=None, rss_delta_kib=None,
            peak_rss_kib=None, peak_rss_delta_kib=None,
        )
        self.assertEqual(_memory_key(record), (-1, -1))


if __name__ == "__main__":
    unittest.main()
