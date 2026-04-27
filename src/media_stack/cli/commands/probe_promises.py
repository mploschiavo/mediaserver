"""Run every promise in .ratchets/promises/promises.yaml against the live stack.

Console-script: ``media-stack-probe-promises`` (after ``pip install``).
Module path: ``python -m media_stack.cli.commands.probe_promises``.

Called by ``bin/verify-fresh-install.sh`` (which handles the optional
wipe + bring-up). Stays runnable on its own for ad-hoc checks:

    media-stack-probe-promises
    media-stack-probe-promises --filter bazarr   # only bazarr-* promises

Exit codes:
    0 — all probes passed
    1 — at least one probe failed
    2 — registry could not be loaded

Output is human-readable (one line per probe + a tally). For machine
output use --json.
"""

from __future__ import annotations

import argparse
from media_stack.core.logging_utils import log_swallowed
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse

# File now lives at src/media_stack/cli/commands/<this>.py — five
# levels under the repo root (was bin/<this>.py — one level when the
# old `parents[1]` calculation was correct).
REPO = Path(__file__).resolve().parents[4]


# --- K8s-mode globals ---------------------------------------------------
#
# When the runner is invoked with ``--k8s --unified``, the same
# ``.ratchets/promises/promises.yaml`` runs against a Kubernetes deployment by
# delegating each probe to the controller pod via ``kubectl exec``.
# Routes that hit ``localhost:<port>`` on compose hit
# ``<service>:<port>`` on K8s (cluster DNS), and file probes read the
# file from the controller's PVC mount.
#
# Without this unified path we'd need two copies of every promise — the
# meta-ratchet's purpose ("one place to assert what the stack
# guarantees") would be lost.
_K8S_UNIFIED = False
_K8S_NAMESPACE = "media-stack"
_K8S_CONTROLLER_POD: str | None = None


def _load_registry() -> dict:
    """Load the unified promises registry (.ratchets/promises/promises.yaml).

    Every promise carries a ``platforms:`` field — ``[compose, k8s]``,
    ``[compose]``, or ``[k8s]``. The runner filters by current platform
    at dispatch time, so one registry covers both runtimes without
    duplication. Consolidated from the earlier two-file split in
    v1.0.169 — the split was hiding promises that were platform-agnostic
    but accidentally written as k8s_exec probes."""
    try:
        import yaml
    except ImportError:
        print("error: PyYAML not installed", file=sys.stderr)
        sys.exit(2)
    path = REPO / ".ratchets" / "promises" / "promises.yaml"
    if not path.is_file():
        print(f"error: registry missing at {path}", file=sys.stderr)
        sys.exit(2)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _service_endpoint(service_id: str) -> tuple[str, int, bool]:
    """Return (host, port, is_tls) for a probe target.

    Compose mode: services published on the host's localhost on each
    service's published port. Gateway pseudo-services hit Envoy at
    localhost:443 / 80.

    K8s unified mode (``--k8s --unified``): services reachable via
    cluster DNS (just the service name; the controller's namespace
    context resolves it). Gateway pseudo-services use the configured
    ``gateway_host`` (the operator's real public hostname) over 443
    so the same promise covers the real public path. Bypassing
    cluster DNS would mean every K8s probe routed through
    127.0.0.1, which would either fail (no service there) or hit
    the wrong process — defeating the unified-registry idea."""
    if service_id == "gateway_https":
        if _K8S_UNIFIED:
            # Inside the cluster the public gateway hostname doesn't
            # resolve (the operator's DNS for ``m.iomio.io`` etc.
            # points at the cluster's PUBLIC IP, which from inside
            # the cluster routes back outward). Send to the envoy
            # Service on its declared port 80 (which targets envoy's
            # 8880 listener) and let the Host header drive vhost
            # selection — same behaviour as a real external request,
            # minus the ingress + TLS hop. We do plain HTTP because
            # envoy's HTTPS listener requires a TLS cert valid for
            # the SNI; the request itself still tests the routing
            # configuration end-to-end.
            return ("envoy", 80, False)
        return ("localhost", 443, True)
    if service_id == "gateway_http":
        if _K8S_UNIFIED:
            return ("envoy", 80, False)
        return ("localhost", 80, False)
    if service_id == "controller":
        if _K8S_UNIFIED:
            return ("media-stack-controller", 9100, False)
        return ("localhost", 9100, False)
    sys.path.insert(0, str(REPO / "src"))
    from media_stack.api.services.registry import SERVICE_MAP
    svc = SERVICE_MAP.get(service_id)
    if not svc:
        raise RuntimeError(f"service id {service_id!r} not in registry")
    port = svc.published_port or svc.port
    if _K8S_UNIFIED:
        # Cluster DNS — the K8s Service name matches the registry id
        # for every app. The controller pod (where probes execute)
        # has DNS resolution inside the namespace so the bare name
        # works without an FQDN.
        return (service_id, int(port), False)
    return ("localhost", int(port), False)


