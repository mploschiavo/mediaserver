import tempfile
import unittest
from pathlib import Path

from core.platforms.kubernetes.services.rebuild_manifest_apply_service import (
    RebuildManifestApplyConfig,
    RebuildManifestApplyService,
)


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RebuildManifestApplyServiceTests(unittest.TestCase):
    def test_fallback_applies_ordered_files_optional_and_configured_component_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            applied_files: list[Path] = []
            kustomize_calls: list[list[str]] = []

            def _subprocess_run(cmd, **_kwargs):
                kustomize_calls.append(list(cmd))
                return _Proc(returncode=1, stderr="kustomize failed")

            service = RebuildManifestApplyService(
                cfg=RebuildManifestApplyConfig(
                    root_dir=root,
                    namespace="media-stack",
                    profile="full",
                    include_optional="1",
                    enable_components="1",
                    component_enable_manifest_paths=("k8s/unpackerr.yaml",),
                ),
                info=lambda _msg: None,
                warn=lambda _msg: None,
                run_kubectl=lambda *_args, **_kwargs: _Proc(returncode=0),
                apply_manifest_text_with_overrides=lambda _text: None,
                apply_manifest_file_with_overrides=lambda path: applied_files.append(path),
                subprocess_run=_subprocess_run,
            )

            service.apply_manifests_for_profile()

            self.assertTrue(kustomize_calls)
            names = [p.name for p in applied_files]
            self.assertIn("namespace.yaml", names)
            self.assertIn("core.yaml", names)
            self.assertIn("optional.yaml", names)
            self.assertIn("unpackerr.yaml", names)

    def test_profile_kustomize_success_applies_rendered_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "k8s" / "profiles" / "full"
            profile_dir.mkdir(parents=True, exist_ok=True)
            applied_text: list[str] = []
            applied_files: list[Path] = []

            def _subprocess_run(_cmd, **_kwargs):
                return _Proc(returncode=0, stdout="kind: ConfigMap\n")

            service = RebuildManifestApplyService(
                cfg=RebuildManifestApplyConfig(
                    root_dir=root,
                    namespace="media-stack",
                    profile="full",
                    include_optional="",
                    enable_components="",
                ),
                info=lambda _msg: None,
                warn=lambda _msg: None,
                run_kubectl=lambda *_args, **_kwargs: _Proc(returncode=0),
                apply_manifest_text_with_overrides=lambda text: applied_text.append(text),
                apply_manifest_file_with_overrides=lambda path: applied_files.append(path),
                subprocess_run=_subprocess_run,
            )

            service.apply_manifests_for_profile()

            self.assertEqual(applied_text, ["kind: ConfigMap\n"])
            self.assertEqual(applied_files, [])

    def test_empty_kustomize_cmd_defaults_to_kubectl_kustomize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_dir = root / "k8s" / "profiles" / "full"
            profile_dir.mkdir(parents=True, exist_ok=True)
            observed_cmds: list[list[str]] = []

            def _subprocess_run(cmd, **_kwargs):
                observed_cmds.append(list(cmd))
                return _Proc(returncode=0, stdout="kind: ConfigMap\n")

            service = RebuildManifestApplyService(
                cfg=RebuildManifestApplyConfig(
                    root_dir=root,
                    namespace="media-stack",
                    profile="full",
                    include_optional="",
                    enable_components="",
                    kustomize_cmd=(),
                ),
                info=lambda _msg: None,
                warn=lambda _msg: None,
                run_kubectl=lambda *_args, **_kwargs: _Proc(returncode=0),
                apply_manifest_text_with_overrides=lambda _text: None,
                apply_manifest_file_with_overrides=lambda _path: None,
                subprocess_run=_subprocess_run,
            )

            service.apply_manifests_for_profile()

            self.assertTrue(observed_cmds)
            self.assertEqual(observed_cmds[0][:2], ["kubectl", "kustomize"])


if __name__ == "__main__":
    unittest.main()
