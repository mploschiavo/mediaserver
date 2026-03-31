from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import subprocess
from typing import Any


def choose_kubectl() -> list[str]:
    if shutil.which("microk8s"):
        return ["microk8s", "kubectl"]
    if shutil.which("kubectl"):
        return ["kubectl"]
    raise RuntimeError("Neither 'microk8s' nor 'kubectl' is available in PATH.")


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def get_secret(kubectl: list[str], namespace: str, secret_name: str) -> dict[str, str]:
    proc = run_cmd(
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
    kubectl: list[str],
    namespace: str,
    secret_name: str,
    values: dict[str, str],
) -> None:
    patch: dict[str, Any] = {"stringData": values}
    run_cmd(
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


def pick_free_local_port() -> int:
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
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass

    def ensure_alive(self) -> None:
        if self.proc and self.proc.poll() is not None:
            out = ""
            err = ""
            try:
                out = self.proc.stdout.read() if self.proc.stdout else ""
            except Exception:
                pass
            try:
                err = self.proc.stderr.read() if self.proc.stderr else ""
            except Exception:
                pass
            raise RuntimeError(
                f"kubectl port-forward exited early (code={self.proc.returncode}). stdout={out} stderr={err}"
            )
