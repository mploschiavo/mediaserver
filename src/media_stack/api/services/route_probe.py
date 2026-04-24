"""Server-side URL probe for the dashboard's "Test All Paths" matrix.

Why this exists
---------------
The dashboard's matrix used to probe routes by calling
``window.fetch(url, {mode: 'no-cors'})`` from the browser. Three
structural problems with that:

  1. **Mixed-content blocker.** When the dashboard is served over
     HTTPS (``https://m.example.com/...``), the browser refuses to
     fire any ``http://...`` probe — every "Localhost"-column row
     went red even though the route worked fine over HTTP.

  2. **Self-signed cert errors.** The stack ships a self-signed cert
     by default; ``fetch`` rejects with ``net::ERR_CERT_*`` and the
     row goes red. The cert IS valid for the deployment — it's just
     not in the browser's trust store.

  3. **Opaque responses.** ``mode: 'no-cors'`` returns an opaque
     response that JS can't introspect — even successful probes are
     indistinguishable from network errors.

Server-side, the controller has waivers for ALL three: it accepts
self-signed (Envoy is upstream of itself), it can hit ``http://``
and ``https://`` freely, and it sees real status codes.

What the dashboard renders
--------------------------
    {
      "url": "<input>",
      "ok": True,                  # 2xx/3xx considered "reachable"
      "status": 302,
      "elapsed_ms": 18,
      "location": "/web/",         # for 3xx redirects
      "error": "",                 # populated when ``ok`` is False
    }

The dashboard treats ``ok=true`` (any 2xx/3xx + 401/403 since auth
challenges count as "the route is alive, the gateway answered") as
green and everything else as red.
"""

from __future__ import annotations

import logging
import socket
import ssl
import time
from http.client import HTTPConnection, HTTPSConnection
from typing import Any
from urllib.parse import urlparse

from media_stack.core.logging_utils import log_swallowed

_log = logging.getLogger("media_stack.route_probe")

_TIMEOUT_SECONDS = 5
_MAX_RESPONSE_BYTES = 8192  # we only need status + headers; don't pull full bodies


def _is_safe_target(parsed) -> tuple[bool, str]:
    """Reject probe targets that point at internal infrastructure
    we shouldn't expose via this endpoint. The dashboard generates
    URLs from routing config (cluster-internal hostnames + the
    operator's own gateway/subdomain), so legitimate targets resolve
    to either the cluster's external IP or to a per-service DNS that
    points at the cluster.

    What we reject:
      - Schemes other than http/https.
      - Cloud metadata endpoints (link-local 169.254.169.254 et al)
        since this endpoint is reachable by any authenticated user
        and we don't want it doubling as an SSRF gadget.
    """
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"unsupported scheme {scheme!r}"
    host = (parsed.hostname or "").lower()
    if host in ("169.254.169.254", "metadata.google.internal", "metadata.aws.amazon.com"):
        return False, "metadata endpoints are not probe-able"
    return True, ""


def probe(url: str) -> dict[str, Any]:
    """Issue a single GET to ``url`` and return reachability info."""
    url = (url or "").strip()
    if not url:
        return {"url": "", "ok": False, "status": 0, "elapsed_ms": 0,
                "location": "", "error": "empty url"}
    try:
        parsed = urlparse(url)
    except Exception as exc:
        return {"url": url, "ok": False, "status": 0, "elapsed_ms": 0,
                "location": "", "error": f"parse: {exc}"}

    safe, reason = _is_safe_target(parsed)
    if not safe:
        return {"url": url, "ok": False, "status": 0, "elapsed_ms": 0,
                "location": "", "error": reason}

    is_https = parsed.scheme == "https"
    host = parsed.hostname or ""
    port = parsed.port or (443 if is_https else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    headers = {
        "Host": host,
        "Accept": "*/*",
        "User-Agent": "media-stack/route-probe",
    }

    start = time.monotonic()
    try:
        if is_https:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            sock = socket.create_connection((host, port), timeout=_TIMEOUT_SECONDS)
            sock = ctx.wrap_socket(sock, server_hostname=host)
            conn = HTTPSConnection(host, port, timeout=_TIMEOUT_SECONDS)
            conn.sock = sock
        else:
            conn = HTTPConnection(host, port, timeout=_TIMEOUT_SECONDS)
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        # Read just enough to free the socket; we don't care about
        # body content for the matrix. Best-effort — body drain failures
        # are benign (connection still closes in the outer scope) but
        # log_swallowed keeps them visible at DEBUG without spamming.
        try:
            resp.read(_MAX_RESPONSE_BYTES)
        except Exception as exc:
            log_swallowed(exc)
        location = resp.getheader("Location") or ""
        try:
            conn.close()
        except Exception as exc:
            log_swallowed(exc)
    except socket.gaierror as exc:
        return {"url": url, "ok": False, "status": 0,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "location": "", "error": f"DNS: {exc}"}
    except (TimeoutError, socket.timeout):
        return {"url": url, "ok": False, "status": 0,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "location": "", "error": "timeout"}
    except ConnectionRefusedError:
        return {"url": url, "ok": False, "status": 0,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "location": "", "error": "connection refused"}
    except Exception as exc:
        return {"url": url, "ok": False, "status": 0,
                "elapsed_ms": int((time.monotonic() - start) * 1000),
                "location": "", "error": str(exc)[:120]}

    elapsed_ms = int((time.monotonic() - start) * 1000)
    # 2xx/3xx + 401/403 all count as "the route is alive". 401/403
    # specifically because Authelia's ext_authz challenge IS evidence
    # the gateway answered — without counting them, every protected
    # service appears red even when routing works perfectly.
    ok = 200 <= status < 400 or status in (401, 403)
    return {
        "url": url,
        "ok": ok,
        "status": status,
        "elapsed_ms": elapsed_ms,
        "location": location,
        "error": "",
    }
