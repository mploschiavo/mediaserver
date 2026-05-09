from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
from typing import Any
import logging


class JellyfinControllerKubeService:
    """kubectl/microk8s helpers for the Jellyfin bootstrap CLI.

    ADR-0012 — every former loose helper is now a plain instance
    method on this class. Module-level aliases at the bottom of the
    file preserve the public import API. Methods that call other
    helpers route through ``sys.modules[__name__]`` so existing test
    suites that ``patch.object(module, "run_cmd", ...)`` keep
    intercepting correctly (see
    ``tests/unit/apps/jellyfin/test_jellyfin_bootstrap_kube_service.py``).
    """

    def choose_kubectl(self) -> list[str]:
        if shutil.which("microk8s"):
            return ["microk8s", "kubectl"]
        if shutil.which("kubectl"):
            return ["kubectl"]
        raise RuntimeError("Neither 'microk8s' nor 'kubectl' is available in PATH.")

    def run_cmd(
        self, cmd: list[str], check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Command failed ({proc.returncode}): {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        return proc

    def get_secret(
        self, kubectl: list[str], namespace: str, secret_name: str
    ) -> dict[str, str]:
        # ADR-0012 design principle 3 — route through module alias so
        # ``patch.object(module, "run_cmd", ...)`` in tests intercepts.
        _module = sys.modules[__name__]
        proc = _module.run_cmd(
            kubectl + ["-n", namespace, "get", "secret", secret_name, "-o", "json"],
            check=False,
        )
        if proc.returncode != 0:
            return {}
        raw = json.loads(proc.stdout)
        data = raw.get("data") or {}
        decoded: dict[str, str] = {}
        for key, value in data.items():
            try:
                decoded[key] = base64.b64decode(value).decode("utf-8")
            except Exception:
                decoded[key] = ""
        return decoded

    def patch_secret(
        self,
        kubectl: list[str],
        namespace: str,
        secret_name: str,
        values: dict[str, str],
    ) -> None:
        patch: dict[str, Any] = {"stringData": values}
        # ADR-0012 design principle 3 — route through module alias so
        # ``patch.object(module, "run_cmd", ...)`` in tests intercepts.
        _module = sys.modules[__name__]
        _module.run_cmd(
            kubectl
            + [
                "-n",
                namespace,
                "patch",
                "secret",
                secret_name,
                "--type",
                "merge",
                "-p",
                json.dumps(patch),
            ]
        )

    def pick_free_local_port(self) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return int(port)


class PortForward:
    def __init__(self, cmd: list[str]):
        self.cmd = cmd
        self.proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> "PortForward":
        self.proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except Exception as exc:
                log_swallowed(exc)
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception as exc:
                    log_swallowed(exc)

    def ensure_alive(self) -> None:
        if self.proc and self.proc.poll() is not None:
            out = ""
            err = ""
            try:
                out = self.proc.stdout.read() if self.proc.stdout else ""
            except Exception as exc:
                log_swallowed(exc)
            try:
                err = self.proc.stderr.read() if self.proc.stderr else ""
            except Exception as exc:
                log_swallowed(exc)
            raise RuntimeError(
                f"kubectl port-forward exited early (code={self.proc.returncode}). stdout={out} stderr={err}"
            )


# Module-level singleton + aliases (ADR-0012 design principle 2) —
# preserves the public import API used by
# ``cli_ensure_controller_main`` and ``controller_db_discovery_service``.
_INSTANCE = JellyfinControllerKubeService()

choose_kubectl = _INSTANCE.choose_kubectl
run_cmd = _INSTANCE.run_cmd
get_secret = _INSTANCE.get_secret
patch_secret = _INSTANCE.patch_secret
pick_free_local_port = _INSTANCE.pick_free_local_port
