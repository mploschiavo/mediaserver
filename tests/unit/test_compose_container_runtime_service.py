import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.compose.services.container_runtime import (  # noqa: E402
    ComposeContainerRuntimeService,
)


class ComposeContainerRuntimeServiceTests(unittest.TestCase):
    def _service(
        self, *, compose_file: Path, docker: object, spec_resolver: object
    ) -> ComposeContainerRuntimeService:
        return ComposeContainerRuntimeService(
            compose_file=compose_file,
            docker=docker,
            spec_resolver=spec_resolver,
            label_service=mock.Mock(),
            info=mock.Mock(),
        )

    def test_port_preflight_raises_on_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text("services: {}\n", encoding="utf-8")

            occupied = mock.Mock()
            occupied.name = "other-container"
            occupied.attrs = {
                "NetworkSettings": {
                    "Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}
                }
            }

            docker = mock.Mock()
            docker.list_running_containers.return_value = [occupied]
            spec_resolver = mock.Mock()
            spec_resolver.container_name.side_effect = (
                lambda service_name, _spec: f"{service_name}-container"
            )
            service = self._service(
                compose_file=compose_file, docker=docker, spec_resolver=spec_resolver
            )

            services = {
                "app": {
                    "container_name": "app-container",
                    "ports": ["8080:8080"],
                }
            }

            with self.assertRaises(RuntimeError):
                service.assert_host_ports_available(services)

    def test_port_preflight_ignores_selected_container_replacements(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text("services: {}\n", encoding="utf-8")

            occupied = mock.Mock()
            occupied.name = "app-container"
            occupied.attrs = {
                "NetworkSettings": {
                    "Ports": {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]}
                }
            }

            docker = mock.Mock()
            docker.list_running_containers.return_value = [occupied]
            spec_resolver = mock.Mock()
            spec_resolver.container_name.side_effect = (
                lambda service_name, _spec: f"{service_name}-container"
            )
            service = self._service(
                compose_file=compose_file, docker=docker, spec_resolver=spec_resolver
            )

            services = {
                "app": {
                    "container_name": "app-container",
                    "ports": ["8080:8080"],
                }
            }

            service.assert_host_ports_available(services)

    def test_storage_budget_report_includes_bind_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_root = root / "media"
            media_root.mkdir(parents=True, exist_ok=True)
            (media_root / "movie.mkv").write_bytes(b"x" * 1024)
            compose_file = root / "docker-compose.yml"
            compose_file.write_text("services: {}\n", encoding="utf-8")

            docker = mock.Mock()
            spec_resolver = mock.Mock()
            service = self._service(
                compose_file=compose_file, docker=docker, spec_resolver=spec_resolver
            )

            services = {
                "jellyfin": {
                    "container_name": "jellyfin",
                    "volumes": [f"{media_root}:/media"],
                }
            }

            report = service.enforce_storage_budget(services, disk_allocation_gb=1)
            self.assertIn("storage_roots", report)
            self.assertGreaterEqual(len(report["storage_roots"]), 1)
            self.assertFalse(report["over_budget"])

    def test_storage_budget_raises_when_estimated_usage_exceeds_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_root = root / "media"
            media_root.mkdir(parents=True, exist_ok=True)
            compose_file = root / "docker-compose.yml"
            compose_file.write_text("services: {}\n", encoding="utf-8")

            docker = mock.Mock()
            spec_resolver = mock.Mock()
            service = self._service(
                compose_file=compose_file, docker=docker, spec_resolver=spec_resolver
            )
            services = {
                "jellyfin": {
                    "container_name": "jellyfin",
                    "volumes": [f"{media_root}:/media"],
                }
            }

            with mock.patch.object(
                service,
                "_path_usage_bytes",
                return_value=2 * 1024 * 1024 * 1024,
            ):
                with self.assertRaises(RuntimeError):
                    service.enforce_storage_budget(services, disk_allocation_gb=1)


if __name__ == "__main__":
    unittest.main()
