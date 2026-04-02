import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.rebuild_ingress_service import (  # noqa: E402
    RebuildIngressConfig,
    RebuildIngressService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RebuildIngressServiceTests(unittest.TestCase):
    def test_auto_pick_prefers_public(self):
        run_kube = mock.Mock(return_value=_Result(0, "nginx\npublic\n"))
        svc = RebuildIngressService(
            cfg=RebuildIngressConfig(namespace="media-stack", ingress_class="auto"),
            info=mock.Mock(),
            warn=mock.Mock(),
            run_kube=run_kube,
        )
        self.assertEqual(svc.pick_ingress_class(), "public")

    def test_patch_returns_false_when_no_classes(self):
        run_kube = mock.Mock(return_value=_Result(0, ""))
        svc = RebuildIngressService(
            cfg=RebuildIngressConfig(namespace="media-stack", ingress_class="auto"),
            info=mock.Mock(),
            warn=mock.Mock(),
            run_kube=run_kube,
        )
        self.assertFalse(svc.patch_ingress_class())

    def test_patch_reconciles_edge_auth_annotations(self):
        run_kube = mock.Mock(
            side_effect=[
                _Result(0, "public\n"),
                _Result(
                    0,
                    '{"spec":{"ingressClassName":"public"},"metadata":{"annotations":{}}}',
                ),
                _Result(0, "ingress patched"),
            ]
        )
        svc = RebuildIngressService(
            cfg=RebuildIngressConfig(
                namespace="media-stack",
                ingress_class="auto",
                internet_exposed="1",
                route_strategy="hybrid",
                app_gateway_host="apps.media-stack.local",
                app_path_prefix="/app",
                media_server_direct_host="jellyfin.media-stack.local",
                auth_provider="authelia",
                auth_middleware="authelia@docker",
            ),
            info=mock.Mock(),
            warn=mock.Mock(),
            run_kube=run_kube,
        )
        self.assertTrue(svc.patch_ingress_class())
        patch_call = run_kube.call_args_list[-1]
        args = patch_call.args[0]
        self.assertIn("patch", args)
        self.assertIn("ingress", args)
        self.assertIn("media-stack-ingress", args)
        payload = args[args.index("-p") + 1]
        self.assertIn("media-stack.io/route-strategy", payload)
        self.assertIn("media-stack.io/auth-provider", payload)
        self.assertIn("media-stack.io/media-server-direct-host", payload)


if __name__ == "__main__":
    unittest.main()
