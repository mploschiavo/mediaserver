import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.platforms.compose.controller_service import (  # noqa: E402
    ComposeBootstrapConfig,
    ComposeBootstrapService,
)


class ComposeBootstrapServiceTests(unittest.TestCase):
    _MINIMAL_CONFIG = {
        "config_version": 2,
        "technology_bindings": {
            "media_server": "jellyfin",
            "torrent_client": "qbittorrent",
            "usenet_client": "sabnzbd",
            "indexer_manager": "prowlarr",
        },
        "download_clients": {
            "qbittorrent": {"url": "http://qbittorrent:8080"},
            "sabnzbd": {"url": "http://sabnzbd:8080"},
        },
        "prowlarr_url": "http://prowlarr:9696",
        "arr_apps": [],
    }

    def _write_temp_config(self, dest_dir: str) -> Path:
        path = Path(dest_dir) / "media-stack.config.json"
        path.write_text(json.dumps(self._MINIMAL_CONFIG, indent=2), encoding="utf-8")
        return path

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
            docker.image_exists.return_value = True

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
                mock.patch.object(
                    service, "_prepare_runtime_config", return_value=runtime_cfg_file
                ),
                mock.patch.object(service, "_run_preflight_handlers", return_value={}),
                mock.patch(
                    "media_stack.core.platforms.compose.controller_service.time.sleep",
                    return_value=None,
                ),
            ):
                service.run()

            create_kwargs = docker.create_container.call_args.kwargs
            volumes = create_kwargs.get("volumes") or {}
            self.assertEqual((volumes.get(str(root)) or {}).get("bind"), "/srv-stack")
            env = create_kwargs.get("environment") or {}
            self.assertEqual(env.get("DISK_GUARDRAILS_MONITOR_PATH"), "/srv-stack")
            docker.pull_image.assert_not_called()

    def test_run_pulls_bootstrap_image_when_missing_under_if_missing_policy(self):
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
            docker.image_exists.return_value = False
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
                mock.patch.object(
                    service, "_prepare_runtime_config", return_value=runtime_cfg_file
                ),
                mock.patch.object(service, "_run_preflight_handlers", return_value={}),
                mock.patch(
                    "media_stack.core.platforms.compose.controller_service.time.sleep",
                    return_value=None,
                ),
            ):
                service.run()

            docker.pull_image.assert_called_once_with("example/bootstrap:latest")

    def test_prepare_runtime_config_writes_runtime_artifact_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bootstrap_config_file = self._write_temp_config(tmp)
            artifacts_dir = root / "artifacts"
            cfg = ComposeBootstrapConfig(
                namespace="media-dev",
                compose_file=root / "docker-compose.yml",
                compose_env_file=None,
                compose_project_name="media-dev",
                bootstrap_runner_image="example/bootstrap:latest",
                bootstrap_config_file=bootstrap_config_file,
                wait_timeout="1s",
                purpose="dev",
                preconfigure_api_keys=True,
                apply_initial_preferences=True,
                auto_download_content=False,
                runtime_config_policy_handler=(
                    "media_stack.services.apps.stack.controller_config_policy:"
                    "apply_bootstrap_runtime_policy"
                ),
                runtime_artifacts_dir=artifacts_dir,
            )
            service = ComposeBootstrapService(cfg=cfg, info=lambda _msg: None, docker=mock.Mock())

            runtime_cfg_path = service._prepare_runtime_config(compose_env={})
            try:
                artifact = artifacts_dir / "resolved" / "bootstrap.runtime.config.json"
                self.assertTrue(artifact.exists())
                payload = artifact.read_text(encoding="utf-8")
                self.assertIn('"prowlarr_url": "http://prowlarr:9696"', payload)
            finally:
                runtime_cfg_path.unlink(missing_ok=True)

    def test_run_sets_preconfigure_flags_in_bootstrap_runner_environment(self):
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

            bootstrap_config_file = self._write_temp_config(tmp)
            runtime_cfg_file = root / "runtime-config.json"
            runtime_cfg_file.write_text("{}", encoding="utf-8")

            docker = mock.Mock()
            docker.image_exists.return_value = True
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
            service = ComposeBootstrapService(cfg=cfg, info=lambda _msg: None, docker=docker)

            with (
                mock.patch.object(
                    service, "_prepare_runtime_config", return_value=runtime_cfg_file
                ),
                mock.patch.object(service, "_run_preflight_handlers", return_value={}),
                mock.patch(
                    "media_stack.core.platforms.compose.controller_service.time.sleep",
                    return_value=None,
                ),
            ):
                service.run()

            create_kwargs = docker.create_container.call_args.kwargs
            env = create_kwargs.get("environment") or {}
            self.assertEqual(env.get("PRECONFIGURE_API_KEYS"), "1")
            self.assertEqual(env.get("APPLY_INITIAL_PREFERENCES"), "1")
            self.assertEqual(env.get("FULLY_PRECONFIGURED"), "1")
            self.assertEqual(env.get("AUTO_DOWNLOAD_CONTENT"), "0")


if __name__ == "__main__":
    unittest.main()
