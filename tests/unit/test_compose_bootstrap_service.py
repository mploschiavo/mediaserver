import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.compose.bootstrap_service import (  # noqa: E402
    ComposeBootstrapConfig,
    ComposeBootstrapService,
)


class ComposeBootstrapServiceTests(unittest.TestCase):
    def test_run_mounts_stack_root_and_sets_disk_guardrails_monitor_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_root = root / "config"
            media_root = root / "media"
            data_root = root / "data"
            config_root.mkdir(parents=True, exist_ok=True)
            media_root.mkdir(parents=True, exist_ok=True)
            data_root.mkdir(parents=True, exist_ok=True)

            compose_env_file = root / ".env"
            compose_env_file.write_text(
                "\n".join(
                    [
                        f"CONFIG_ROOT={config_root}",
                        f"MEDIA_ROOT={media_root}",
                        f"DATA_ROOT={data_root}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            bootstrap_config_file = root / "bootstrap.json"
            bootstrap_config_file.write_text("{}", encoding="utf-8")
            runtime_cfg_file = root / "runtime-config.json"
            runtime_cfg_file.write_text("{}", encoding="utf-8")

            docker = mock.Mock()
            docker.container_state.side_effect = [
                mock.Mock(status="running", exit_code=None),
                mock.Mock(status="exited", exit_code=0),
            ]

            cfg = ComposeBootstrapConfig(
                namespace="media-dev",
                compose_file=root / "docker-compose.yml",
                compose_env_file=compose_env_file,
                compose_project_name="media-dev",
                bootstrap_runner_image="example/bootstrap:latest",
                bootstrap_config_file=bootstrap_config_file,
                wait_timeout="1s",
                purpose="dev",
                preconfigure_api_keys=True,
                apply_initial_preferences=True,
                auto_download_content=False,
            )
            service = ComposeBootstrapService(
                cfg=cfg,
                info=lambda _msg: None,
                docker=docker,
            )

            with (
                mock.patch.object(service, "_prepare_runtime_config", return_value=runtime_cfg_file),
                mock.patch.object(service, "_run_preflight_handlers", return_value={}),
                mock.patch(
                    "core.platforms.compose.bootstrap_service.time.sleep",
                    return_value=None,
                ),
            ):
                service.run()

            create_kwargs = docker.create_container.call_args.kwargs
            volumes = create_kwargs.get("volumes") or {}
            self.assertEqual((volumes.get(str(root)) or {}).get("bind"), "/srv-stack")
            env = create_kwargs.get("environment") or {}
            self.assertEqual(env.get("DISK_GUARDRAILS_MONITOR_PATH"), "/srv-stack")


if __name__ == "__main__":
    unittest.main()
