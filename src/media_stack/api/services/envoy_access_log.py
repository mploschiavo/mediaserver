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
_KEY_UPSTREAM_HOST = ("upstream_host",)
_KEY_DURATION = ("duration", "duration_ms", "request_duration_ms")
# Real client IP — Envoy resolves this for us when use_remote_address
# + xff_num_trusted_hops are configured (see envoy.runtime.base.yaml).
# Falls back to the proxy-hop address when those settings are off so
# operators upgrading from older deploys still see *something*.
_KEY_CLIENT = ("client_ip", "downstream_remote_address")
# XFF chain — full audit trail. Useful when more than xff_num_trusted_hops
# proxies are between the client and Envoy (e.g. CDN added mid-deploy).
_KEY_XFF = ("x_forwarded_for", "request_x_forwarded_for")
# Cloudflare's authoritative client IP. CF strips inbound CF-* headers
# so this can be trusted when the request came through Cloudflare.
_KEY_CF = ("cf_connecting_ip", "request_cf_connecting_ip")
_KEY_REAL_IP = ("x_real_ip", "real_ip")
_KEY_HOST = ("host", "authority", "request_authority")
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
            "upstream_host": _first(obj, _KEY_UPSTREAM_HOST),
            "duration_ms": _first(obj, _KEY_DURATION),
            "client_ip": _first(obj, _KEY_CLIENT),
            "x_forwarded_for": _first(obj, _KEY_XFF),
            "cf_connecting_ip": _first(obj, _KEY_CF),
            "x_real_ip": _first(obj, _KEY_REAL_IP),
            "host": _first(obj, _KEY_HOST),
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


def _k8s_tail(limit: int) -> list[str]:
    """Fetch the last N stdout lines from the Envoy pod via the
    Kubernetes Python client.

    The controller image ships kubernetes>=35; ``kubectl`` is NOT
    installed (extra ~50MB binary for one feature). The K8s API
    server's ``GET /api/v1/namespaces/{ns}/pods/{name}/log?tailLines=N``
    returns the same content, and the bundled controller
    ServiceAccount has the ``pods/log`` verb.

    ``ENVOY_POD_LABEL`` defaults to ``app=envoy`` (matches the
    bundled K8s manifests in deploy/k8s/base/edge/). Operators with a
    custom label set the env to override.
    """
    namespace = os.environ.get("MEDIA_STACK_NAMESPACE", "media-stack")
    pod_label = os.environ.get("ENVOY_POD_LABEL", "app=envoy")
    container = os.environ.get("ENVOY_CONTAINER_NAME", "envoy")
    try:
        # Lazy import — kubernetes is heavy; only paid when an
        # operator actually opens the live-tail panel.
        from kubernetes import client, config
        try:
            config.load_incluster_config()
        except Exception:  # noqa: BLE001
            try:
                config.load_kube_config()
            except Exception:  # noqa: BLE001
                return []
        v1 = client.CoreV1Api()
        # Resolve the label to a concrete pod name (pick the first
        # ready pod). Multiple replicas would need fan-out; the
        # bundled deploy is single-replica so first-pod is correct.
        pods = v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=pod_label,
            limit=5,
        )
        if not pods.items:
            return []
        pod_name = pods.items[0].metadata.name
        # tailLines = limit; container = the envoy sidecar (ignores
        # init containers). We avoid streaming/follow — the panel
        # polls every 5s, which is plenty without sustaining an
        # open watch.
        log_text: str = v1.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container,
            tail_lines=limit,
            timestamps=False,
        )
        return log_text.splitlines() if log_text else []
    except Exception:  # noqa: BLE001
        # Anything wrong (RBAC denied, pod renamed, API unreachable)
        # falls through to the docker compose path so a misconfigured
        # K8s deploy doesn't black-hole the panel.
        return []


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
        lines = _k8s_tail(limit)
    if not lines:
        lines = _docker_tail(limit)

    rows: list[dict[str, Any]] = []
    for line in lines:
        parsed = _parse_line(line)
        if parsed:
            rows.append(parsed)
    return rows[-limit:]
