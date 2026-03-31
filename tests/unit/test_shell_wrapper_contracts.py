import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


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
        self.assertIn("scripts/install.sh", proc.stdout)

    def test_rebuild_wrapper_help_contract(self):
        proc = run_wrapper("rebuild-and-bootstrap.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/rebuild-and-bootstrap.sh", proc.stdout)

    def test_rebuild_wrapper_missing_config_file(self):
        proc = run_wrapper(
            "rebuild-and-bootstrap.sh",
            env_overrides={"CONFIG_FILE": "/tmp/does-not-exist-rebuild.json"},
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Config file not found", proc.stdout + proc.stderr)

    def test_bootstrap_all_wrapper_help_contract(self):
        proc = run_wrapper("bootstrap-all.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/bootstrap-all.sh", proc.stdout)

    def test_bootstrap_all_wrapper_missing_config_file(self):
        proc = run_wrapper("bootstrap-all.sh", "/tmp/does-not-exist-media-stack.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Config file not found", proc.stderr)

    def test_ensure_sab_wrapper_help_contract(self):
        proc = run_wrapper("ensure-sabnzbd-api-access.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/ensure-sabnzbd-api-access.sh", proc.stdout)

    def test_ensure_sab_wrapper_rejects_unknown_flag(self):
        proc = run_wrapper("ensure-sabnzbd-api-access.sh", "--nope")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("unrecognized arguments", proc.stderr)

    def test_set_pvc_wrapper_help_contract(self):
        proc = run_wrapper("set-pvc-storage-class.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/set-pvc-storage-class.sh", proc.stdout)

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
        self.assertIn("scripts/sync-unpackerr-keys.sh", proc.stdout)

    def test_ensure_jellyfin_wrapper_help_contract(self):
        proc = run_wrapper("ensure-jellyfin-bootstrap.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/ensure-jellyfin-bootstrap.sh", proc.stdout)

    def test_run_bootstrap_job_wrapper_help_contract(self):
        proc = run_wrapper("run-bootstrap-job.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/run-bootstrap-job.sh", proc.stdout)

    def test_run_bootstrap_job_wrapper_missing_config_file(self):
        proc = run_wrapper("run-bootstrap-job.sh", "/tmp/does-not-exist-bootstrap.json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Config file not found", proc.stderr)

    def test_run_prowlarr_auto_indexers_wrapper_help_contract(self):
        proc = run_wrapper("run-prowlarr-auto-indexers.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/run-prowlarr-auto-indexers.sh", proc.stdout)

    def test_validate_bootstrap_config_wrapper_help_contract(self):
        proc = run_wrapper("validate-bootstrap-config.sh", "--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("scripts/validate-bootstrap-config.sh", proc.stdout)


if __name__ == "__main__":
    unittest.main()
