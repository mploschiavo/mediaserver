import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.runner_phase_plan_service import run_phase_plan  # noqa: E402


class RunnerPhasePlanServiceTests(unittest.TestCase):
    def _runtime(self, **overrides):
        data = {
            "cfg": {"k": "v"},
            "config_root": "/srv-config",
            "wait_timeout": 30,
            "arr_apps_raw": [],
            "app_keys": {},
            "qbit_cfg": {},
            "app_auth_cfg": {"enabled": True},
            "qb_user": "user",
            "qb_pass": "pass",
            "prowlarr_url": "http://prowlarr:9696",
            "prowlarr_key": "key",
            "prowlarr_indexers": [],
            "auto_indexers": False,
            "trigger_sync": False,
        }
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_enabled_when_attr_skips_step_when_empty(self):
        runtime = self._runtime(prowlarr_url="")
        invoke_operation = mock.Mock()
        run_optional_step = mock.Mock()

        ran = run_phase_plan(
            runtime=runtime,
            plan_cfg={
                "precheck_steps": {
                    "steps": [
                        {
                            "operation": "ensure_prowlarr_ready",
                            "args": [
                                "cfg",
                                "prowlarr_url",
                                "prowlarr_key",
                                "app_auth_cfg",
                                "wait_timeout",
                            ],
                            "enabled_when_attr": "prowlarr_url",
                        }
                    ]
                }
            },
            phase_name="precheck_steps",
            invoke_operation=invoke_operation,
            run_optional_step=run_optional_step,
            log=mock.Mock(),
        )

        self.assertTrue(ran)
        invoke_operation.assert_not_called()
        run_optional_step.assert_not_called()

    def test_resolves_app_auth_token_for_step_args(self):
        runtime = self._runtime()
        invoke_operation = mock.Mock()

        run_phase_plan(
            runtime=runtime,
            plan_cfg={
                "precheck_steps": {
                    "steps": [
                        {
                            "operation": "ensure_prowlarr_ready",
                            "args": [
                                "cfg",
                                "prowlarr_url",
                                "prowlarr_key",
                                "app_auth_cfg",
                                "wait_timeout",
                            ],
                            "enabled_when_attr": "prowlarr_url",
                        }
                    ]
                }
            },
            phase_name="precheck_steps",
            invoke_operation=invoke_operation,
            run_optional_step=mock.Mock(),
            log=mock.Mock(),
        )

        invoke_operation.assert_called_once_with(
            "ensure_prowlarr_ready",
            runtime.cfg,
            runtime.prowlarr_url,
            runtime.prowlarr_key,
            runtime.app_auth_cfg,
            runtime.wait_timeout,
        )


if __name__ == "__main__":
    unittest.main()

