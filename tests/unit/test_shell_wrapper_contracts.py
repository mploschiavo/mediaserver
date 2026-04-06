import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "bin"


def run_wrapper(
    script_name: str,
    *args: str,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHON_BIN"] = sys.executable
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(SCRIPTS / script_name), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class ShellWrapperContractTests(unittest.TestCase):
    def test_install_wrapper_help_contract(self):
        proc = run_wrapper("install.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/install.sh", proc.stdout)

    def test_deploy_stack_wrapper_help_contract(self):
        proc = run_wrapper("deploy-stack.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/deploy-stack.sh", proc.stdout)

    def test_deploy_stack_wrapper_missing_config_file(self):
        proc = run_wrapper(
            "deploy-stack.sh",
            env_overrides={"CONFIG_FILE": "/tmp/does-not-exist-rebuild.json"},
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Config file not found", proc.stdout + proc.stderr)

    def test_bootstrap_all_wrapper_help_contract(self):
        proc = run_wrapper("bootstrap-all.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/bootstrap-all.sh", proc.stdout)

    def test_bootstrap_all_wrapper_missing_config_file(self):
        proc = run_wrapper("bootstrap-all.sh", "/tmp/does-not-exist-media-stack.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Config file not found", proc.stderr)

    def test_ensure_sab_wrapper_help_contract(self):
        proc = run_wrapper("ensure-sabnzbd-api-access.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/ensure-sabnzbd-api-access.sh", proc.stdout)

    def test_ensure_sab_wrapper_rejects_unknown_flag(self):
        proc = run_wrapper("ensure-sabnzbd-api-access.sh", "--nope")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unrecognized arguments", proc.stderr)

    def test_set_pvc_wrapper_help_contract(self):
        proc = run_wrapper("set-pvc-storage-class.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/set-pvc-storage-class.sh", proc.stdout)

    def test_set_pvc_wrapper_missing_required_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "storage-pvc.yaml"
            target.write_text(
                textwrap.dedent(
                    """\
                    apiVersion: v1
                    kind: PersistentVolumeClaim
                    metadata:
                      name: demo
                    spec:
                      accessModes: [ReadWriteOnce]
                      resources:
                        requests:
                          storage: 1Gi
                    """
                ),
                encoding="utf-8",
            )
            proc = run_wrapper("set-pvc-storage-class.sh", "--file", str(target))
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("STORAGE_CLASS_NAME is required", proc.stdout + proc.stderr)

    def test_set_pvc_wrapper_clear_mode_stays_backward_compatible(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "storage-pvc.yaml"
            target.write_text(
                textwrap.dedent(
                    """\
                    apiVersion: v1
                    kind: PersistentVolumeClaim
                    metadata:
                      name: demo
                    spec:
                      storageClassName: old-class
                      accessModes: [ReadWriteOnce]
                      resources:
                        requests:
                          storage: 1Gi
                    """
                ),
                encoding="utf-8",
            )
            proc = run_wrapper("set-pvc-storage-class.sh", "--clear", "--file", str(target))
            self.assertEqual(proc.returncode, 0)
            rewritten = target.read_text(encoding="utf-8")
            self.assertNotIn("storageClassName:", rewritten)

    def test_sync_unpackerr_wrapper_help_contract(self):
        proc = run_wrapper("sync-unpackerr-keys.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/sync-unpackerr-keys.sh", proc.stdout)

    def test_ensure_jellyfin_wrapper_help_contract(self):
        proc = run_wrapper("ensure-jellyfin-bootstrap.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/ensure-jellyfin-bootstrap.sh", proc.stdout)

    def test_run_bootstrap_job_wrapper_help_contract(self):
        proc = run_wrapper("run-bootstrap-job.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/run-bootstrap-job.sh", proc.stdout)

    def test_run_bootstrap_job_wrapper_missing_config_file(self):
        proc = run_wrapper("run-bootstrap-job.sh", "/tmp/does-not-exist-bootstrap.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Config file not found", proc.stderr)

    def test_run_prowlarr_auto_indexers_wrapper_help_contract(self):
        proc = run_wrapper("run-prowlarr-auto-indexers.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/run-prowlarr-auto-indexers.sh", proc.stdout)

    def test_validate_bootstrap_config_wrapper_help_contract(self):
        proc = run_wrapper("validate-bootstrap-config.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/validate-bootstrap-config.sh", proc.stdout)

    def test_verify_flow_wrapper_help_contract(self):
        proc = run_wrapper("verify-flow.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/verify-flow.sh", proc.stdout)

    def test_microk8s_smoke_test_wrapper_help_contract(self):
        proc = run_wrapper("microk8s-smoke-test.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/microk8s-smoke-test.sh", proc.stdout)

    def test_watch_install_wrapper_help_contract(self):
        proc = run_wrapper("watch-install.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/watch-install.sh", proc.stdout)

    def test_fast_first_run_wrapper_help_contract(self):
        proc = run_wrapper("fast-first-run.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/fast-first-run.sh", proc.stdout)

    def test_reset_qbit_webui_auth_wrapper_help_contract(self):
        proc = run_wrapper("reset-qbit-webui-auth.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/reset-qbit-webui-auth.sh", proc.stdout)

    def test_build_bootstrap_runner_image_wrapper_help_contract(self):
        proc = run_wrapper("build-bootstrap-runner-image.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/build-bootstrap-runner-image.sh", proc.stdout)

    def test_microk8s_reconcile_wrapper_help_contract(self):
        proc = run_wrapper("microk8s-reconcile.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/microk8s-reconcile.sh", proc.stdout)

    def test_render_architecture_diagrams_wrapper_help_contract(self):
        proc = run_wrapper("render-architecture-diagrams.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/render-architecture-diagrams.sh", proc.stdout)

    def test_setup_lan_tls_wrapper_help_contract(self):
        proc = run_wrapper("setup-lan-tls.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/setup-lan-tls.sh", proc.stdout)

    def test_seed_jellyseerr_local_admin_wrapper_help_contract(self):
        proc = run_wrapper("seed-jellyseerr-local-admin.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/seed-jellyseerr-local-admin.sh", proc.stdout)

    def test_backup_stack_wrapper_help_contract(self):
        proc = run_wrapper("backup-stack.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/backup-stack.sh", proc.stdout)

    def test_restore_stack_wrapper_help_contract(self):
        proc = run_wrapper("restore-stack.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/restore-stack.sh", proc.stdout)

    def test_apply_scale_policy_wrapper_help_contract(self):
        proc = run_wrapper("apply-scale-policy.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/apply-scale-policy.sh", proc.stdout)

    def test_deploy_verify_wrapper_help_contract(self):
        proc = run_wrapper("deploy-verify.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/deploy-verify.sh", proc.stdout)

    def test_run_playwright_screenshots_wrapper_help_contract(self):
        proc = run_wrapper("run-playwright-screenshots.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/run-playwright-screenshots.sh", proc.stdout)

    def test_with_env_wrapper_help_contract(self):
        proc = run_wrapper("with-env.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("bin/with-env.sh", proc.stdout)

    def test_with_env_wrapper_defaults_delete_namespace_to_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "media-dev.env"
            env_file.write_text(
                textwrap.dedent(
                    """\
                    NAMESPACE=media-dev
                    INGRESS_DOMAIN=media-dev.local
                    """
                ),
                encoding="utf-8",
            )
            proc = run_wrapper(
                "with-env.sh",
                str(env_file),
                "bash",
                "-lc",
                'printf "%s" "$DELETE_NAMESPACE"',
            )
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "0")


if __name__ == "__main__":
    unittest.main()