def _api_key_for(service_id: str) -> str:
    """Read the *arr/bazarr API key from its on-disk config.

    Compose: read from the operator's local ``config/`` directory.
    K8s unified: read from inside the controller pod, which has the
    services' config dirs mounted at ``/srv-config/<svc>``."""
    if _K8S_UNIFIED:
        snippet = (
            "import os, sys\n"
            "sys.path.insert(0, '/opt/media-stack/src')\n"
            "from media_stack.api.services.registry import read_api_key_from_file\n"
            "key = read_api_key_from_file(" + repr(service_id) + ", os.environ.get('CONFIG_ROOT', '/srv-config'))\n"
            "print(key or '')\n"
        )
        rc, stdout, _ = _kubectl_exec_python(snippet, timeout=15)
        if rc == 0:
            return stdout.strip()
        return ""
    sys.path.insert(0, str(REPO / "src"))
    from media_stack.api.services.registry import read_api_key_from_file
    config_root = str(REPO / "config")
    return read_api_key_from_file(service_id, config_root) or ""


def _kubectl_exec_python(snippet: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run ``snippet`` as ``python3 -c`` inside the controller pod.
    Returns (exit_code, stdout, stderr). Cached pod name avoids one
    ``kubectl get pod`` per probe."""
    import subprocess
    global _K8S_CONTROLLER_POD
    if not _K8S_CONTROLLER_POD:
        out = subprocess.run(
            ["kubectl", "-n", _K8S_NAMESPACE, "get", "pod",
             "-l", "app=media-stack-controller",
             "-o", "jsonpath={.items[?(@.status.phase=='Running')].metadata.name}"],
            capture_output=True, text=True, timeout=10, check=False)
        names = (out.stdout or "").split()
        if not names:
            return (1, "", "no Running controller pod")
        _K8S_CONTROLLER_POD = names[0]
    cmd = ["kubectl", "-n", _K8S_NAMESPACE, "exec",
           _K8S_CONTROLLER_POD, "-c", "controller", "--",
           "python3", "-c", snippet]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return (out.returncode, out.stdout, out.stderr)
    except subprocess.TimeoutExpired:
        return (1, "", "timeout")
    except Exception as exc:
        return (1, "", str(exc)[:200])


def _http_via_controller(url: str, headers: dict, timeout: int = 15) -> tuple[int, bytes]:
    """Run an HTTP GET INSIDE the controller pod and return
    (status, body). Used in K8s unified mode so probes traverse
    cluster DNS + accept self-signed certs the way the controller
    itself does. Body is base64-encoded over the wire so binary
    content survives the kubectl exec text channel."""
    import json as _json
    snippet = (
        "import sys, json, base64, ssl, socket, http.client\n"
        "from urllib.parse import urlparse\n"
        "p = urlparse(" + repr(url) + ")\n"
        "is_https = p.scheme == 'https'\n"
        "host = p.hostname or ''\n"
        "port = p.port or (443 if is_https else 80)\n"
        "path = p.path + ('?' + p.query if p.query else '')\n"
        "headers = " + _json.dumps(headers or {}) + "\n"
        "headers.setdefault('Host', host)\n"
        "if is_https:\n"
        "    ctx = ssl.create_default_context()\n"
        "    ctx.check_hostname = False\n"
        "    ctx.verify_mode = ssl.CERT_NONE\n"
        "    sock = socket.create_connection((host, port), timeout=" + str(timeout) + ")\n"
        "    sock = ctx.wrap_socket(sock, server_hostname=host)\n"
        "    conn = http.client.HTTPConnection(host, port, timeout=" + str(timeout) + ")\n"
        "    conn.sock = sock\n"
        "else:\n"
        "    conn = http.client.HTTPConnection(host, port, timeout=" + str(timeout) + ")\n"
        "conn.request('GET', path or '/', headers=headers)\n"
        "r = conn.getresponse()\n"
        "body = r.read()\n"
        "print(json.dumps({'status': r.status, 'body': base64.b64encode(body).decode()}))\n"
    )
    rc, stdout, stderr = _kubectl_exec_python(snippet, timeout=timeout + 10)
    if rc != 0:
        raise RuntimeError(f"controller exec failed: {stderr.strip()[:200]}")
    try:
        import base64 as _b64
        payload = _json.loads(stdout.strip().splitlines()[-1])
        return (int(payload["status"]), _b64.b64decode(payload["body"]))
    except Exception as exc:
        raise RuntimeError(f"parse exec result: {exc}; raw={stdout[:200]}") from exc


def _http_open(url: str, headers: dict, timeout: int = 15):
    """GET ``url`` with SNI-aware HTTPS handling. *.media-stack.local
    hostnames resolve to 127.0.0.1 (the gateway) so the test works
    on machines without those entries in /etc/hosts. Self-signed
    certs accepted — the gateway's cert is auto-minted.

    K8s unified mode: the request is executed INSIDE the controller
    pod via kubectl exec. The controller has cluster DNS for service
    hostnames, accepts self-signed certs the same way envoy upstream
    does, and reaches the configured public gateway as a real client
    would. This keeps the same ``.ratchets/promises/promises.yaml`` valid on
    both platforms — the runner adapts the delivery."""
    if _K8S_UNIFIED:
        return _http_via_controller(url, headers, timeout=timeout)
    import http.client
    import socket
    import ssl
    p = urlparse(url)
    is_https = p.scheme == "https"
    host = p.hostname or ""
    port = p.port or (443 if is_https else 80)
    target_host = "127.0.0.1" if host.endswith("media-stack.local") else host
    full_path = p.path + (f"?{p.query}" if p.query else "")
    req_headers = dict(headers)
    req_headers.setdefault("Host", host)
    if is_https:
        # Open the TCP socket to the resolved IP, wrap with TLS
        # using server_hostname=<original host> so SNI matches what
        # Envoy's route config keys on. Bypasses HTTPSConnection's
        # built-in DNS so we don't need /etc/hosts entries.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = socket.create_connection((target_host, port), timeout=timeout)
        sock = ctx.wrap_socket(sock, server_hostname=host)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.sock = sock
    else:
        conn = http.client.HTTPConnection(target_host, port, timeout=timeout)
    conn.request("GET", full_path or "/", headers=req_headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def _http_get_json(url: str, headers: dict, timeout: int = 15):
    status, body = _http_open(url, headers, timeout)
    if status >= 400:
        raise urllib.error.HTTPError(url, status, "", {}, None)
    if not body:
        return None
    return json.loads(body)


def _rewrite_sni_for_k8s(sni: str) -> str:
    """In K8s unified mode, rewrite compose-default SNI hostnames to
    match the operator's actual routing config.

    Compose promises hard-code SNI like ``authelia.media-stack.local``
    or ``apps.media-stack.local`` because that's the compose default.
    On K8s the operator may have set ``gateway_host: m.iomio.io`` and
    ``stack_subdomain: iomio, base_domain: io`` — those compose-style
    hostnames don't resolve. This function maps:
      - ``apps.media-stack.local`` (the default gateway host)
            → configured ``gateway_host``
      - ``<svc>.media-stack.local`` (per-service subdomain)
            → ``<svc>.<stack_subdomain>.<base_domain>``
    Other SNI values pass through unchanged."""
    if not _K8S_UNIFIED or not sni or "media-stack.local" not in sni:
        return sni
    routing = _resolve_routing_vars()
    gateway_host = routing.get("gateway_host", "")
    stack_subdomain = routing.get("stack_subdomain", "")
    base_domain = routing.get("base_domain", "")
    # Default-gateway alias → configured gateway.
    if sni == "apps.media-stack.local" and gateway_host:
        return gateway_host
    # Per-service subdomain → swap the suffix.
    if sni.endswith(".media-stack.local") and stack_subdomain and base_domain:
        prefix = sni[: -len(".media-stack.local")]
        return f"{prefix}.{stack_subdomain}.{base_domain}"
    return sni


def _build_url_and_headers(probe: dict) -> tuple[str, dict, str | None]:
    """Resolve a probe's URL + auth headers. Returns (url, headers, error)."""
    service = probe.get("service") or ""
    path = probe.get("path") or "/"
    auth = probe.get("auth") or "none"
    sni = _rewrite_sni_for_k8s((probe.get("sni") or "").strip())
    try:
        host, port, is_tls = _service_endpoint(service)
    except Exception as exc:
        return ("", {}, f"endpoint resolve failed: {exc}")
    headers: dict[str, str] = {"Accept": "application/json, text/plain, */*"}
    scheme = "https" if is_tls else "http"
    # When SNI is set, the URL should use the SNI hostname (so the
    # request's Host header + TLS SNI match what Envoy's route
    # config expects) but RESOLVE to the host:port we actually want.
    # Plain urllib doesn't override DNS resolution per-request, so
    # use a custom https handler that forces the connect target.
    if sni:
        if _K8S_UNIFIED:
            # In K8s unified mode the URL connects to the resolved
            # cluster service (envoy or per-service) but the Host
            # header carries the SNI so Envoy routes by host name
            # the way external traffic does. Without this the URL
            # would point at the SNI hostname (e.g. m.iomio.io)
            # which doesn't resolve from inside the cluster.
            url = f"{scheme}://{host}:{port}{path}"
            headers["Host"] = sni
        else:
            url = f"{scheme}://{sni}:{port}{path}"
            headers["Host"] = sni
    else:
        url = f"{scheme}://{host}:{port}{path}"
    if auth == "api_key":
        key = _api_key_for(service)
        if not key:
            return ("", {}, f"api key for {service!r} not discoverable")
        headers["X-Api-Key"] = key
    elif auth == "jellyfin_key":
        # Jellyfin stores its key in SQLite, not the file-format
        # readers — read it directly from the on-disk DB the same
        # way the controller's discover_api_keys does.
        try:
            import sqlite3
            db = REPO / "config" / "jellyfin" / "data" / "jellyfin.db"
            row = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
                "SELECT AccessToken FROM ApiKeys LIMIT 1"
            ).fetchone()
            if not row or not row[0]:
                return ("", {}, "jellyfin api key not found in db")
            sep = "&" if "?" in path else "?"
            url = f"{url}{sep}api_key={row[0]}"
        except Exception as exc:
            return ("", {}, f"jellyfin db read failed: {exc}")
    elif auth == "controller_basic":
        # Basic-auth as the seeded stack admin — same flow the
        # dashboard uses on first load before token-cookie auth.
        import base64 as _b64
        import os as _os
        user = _os.environ.get("STACK_ADMIN_USERNAME", "admin")
        pwd = _os.environ.get("STACK_ADMIN_PASSWORD", "admin")
        token = _b64.b64encode(f"{user}:{pwd}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    elif auth == "qbit_basic":
        # qBit's WebUI accepts HTTP Basic auth on the API endpoints
        # when the WebUI's "Bypass authentication for clients on
        # localhost" isn't in play. Set the header explicitly —
        # urllib doesn't pull credentials out of a user:pass@host
        # URL on its own.
        import base64 as _b64
        import os as _os
        user = _os.environ.get("QBIT_USERNAME", "admin")
        pwd = _os.environ.get("QBIT_PASSWORD", "adminadmin")
        token = _b64.b64encode(f"{user}:{pwd}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    return (url, headers, None)


def _probe_http_json(probe: dict) -> tuple[bool, str]:
    url, headers, err = _build_url_and_headers(probe)
    if err:
        return (False, err)
    try:
        response = _http_get_json(url, headers)
    except urllib.error.HTTPError as exc:
        return (False, f"HTTP {exc.code} from {url}")
    except Exception as exc:
        return (False, f"GET {url} failed: {exc}")
    return _evaluate(probe.get("assert", ""), {"response": response})


def _probe_http_status(probe: dict) -> tuple[bool, str]:
    """Probe that exposes the HTTP status + response headers in the
    assertion scope (no body). Used for redirect/auth-challenge
    checks where the body is irrelevant or empty."""
    url, headers, err = _build_url_and_headers(probe)
    if err:
        return (False, err)
    if _K8S_UNIFIED:
        # Controller-side execution: status is what we need, but the
        # exec snippet returns body too — discard it. Headers via the
        # exec channel would mean expanding the snippet; for now we
        # expose status only and treat ``headers`` as empty {}.
        try:
            status, _body = _http_via_controller(url, headers)
            return _evaluate(probe.get("assert", ""), {"status": status, "headers": {}})
        except Exception as exc:
            return (False, f"GET {url} failed: {exc}")
    try:
        # Don't follow redirects — we want to ASSERT on the redirect.
        import http.client
        import ssl
        import socket
        p = urlparse(url)
        is_https = p.scheme == "https"
        host = p.hostname or ""
        port = p.port or (443 if is_https else 80)
        target_host = "127.0.0.1" if host.endswith("media-stack.local") else host
        if is_https:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = socket.create_connection((target_host, port), timeout=15)
            sock = ctx.wrap_socket(sock, server_hostname=host)
            conn = http.client.HTTPConnection(host, port, timeout=15)
            conn.sock = sock
        else:
            conn = http.client.HTTPConnection(target_host, port, timeout=15)
        path = p.path + (f"?{p.query}" if p.query else "")
        req_headers = dict(headers)
        req_headers.setdefault("Host", host)
        conn.request("GET", path or "/", headers=req_headers)
        resp = conn.getresponse()
        status = resp.status
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
    except Exception as exc:
        return (False, f"GET {url} failed: {exc}")
    return _evaluate(probe.get("assert", ""), {
        "status": status, "headers": resp_headers,
    })


def _probe_http_text(probe: dict) -> tuple[bool, str]:
    """Like http_json but exposes the raw response body as ``data``.
    Used when the endpoint returns non-JSON or when the assertion
    just needs substring/regex checks."""
    url, headers, err = _build_url_and_headers(probe)
    if err:
        return (False, err)
    try:
        status, body = _http_open(url, headers)
        data = body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return (False, f"HTTP {exc.code} from {url}")
    except Exception as exc:
        return (False, f"GET {url} failed: {exc}")
    if status >= 400:
        return (False, f"HTTP {status} from {url}")
    return _evaluate(probe.get("assert", ""), {"data": data})


def _read_file_via_controller(rel_path: str) -> tuple[bool, str, str]:
    """Read ``${CONFIG_ROOT}/<rel_path>`` from inside the controller
    pod. Used in K8s unified mode where files don't live on the
    operator's local disk. Returns (ok, data, err)."""
    snippet = (
        "from pathlib import Path\n"
        "import os, sys, base64\n"
        "rel = " + repr(rel_path) + "\n"
        "p = Path(os.environ.get('CONFIG_ROOT', '/srv-config')) / rel\n"
        "if not p.is_file():\n"
        "    print('MISSING'); sys.exit(0)\n"
        "print('OK:' + base64.b64encode(p.read_bytes()).decode())\n"
    )
    rc, stdout, stderr = _kubectl_exec_python(snippet, timeout=15)
    if rc != 0:
        return (False, "", f"controller exec: {stderr.strip()[:120]}")
    out = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if out == "MISSING":
        return (False, "", "file missing on controller PVC")
    if out.startswith("OK:"):
        try:
            import base64 as _b64
            return (True, _b64.b64decode(out[3:]).decode("utf-8", errors="replace"), "")
        except Exception as exc:
            return (False, "", f"decode: {exc}")
    return (False, "", f"unexpected output: {out[:120]}")


def _probe_file_json(probe: dict) -> tuple[bool, str]:
    rel = probe.get("path") or ""
    if _K8S_UNIFIED:
        ok, text, err = _read_file_via_controller(rel)
        if not ok:
            return (False, err)
        try:
            data = json.loads(text)
        except Exception as exc:
            return (False, f"parse failed: {exc}")
        return _evaluate(probe.get("assert", ""), {"data": data})
    full = REPO / "config" / rel
    if not full.is_file():
        return (False, f"file missing: {full}")
    try:
        data = json.loads(full.read_text(encoding="utf-8"))
    except Exception as exc:
        return (False, f"parse failed: {exc}")
    return _evaluate(probe.get("assert", ""), {"data": data})


def _probe_file_text(probe: dict) -> tuple[bool, str]:
    rel = probe.get("path") or ""
    skip_if_missing = bool(probe.get("skip_if_missing"))
    if _K8S_UNIFIED:
        ok, text, err = _read_file_via_controller(rel)
        if not ok:
            # In unified-k8s mode, "file missing on controller PVC" is
            # the signal we want to honour for skip_if_missing. Other
            # errors (exec failed, parse failed) keep failing loud.
            if skip_if_missing and "file missing" in err:
                return (True, "skipped (file not present)")
            return (False, err)
        return _evaluate(probe.get("assert", ""), {"data": text})
    full = REPO / "config" / rel
    if not full.is_file():
        if skip_if_missing:
            return (True, "skipped (file not present)")
        return (False, f"file missing: {full}")
    try:
        data = full.read_text(encoding="utf-8")
    except Exception as exc:
        return (False, f"read failed: {exc}")
    return _evaluate(probe.get("assert", ""), {"data": data})


_SAFE_BUILTINS = {
    "isinstance": isinstance, "any": any, "all": all, "len": len,
    "set": set, "dict": dict, "list": list, "tuple": tuple,
    "bool": bool, "str": str, "int": int, "float": float,
    "sorted": sorted, "min": min, "max": max,
}


def _evaluate(expr: str, scope: dict) -> tuple[bool, str]:
    """Evaluate the assert expression in a controlled scope.

    YAML ``|`` block scalars in promises.yaml produce multi-line
    strings — Python's ``eval`` only accepts a single expression,
    so we collapse newlines to spaces.

    Subtle Python gotcha: generator/comprehension expressions
    (``all(... for x in ...)``) use their OWN scope and can't see
    names passed via ``eval``'s ``locals`` dict — only ``globals``.
    So the response/data name has to live in the globals dict, not
    locals. Without this, every probe using ``all()``/``any()`` over
    the response fails with ``name 'data' is not defined``."""
    expr = (expr or "").strip().replace("\n", " ")
    if not expr:
        return (False, "empty assert expression")
    globals_dict = {"__builtins__": _SAFE_BUILTINS, **scope}
    try:
        ok = bool(eval(expr, globals_dict))  # noqa: S307
    except Exception as exc:
        return (False, f"assert eval error: {exc}")
    if not ok:
        return (False, "assert returned False")
    return (True, "ok")


def _resolve_routing_vars() -> dict[str, str]:
    """Read routing config from the live controller (K8s mode) so K8s
    probes can substitute ``${gateway_host}`` etc. in their commands.

    Reads via ``api.services.config.get_routing`` (the MERGED view —
    profile YAML + dashboard runtime overrides) rather than the raw
    profile YAML. On K8s the profile is often mounted as a read-only
    ConfigMap that the dashboard can't update; the operator's actual
    routing lives in ``${CONFIG_ROOT}/.controller/routing-overrides.yaml``
    which only ``get_routing()`` knows to merge.

    Before this change, K8s deployments without a profile ConfigMap
    silently failed every gateway-host-aware promise (skipped because
    the probe runner thought gateway_host was unset, even though the
    operator had set it in the dashboard).

    Cached per-process — no need to re-fetch for every probe."""
    if hasattr(_resolve_routing_vars, "_cached"):
        return _resolve_routing_vars._cached
    import subprocess
    out: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["kubectl", "-n", "media-stack", "exec",
             "deploy/media-stack-controller", "--",
             "python3", "-c",
             "import json; from media_stack.api.services.config import get_routing; print(json.dumps(get_routing() or {}))"],
            capture_output=True, text=True, timeout=15, check=False)
        if result.returncode == 0:
            try:
                routing = json.loads(result.stdout) or {}
                for key in ("gateway_host", "stack_subdomain", "base_domain",
                            "app_path_prefix"):
                    val = str(routing.get(key) or "").strip()
                    if val:
                        out[key] = val
            except Exception as exc:
                log_swallowed(exc)
    except Exception as exc:
        log_swallowed(exc)
    _resolve_routing_vars._cached = out
    return out


def _probe_k8s_exec(probe: dict) -> tuple[bool, str]:
    """Run ``kubectl exec`` into a pod and evaluate the assertion against
    the command's stdout (exposed as ``data``).

    Probe fields:
        namespace:      ``media-stack`` (required)
        pod_label:      label selector to pick the pod (e.g. ``app=envoy``)
        container:      container name within the pod (optional)
        command:        list[str] passed as the exec command
        skip_if_unset:  optional name of a routing var (e.g. ``gateway_host``).
                        When the var is empty/unset the probe is SKIPPED
                        (treated as pass with a "skipped" message).
        assert:         python expr; ``data`` = stdout

    The ``command`` strings support ``${var}`` substitution from the
    routing config (gateway_host, stack_subdomain, base_domain,
    app_path_prefix). This lets a single promise template stay
    portable across every user's deployment without hardcoding hostnames.
    """
    import subprocess
    namespace = (probe.get("namespace") or "").strip()
    pod_label = (probe.get("pod_label") or "").strip()
    container = (probe.get("container") or "").strip()
    command = probe.get("command") or []
    skip_if_unset = (probe.get("skip_if_unset") or "").strip()
    if not namespace or not pod_label or not command:
        return (False, "k8s_exec missing namespace/pod_label/command")

    routing_vars = _resolve_routing_vars()
    if skip_if_unset and not routing_vars.get(skip_if_unset, "").strip():
        return (True, f"skipped ({skip_if_unset} not configured)")

    # Substitute ${var} placeholders in command strings AND in the
    # assertion expression — so probes can write things like
    # ``'${gateway_host}' == 'apps.media-stack.local'`` and have the
    # comparison evaluate against the real configured host. Without
    # substitution-in-assert, that string compares two literals and
    # is always false, defeating the skip-when-default logic.
    def _sub(s: str) -> str:
        for k, v in routing_vars.items():
            s = s.replace("${" + k + "}", v)
        return s
    resolved_cmd = [_sub(str(p)) for p in command]
    resolved_assert = _sub(str(probe.get("assert", "")))

    # Find a pod matching the label selector.
    try:
        find = subprocess.run(
            ["kubectl", "-n", namespace, "get", "pod",
             "-l", pod_label, "-o",
             "jsonpath={.items[?(@.status.phase=='Running')].metadata.name}"],
            capture_output=True, text=True, timeout=10, check=False)
        if find.returncode != 0:
            return (False, f"kubectl get pod failed: {find.stderr.strip()[:200]}")
        pod_names = find.stdout.split()
        if not pod_names:
            return (False, f"no Running pod matches {pod_label!r}")
        pod_name = pod_names[0]
    except Exception as exc:
        return (False, f"pod lookup failed: {exc}")

    exec_args = ["kubectl", "-n", namespace, "exec", pod_name]
    if container:
        exec_args.extend(["-c", container])
    exec_args.append("--")
    exec_args.extend(resolved_cmd)
    try:
        out = subprocess.run(exec_args, capture_output=True, text=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return (False, "kubectl exec timeout")
    except Exception as exc:
        return (False, f"kubectl exec failed: {exc}")
    # Don't fail the probe on non-zero exit; the assertion has access
    # to stdout and can decide what counts as a pass.
    return _evaluate(resolved_assert, {"data": out.stdout})


def _probe_k8s_resource(probe: dict) -> tuple[bool, str]:
    """Probe a Kubernetes resource via ``kubectl get -o json``.

    Probe fields:
        kind:           pvc | pv | pod | deployment | service | ingress | secret
        namespace:      ``media-stack`` for namespaced kinds (omit for cluster-scoped)
        label_selector: optional, e.g. ``app=authelia``
        name_pattern:   optional substring to filter resource names
        assert:         python expr; ``resources`` = list of items

    The runner shells out to kubectl with the user's current context.
    Doesn't authenticate via in-cluster service accounts (that path
    is handled by a different probe runner running INSIDE the
    cluster). Default verifier flow runs locally against the
    operator's kubeconfig."""
    import subprocess
    kind = probe.get("kind") or ""
    namespace = (probe.get("namespace") or "").strip()
    label_selector = (probe.get("label_selector") or "").strip()
    name_pattern = (probe.get("name_pattern") or "").strip()
    if not kind:
        return (False, "k8s_resource probe missing 'kind'")
    cmd = ["kubectl", "get", kind, "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    if label_selector:
        cmd.extend(["-l", label_selector])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except FileNotFoundError:
        return (False, "kubectl not found on PATH")
    except subprocess.TimeoutExpired:
        return (False, f"kubectl timeout: {' '.join(cmd)}")
    if out.returncode != 0:
        return (False, f"kubectl error: {out.stderr.strip()[:200]}")
    try:
        payload = json.loads(out.stdout)
    except Exception as exc:
        return (False, f"json parse failed: {exc}")
    resources = payload.get("items") or []
    if name_pattern:
        resources = [
            r for r in resources
            if name_pattern in (r.get("metadata", {}).get("name") or "")
        ]
    return _evaluate(probe.get("assert", ""), {"resources": resources})


_PROBE_DISPATCH = {
    "http_json": _probe_http_json,
    "http_text": _probe_http_text,
    "http_status": _probe_http_status,
    "file_json": _probe_file_json,
    "file_text": _probe_file_text,
    "k8s_resource": _probe_k8s_resource,
    "k8s_exec": _probe_k8s_exec,
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--filter", default="", help="substring of promise id to include")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--k8s", action="store_true",
                   help="run the registry against a K8s cluster (k8s_resource/k8s_exec "
                        "probes run natively; http/file probes route through kubectl exec "
                        "into the controller pod so the same promise holds on both runtimes)")
    p.add_argument("--unified", action="store_true",
                   help="deprecated — implied by --k8s (kept for wrapper-script compatibility)")
    p.add_argument("--compose-file")  # accepted for parity with the wrapper
    p.add_argument("--controller-url")
    p.add_argument("--admin-user")
    p.add_argument("--admin-pass")
    args = p.parse_args()

    # --k8s always implies unified: there's one registry, and http/file
    # probes on a k8s target need the controller-exec adapter to work.
    global _K8S_UNIFIED
    _K8S_UNIFIED = bool(args.k8s)

    reg = _load_registry()
    promises = reg.get("promises") or []
    if args.filter:
        promises = [pr for pr in promises if args.filter in (pr.get("id") or "")]
    if not promises:
        print("no promises matched filter", file=sys.stderr)
        return 2

    # Platform scope: every promise declares platforms. The runner
    # dispatches only the promises whose platforms include the current
    # runtime; everything else is a fast "skipped" pass so the tally
    # shows what actually ran.
    current_platform = "k8s" if args.k8s else "compose"
    results = []
    for promise in promises:
        pid = promise.get("id", "<no-id>")
        platforms = promise.get("platforms") or []
        if current_platform not in platforms:
            results.append((pid, True, f"skipped (platforms={platforms})"))
            continue
        probe = promise.get("probe") or {}
        ptype = probe.get("type")
        fn = _PROBE_DISPATCH.get(ptype)
        if not fn:
            results.append((pid, False, f"unknown probe type {ptype!r}"))
            continue
        ok, msg = fn(probe)
        results.append((pid, ok, msg))

    if args.json:
        print(json.dumps([
            {"id": pid, "ok": ok, "msg": msg} for pid, ok, msg in results
        ], indent=2))
    else:
        passed = sum(1 for _, ok, _ in results if ok)
        for pid, ok, msg in results:
            mark = "PASS" if ok else "FAIL"
            print(f"  [{mark}] {pid:<40} {msg}")
        print(f"\n{passed}/{len(results)} promises pass")

    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
