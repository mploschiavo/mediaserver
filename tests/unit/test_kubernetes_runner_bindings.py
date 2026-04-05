import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.kubernetes.services.rebuild_deployments_wait_service import (  # noqa: E402
    RebuildDeploymentsWaitService,
)
from core.platforms.kubernetes.services.rebuild_ingress_service import (  # noqa: E402
    RebuildIngressService,
)
from core.platforms.kubernetes.services.rebuild_manifest_apply_service import (  # noqa: E402
    RebuildManifestApplyService,
)
from core.platforms.kubernetes.services.rebuild_namespace_service import (  # noqa: E402
    RebuildNamespaceService,
)
from core.platforms.kubernetes.services.rebuild_secret_preservation_service import (  # noqa: E402
    RebuildSecretPreservationService,
)
from core.platforms.kubernetes.services.rebuild_smoke_test_service import (  # noqa: E402
    RebuildSmokeTestService,
)
from core.platforms.kubernetes.services.runner_bindings import (  # noqa: E402
    build_kubernetes_runner_request,
)


class _FakeRunner:
    def __init__(self, root_dir: Path):
        self.cfg = SimpleNamespace(
            namespace="media-dev",
            root_dir=root_dir,
            profile="full",
            include_optional="1",
            enable_components="1",
            prepare_host_root="/srv/media-stack",
            ingress_domain="local",
            pvc_storage_class="",
            ingress_class="auto",
            internet_exposed="0",
            route_strategy="subdomain",
            app_gateway_host="",
            app_path_prefix="/app",
            media_server_direct_host="",
            auth_provider="none",
            auth_middleware="",
            wait_timeout="20m",
            node_ip="",
            secret_name="media-stack-secrets",
        )
        self.tracker = SimpleNamespace(warn=mock.Mock())
        self.kube = SimpleNamespace(cmd_prefix=["kubectl"])
        self._run_kubectl = mock.Mock()
        self._run_script = mock.Mock()

    def _resolved_platform_target(self) -> str:
        return "k8s"

    def _ingress_class_priority(self) -> tuple[str, ...]:
        return ("public", "nginx")

    def _rebuild_profile_actions(self):
        return (
            {"full": ("flaresolverr",)},
            {"public-demo": ("media-stack.example.com",)},
            {"public-demo": "media-stack-tls"},
            {"power-user": ("k8s/optional.yaml",)},
            ("k8s/enable-components.yaml",),
            ("STACK_ADMIN_USERNAME", "STACK_ADMIN_PASSWORD"),
            ("k8s/core.yaml",),
        )


class KubernetesRunnerBindingsTests(unittest.TestCase):
    def test_build_runner_request_returns_kubernetes_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _FakeRunner(root_dir=Path(tmp))
            info = mock.Mock()

            request = build_kubernetes_runner_request(runner, info)

            self.assertEqual(request.get("target"), "k8s")
            self.assertEqual(request.get("environment_id"), "media-dev")
            self.assertIs(request.get("info"), info)
            self.assertIs(request.get("run_kubectl"), runner._run_kubectl)
            self.assertIsInstance(request.get("namespace_service"), RebuildNamespaceService)
            self.assertIsInstance(
                request.get("manifest_apply_service"), RebuildManifestApplyService
            )
            self.assertIsInstance(request.get("ingress_service"), RebuildIngressService)
            self.assertIsInstance(
                request.get("deployments_wait_service"), RebuildDeploymentsWaitService
            )
            self.assertIsInstance(request.get("smoke_test_service"), RebuildSmokeTestService)
            self.assertIsInstance(
                request.get("secret_preservation_service"), RebuildSecretPreservationService
            )
            secret_service = request["secret_preservation_service"]
            manifest_service = request["manifest_apply_service"]
            self.assertEqual(
                secret_service.cfg.preserve_keys,
                ("STACK_ADMIN_USERNAME", "STACK_ADMIN_PASSWORD"),
            )
            self.assertEqual(manifest_service.cfg.kustomize_cmd, ("kubectl", "kustomize"))


if __name__ == "__main__":
    unittest.main()
