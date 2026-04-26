"""Tail Envoy's access log for the operator-facing live-flow card.

Three source modes, tried in order:

  1. ``ENVOY_ACCESS_LOG_PATH`` env var pointing at a file the
     controller can read directly (the cleanest path; used when an
     operator-mounted volume shares the log file).
  2. ``kubectl logs media-stack-envoy --tail=N`` when running on K8s
     (the controller's ServiceAccount has the ``pods/log`` verb in
     the bundled RBAC).
  3. ``docker compose logs envoy --tail=N`` when running on Compose.

Returns a list of dicts:

    [
      {
        "ts": "2026-04-26T12:34:56Z",
        "method": "GET",
        "path": "/api/health",
        "status": 200,
        "upstream": "service_jellyfin",
        "duration_ms": 12,
        "client_ip": "10.0.1.5",
        "user_agent": "...",
        "raw": "<original line, on parse failure>",
      },
      ...
    ]

Each entry is the parsed JSON line if the Envoy access_log is
configured for JSON (the media-stack default); falls back to a
``raw`` field otherwise. Best-effort: malformed lines are dropped
silently so a single bad entry doesn't break the panel.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Envoy's default access-log JSON keys (the media-stack profile pins
# these in the access_log filter config). If the operator changed the
# format, we fall back to surfacing the raw line.
_KEY_TS = ("ts", "start_time", "@timestamp")
_KEY_METHOD = ("method", "request_method", ":method")
_KEY_PATH = ("path", "request_path", ":path")
_KEY_STATUS = ("status", "response_code")
_KEY_UPSTREAM = ("upstream", "upstream_cluster", "cluster")
_KEY_DURATION = ("duration", "duration_ms", "request_duration_ms")
_KEY_CLIENT = ("client_ip", "downstream_remote_address", "x_forwarded_for")
_KEY_UA = ("user_agent", "request_user_agent")


def _first(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _parse_line(line: str) -> dict[str, Any]:
    """Parse one access-log line into the wire shape. JSON-typed
    lines unpack into structured fields; anything else returns just
    a ``raw`` field so the UI can render the original text."""
    line = line.strip()
    if not line:
        return {}
    if line.startswith("{") and line.endswith("}"):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return {"raw": line}
        return {
            "ts": _first(obj, _KEY_TS),
            "method": _first(obj, _KEY_METHOD),
            "path": _first(obj, _KEY_PATH),
            "status": _first(obj, _KEY_STATUS),
            "upstream": _first(obj, _KEY_UPSTREAM),
            "duration_ms": _first(obj, _KEY_DURATION),
            "client_ip": _first(obj, _KEY_CLIENT),
            "user_agent": _first(obj, _KEY_UA),
            "raw": line,
        }
    return {"raw": line}


def _read_file(path: Path, limit: int) -> list[str]:
    if not path.is_file():
        return []
    # Read the tail efficiently — for large files we don't want to
    # slurp the whole thing. Read in 64KB chunks from the end.
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 65536
            data = b""
            pos = size
            while pos > 0 and data.count(b"\n") <= limit:
                pos = max(0, pos - block)
                f.seek(pos)
                data = f.read(size - pos)
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()
            return lines[-limit:]
    except OSError:
        return []


def _kubectl_tail(limit: int) -> list[str]:
    """kubectl logs media-stack-envoy --tail=<limit>. Returns [] if
    kubectl is absent or the call fails (e.g. running on Compose)."""
    if not shutil.which("kubectl"):
        return []
    namespace = os.environ.get("MEDIA_STACK_NAMESPACE", "media-stack")
    pod_label = os.environ.get(
        "ENVOY_POD_LABEL", "app=media-stack-envoy",
    )
    try:
        proc = subprocess.run(
            [
                "kubectl", "-n", namespace, "logs",
                "-l", pod_label,
                "--tail", str(limit),
                "--max-log-requests", "1",
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    return proc.stdout.decode("utf-8", errors="replace").splitlines()


def _docker_tail(limit: int) -> list[str]:
    """docker compose logs envoy --tail=<limit>. Returns [] if
    docker is absent or the call fails."""
    if not shutil.which("docker"):
        return []
    try:
        proc = subprocess.run(
            ["docker", "compose", "logs", "envoy", "--tail", str(limit)],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []
    return proc.stdout.decode("utf-8", errors="replace").splitlines()


def tail_envoy_access_log(limit: int = 50) -> list[dict[str, Any]]:
    """Return up to ``limit`` recent access-log entries, parsed.
    Newest last (chronological order, matching Envoy's emission)."""
    lines: list[str] = []

    explicit = os.environ.get("ENVOY_ACCESS_LOG_PATH", "").strip()
    if explicit:
        lines = _read_file(Path(explicit), limit)
    if not lines and os.environ.get("KUBERNETES_SERVICE_HOST"):
        lines = _kubectl_tail(limit)
    if not lines:
        lines = _docker_tail(limit)

    rows: list[dict[str, Any]] = []
    for line in lines:
        parsed = _parse_line(line)
        if parsed:
            rows.append(parsed)
    return rows[-limit:]
