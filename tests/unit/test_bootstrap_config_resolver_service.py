import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_config_resolver_service import (  # noqa: E402
    BootstrapConfigResolverConfig,
    BootstrapConfigResolverService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(self, hosts_stdout: str) -> None:
        self.hosts_stdout = hosts_stdout
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        cmd = list(args)
        self.calls.append(cmd)
        if cmd[:5] == ["-n", "media-stack", "get", "ingress", "media-stack-ingress"]:
            return _Result(0, self.hosts_stdout)
        return _Result(1, "", "unexpected command")


class BootstrapConfigResolverServiceTests(unittest.TestCase):
    def test_resolve_injects_hosts_into_homepage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_path = base / "bootstrap.json"
            job_config_path = base / "job-config.json"
            config_path.write_text(json.dumps({"homepage": {"enabled": False}}), encoding="utf-8")
            kube = _Kube("jellyfin.local\nsonarr.local\njellyfin.local\n")
            svc = BootstrapConfigResolverService(
                cfg=BootstrapConfigResolverConfig(
                    namespace="media-stack",
                    ingress_name="media-stack-ingress",
                    config_file=config_path,
                    job_config_file=job_config_path,
                ),
                kube=kube,
                info=mock.Mock(),
            )

            svc.resolve_bootstrap_config()
            written = json.loads(job_config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                written["homepage"]["hosts"],
                ["jellyfin.local", "sonarr.local"],
            )
            self.assertTrue(written["homepage"]["enabled"])


if __name__ == "__main__":
    unittest.main()
