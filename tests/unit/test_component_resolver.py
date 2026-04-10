"""Tests for controller_component_resolver — technology resolution, phase plan parsing."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.exceptions import ConfigError  # noqa: E402
from media_stack.cli.workflows.controller_component_resolver import (  # noqa: E402
    normalize_technology_token,
    canonicalize_technology,
    normalize_flag_token,
    _coerce_technology_list,
    _adapter_hooks,
    _phase_plan_steps,
    resolve_pipeline_phase_plan,
    ControllerPhasePlanStep,
    PhaseSkipFlagSpec,
    evaluate_phase_condition,
)


class TestNormalizeTechnologyToken(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(normalize_technology_token("Sonarr"), "sonarr")

    def test_special_chars(self):
        self.assertEqual(normalize_technology_token("my App!@#"), "my-app")

    def test_none(self):
        self.assertEqual(normalize_technology_token(None), "")

    def test_empty(self):
        self.assertEqual(normalize_technology_token(""), "")

    def test_strips_hyphens(self):
        self.assertEqual(normalize_technology_token("--test--"), "test")

    def test_numbers(self):
        self.assertEqual(normalize_technology_token("app2"), "app2")


class TestCanonicalizeTechnology(unittest.TestCase):
    def test_alias_lookup(self):
        aliases = {"qbit": "qbittorrent", "prow": "prowlarr"}
        self.assertEqual(canonicalize_technology("qbit", aliases), "qbittorrent")

    def test_no_alias(self):
        self.assertEqual(canonicalize_technology("sonarr", {}), "sonarr")

    def test_empty_returns_empty(self):
        self.assertEqual(canonicalize_technology("", {}), "")

    def test_normalizes_before_lookup(self):
        aliases = {"my-app": "myapp"}
        self.assertEqual(canonicalize_technology("My App", aliases), "myapp")


class TestNormalizeFlagToken(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(normalize_flag_token("skip_torrent"), "skip_torrent")

    def test_special_chars_to_underscore(self):
        self.assertEqual(normalize_flag_token("skip-torrent-client"), "skip_torrent_client")

    def test_none(self):
        self.assertEqual(normalize_flag_token(None), "")


class TestCoerceTechnologyList(unittest.TestCase):
    def test_basic_list(self):
        result = _coerce_technology_list(["sonarr", "radarr"], {})
        self.assertEqual(result, ("sonarr", "radarr"))

    def test_deduplication(self):
        result = _coerce_technology_list(["sonarr", "sonarr"], {})
        self.assertEqual(result, ("sonarr",))

    def test_alias_resolution(self):
        result = _coerce_technology_list(["qbit"], {"qbit": "qbittorrent"})
        self.assertEqual(result, ("qbittorrent",))

    def test_not_list_returns_empty(self):
        self.assertEqual(_coerce_technology_list("string", {}), ())

    def test_none_returns_empty(self):
        self.assertEqual(_coerce_technology_list(None, {}), ())


class TestAdapterHooks(unittest.TestCase):
    def test_returns_hooks_dict(self):
        cfg = {"adapter_hooks": {"bootstrap_all": {"phase_plan": []}}}
        result = _adapter_hooks(cfg)
        self.assertIn("bootstrap_all", result)

    def test_missing_hooks(self):
        self.assertEqual(_adapter_hooks({}), {})

    def test_non_dict_hooks(self):
        self.assertEqual(_adapter_hooks({"adapter_hooks": "string"}), {})


class TestPhasePlanSteps(unittest.TestCase):
    def test_string_step(self):
        result = _phase_plan_steps(["run"], pipeline="test")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].operation, "run")

    def test_dict_step(self):
        result = _phase_plan_steps([{
            "operation": "run",
            "phase_name": "test phase",
            "params": {"action": "script"},
        }], pipeline="test")
        self.assertEqual(result[0].phase_name, "test phase")
        self.assertEqual(result[0].params["action"], "script")

    def test_empty_operation_raises(self):
        with self.assertRaises(ConfigError):
            _phase_plan_steps([{"operation": ""}], pipeline="test")

    def test_invalid_type_raises(self):
        with self.assertRaises(ConfigError):
            _phase_plan_steps([123], pipeline="test")

    def test_not_list_returns_empty(self):
        self.assertEqual(_phase_plan_steps("not a list", pipeline="test"), ())


class TestResolvePipelinePhasePlan(unittest.TestCase):
    def test_valid_plan(self):
        cfg = {"adapter_hooks": {"bootstrap_all": {"phase_plan": [
            {"operation": "run", "params": {"action": "script", "script": "test"}, "phase_name": "test"},
        ]}}}
        result = resolve_pipeline_phase_plan(cfg, pipeline="bootstrap_all")
        self.assertEqual(len(result), 1)

    def test_missing_pipeline_raises(self):
        with self.assertRaises(ConfigError):
            resolve_pipeline_phase_plan({}, pipeline="nonexistent")

    def test_allow_empty(self):
        result = resolve_pipeline_phase_plan({}, pipeline="nonexistent", allow_empty=True)
        self.assertEqual(result, ())


class TestEvaluatePhaseCondition(unittest.TestCase):
    def test_true_bool(self):
        self.assertTrue(evaluate_phase_condition(True, context={}))

    def test_false_bool(self):
        self.assertFalse(evaluate_phase_condition(False, context={}))

    def test_none_is_true(self):
        self.assertTrue(evaluate_phase_condition(None, context={}))


class TestControllerPhasePlanStep(unittest.TestCase):
    def test_defaults(self):
        step = ControllerPhasePlanStep(operation="run")
        self.assertEqual(step.operation, "run")
        self.assertTrue(step.enabled)
        self.assertEqual(step.params, {})
        self.assertEqual(step.skip_flag, "")


if __name__ == "__main__":
    unittest.main()
