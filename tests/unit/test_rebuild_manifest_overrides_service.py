import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.platforms.kubernetes.services.rebuild_manifest_overrides_service import (  # noqa: E402
    RebuildManifestOverridesConfig,
    RebuildManifestOverridesService,
)


class RebuildManifestOverridesServiceTests(unittest.TestCase):
    def _svc(self, run_kubectl=None):
        return RebuildManifestOverridesService(
            cfg=RebuildManifestOverridesConfig(
                namespace="media-stack-dev",
                prepare_host_root="/mnt/media",
                ingress_domain="example.local",
                pvc_storage_class="fast",
            ),
            run_kubectl=run_kubectl or mock.Mock(),
        )

    def test_stream_overrides_namespace_host_root_and_domain(self):
        svc = self._svc()
        rendered = svc.stream_with_manifest_overrides(
            "namespace: media-stack\n"
            "name: media-stack\n"
            "host: jellyfin.local\n"
            "path: /srv/media-stack\n"
            '  STACK_ADMIN_PASSWORD: "media-stack"\n'
        )
        self.assertIn("namespace: media-stack-dev", rendered)
        self.assertIn("name: media-stack-dev", rendered)
        self.assertIn("jellyfin.example.local", rendered)
        self.assertIn("/mnt/media", rendered)
        self.assertIn('STACK_ADMIN_PASSWORD: "media-stack-dev"', rendered)

    def test_apply_manifest_text_calls_kubectl_with_patched_manifest(self):
        run_kubectl = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        svc = self._svc(run_kubectl=run_kubectl)
        svc.apply_manifest_text_with_overrides(
            "kind: PersistentVolumeClaim\nspec:\n  resources:\n    requests:\n      storage: 1Gi\n"
        )
        self.assertEqual(run_kubectl.call_count, 2)
        self.assertEqual(
            run_kubectl.call_args_list[0].args[0],
            ["create", "namespace", "media-stack-dev"],
        )
        self.assertEqual(run_kubectl.call_args_list[1].args[0], ["apply", "-f", "-"])

    def test_apply_manifest_text_deletes_existing_jobs_before_apply(self):
        run_kubectl = mock.Mock(return_value=mock.Mock(returncode=0, stdout="", stderr=""))
        svc = self._svc(run_kubectl=run_kubectl)
        svc.apply_manifest_text_with_overrides(
            "apiVersion: batch/v1\n"
            "kind: Job\n"
            "metadata:\n"
            "  name: media-stack-controller\n"
            "  namespace: media-stack\n"
            "---\n"
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: sample\n"
            "  namespace: media-stack\n"
        )
        self.assertEqual(run_kubectl.call_count, 3)
        self.assertEqual(
            run_kubectl.call_args_list[1].args[0],
            ["-n", "media-stack-dev", "delete", "job", "media-stack-controller", "--ignore-not-found"],
        )
        self.assertEqual(run_kubectl.call_args_list[1].kwargs.get("check"), False)
        self.assertEqual(run_kubectl.call_args_list[2].args[0], ["apply", "-f", "-"])

    def test_apply_manifest_text_conflict_falls_back_to_replace_create(self):
        run_kubectl = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0, stdout="", stderr=""),
                mock.Mock(returncode=1, stdout="", stderr="Conflict"),
                mock.Mock(returncode=0, stdout="", stderr=""),
                mock.Mock(returncode=1, stdout="", stderr="NotFound"),
                mock.Mock(returncode=0, stdout="", stderr=""),
            ]
        )
        svc = self._svc(run_kubectl=run_kubectl)
        svc.apply_manifest_text_with_overrides(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: first\n"
            "  namespace: media-stack\n"
            "---\n"
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: second\n"
            "  namespace: media-stack\n"
            "type: Opaque\n"
        )
        self.assertEqual(run_kubectl.call_args_list[1].args[0], ["apply", "-f", "-"])
        self.assertEqual(run_kubectl.call_args_list[1].kwargs.get("check"), False)
        self.assertEqual(run_kubectl.call_args_list[2].args[0], ["replace", "-f", "-"])
        self.assertEqual(run_kubectl.call_args_list[3].args[0], ["replace", "-f", "-"])
        self.assertEqual(run_kubectl.call_args_list[4].args[0], ["create", "-f", "-"])

    def test_apply_manifest_text_conflict_tolerates_existing_immutable_objects(self):
        run_kubectl = mock.Mock(
            side_effect=[
                mock.Mock(returncode=0, stdout="", stderr=""),
                mock.Mock(returncode=1, stdout="", stderr="Conflict"),
                mock.Mock(returncode=1, stdout="", stderr="spec is immutable"),
                mock.Mock(returncode=1, stdout="", stderr="already exists"),
            ]
        )
        svc = self._svc(run_kubectl=run_kubectl)
        svc.apply_manifest_text_with_overrides(
            "apiVersion: v1\n"
            "kind: PersistentVolumeClaim\n"
            "metadata:\n"
            "  name: media-stack-config-bazarr\n"
            "  namespace: media-stack\n"
            "spec:\n"
            "  resources:\n"
            "    requests:\n"
            "      storage: 5Gi\n"
        )
        self.assertEqual(run_kubectl.call_count, 4)
        self.assertEqual(run_kubectl.call_args_list[2].args[0], ["replace", "-f", "-"])
        self.assertEqual(run_kubectl.call_args_list[3].args[0], ["create", "-f", "-"])


if __name__ == "__main__":
    unittest.main()
