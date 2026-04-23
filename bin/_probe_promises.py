#!/usr/bin/env python3
"""Run every promise in contracts/promises.yaml against the live stack.

Called by ``bin/verify-fresh-install.sh`` (which handles the optional
wipe + bring-up). Stays runnable on its own for ad-hoc checks:

    python3 bin/_probe_promises.py
    python3 bin/_probe_promises.py --filter bazarr      # only bazarr-* promises

Exit codes:
    0 — all probes passed
    1 — at least one probe failed
    2 — registry could not be loaded

Output is human-readable (one line per probe + a tally). For machine
output use --json.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_registry() -> dict:
    try:
        import yaml
    except ImportError:
        print("error: PyYAML not installed", file=sys.stderr)
        sys.exit(2)
    path = REPO / "contracts" / "promises.yaml"
    if not path.is_file():
        print(f"error: registry missing at {path}", file=sys.stderr)
        sys.exit(2)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _service_endpoint(service_id: str) -> tuple[str, int, bool]:
    """Return (host, port, is_tls) for a probe target. Special pseudo-
    service ids let probes target the gateway (Envoy on host:443/80)
    or the controller itself, instead of bypassing it via direct
    ports — the latter masks "Envoy listener broken" regressions."""
    if service_id == "gateway_https":
        return ("localhost", 443, True)
    if service_id == "gateway_http":
        return ("localhost", 80, False)
    if service_id == "controller":
        return ("localhost", 9100, False)
    sys.path.insert(0, str(REPO / "src"))
    from media_stack.api.services.registry import SERVICE_MAP
    svc = SERVICE_MAP.get(service_id)
    if not svc:
        raise RuntimeError(f"service id {service_id!r} not in registry")
    port = svc.published_port or svc.port
    return ("localhost", int(port), False)


def _api_key_for(service_id: str) -> str:
    """Read the *arr/bazarr API key from its on-disk config (same files
    the controller's discover_api_keys consults)."""
    sys.path.insert(0, str(REPO / "src"))
    from media_stack.api.services.registry import read_api_key_from_file
    config_root = str(REPO / "config")
    return read_api_key_from_file(service_id, config_root) or ""


def _http_open(url: str, headers: dict, timeout: int = 15):
    """GET ``url`` with SNI-aware HTTPS handling. *.media-stack.local
    hostnames resolve to 127.0.0.1 (the gateway) so the test works
    on machines without those entries in /etc/hosts. Self-signed
    certs accepted — the gateway's cert is auto-minted."""
    import http.client
    import socket
    import ssl
    from urllib.parse import urlparse
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


def _build_url_and_headers(probe: dict) -> tuple[str, dict, str | None]:
    """Resolve a probe's URL + auth headers. Returns (url, headers, error)."""
    service = probe.get("service") or ""
    path = probe.get("path") or "/"
    auth = probe.get("auth") or "none"
    sni = (probe.get("sni") or "").strip()
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
    try:
        # Don't follow redirects — we want to ASSERT on the redirect.
        from urllib.parse import urlparse
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


def _probe_file_json(probe: dict) -> tuple[bool, str]:
    rel = probe.get("path") or ""
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
    full = REPO / "config" / rel
    if not full.is_file():
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


_PROBE_DISPATCH = {
    "http_json": _probe_http_json,
    "http_text": _probe_http_text,
    "http_status": _probe_http_status,
    "file_json": _probe_file_json,
    "file_text": _probe_file_text,
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--filter", default="", help="substring of promise id to include")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--compose-file")  # accepted for parity with the wrapper
    p.add_argument("--controller-url")
    p.add_argument("--admin-user")
    p.add_argument("--admin-pass")
    args = p.parse_args()

    reg = _load_registry()
    promises = reg.get("promises") or []
    if args.filter:
        promises = [pr for pr in promises if args.filter in (pr.get("id") or "")]
    if not promises:
        print("no promises matched filter", file=sys.stderr)
        return 2

    results = []
    for promise in promises:
        pid = promise.get("id", "<no-id>")
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
