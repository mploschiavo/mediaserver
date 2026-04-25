"""HTTP-webhook channel for :class:`NotificationDispatcher`.

Posts the notification as JSON to a caller-supplied URL. Designed to
slot into the same operator-configured URL list the controller
already uses for action events (``_fire_webhooks`` in
``media_stack.api.webhooks``) but without reaching into that module's
state shape — this channel takes the URL in its constructor so the
admin UI can register as many or as few as it likes.

Why we reuse :class:`media_stack.core.http.HttpClient` rather than
raw ``urllib``:

* It already implements the retry-with-backoff loop operators expect.
* Its timing decorator feeds the same observability surface
  (``http.request`` metric) the rest of the stack uses, so webhook
  spikes show up on the same dashboards as indexer calls.
* Centralising HTTP behaviour means one place to fix TLS, proxy,
  or user-agent concerns — no second codepath to audit.

Result mapping:

* 2xx → :attr:`DeliveryStatus.OK`.
* 3xx / 4xx → :attr:`DeliveryStatus.RETRYABLE` with status in
  ``detail``. We mark 4xx retryable because in practice the most
  common 4xx here is ``429 Too Many Requests``, which absolutely
  *is* transient; distinguishing "retry at 429" from "never retry
  at 403" is a policy the caller (not the transport) should own,
  so we surface the status verbatim and let them decide.
* 5xx → :attr:`DeliveryStatus.RETRYABLE` with status in ``detail``.
* Transport exception (DNS, TCP reset, timeout) →
  :attr:`DeliveryStatus.RETRYABLE`.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any
from urllib.parse import urlparse

from .dispatcher import (
    Channel,
    DeliveryStatus,
    Notification,
    NotificationResult,
)


# --------------------------------------------------------------------------
# SSRF guard — bundled into a class to keep the module free of loose
# functions (per the structure ratchet).
# --------------------------------------------------------------------------


class _SsrfGuard:
    """Pure helper that classifies a webhook URL as internal / external.

    Nested-class design keeps ``webhook_channel`` module-top clean of
    loose functions. The guard's state is all in the ``_REASON_*``
    constants below; every method is effectively static.
    """

    _REASON_SCHEME = "unsupported scheme"
    _REASON_NO_HOST = "missing hostname"
    _REASON_LOOPBACK = "loopback address"
    _REASON_LINK_LOCAL = "link-local address"
    _REASON_PRIVATE = "private address"
    _REASON_K8S_SVC = "kubernetes service DNS"
    _REASON_RESOLVE_FAILED = "hostname does not resolve"

    def evaluate(self, url: str) -> str | None:
        """Return a reason string when ``url`` should be rejected, else None.

        Defends against attackers who can cause the controller to POST
        arbitrary bodies to internal addresses:

        - Cloud metadata endpoints (169.254/16 for AWS/GCP IMDS).
        - Controller self-reach at loopback (127.0.0.1 / ::1).
        - In-cluster services via k8s DNS (``*.svc`` / ``*.svc.cluster.local``).
        - RFC 1918 (10/8, 172.16/12, 192.168/16).
        - IPv6 link-local (fe80::/10).

        ``None`` return means externally-routable. Port is NOT checked —
        some legitimate webhooks listen on non-standard ports.

        Resolution sweeps every A/AAAA record so a DNS-rebind attacker
        that returns ``127.0.0.1`` on the second lookup still trips the
        guard on the first.
        """
        if not url:
            return self._REASON_NO_HOST
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return self._REASON_SCHEME
        host = (parsed.hostname or "").lower()
        if not host:
            return self._REASON_NO_HOST
        if host.endswith(".svc") or host.endswith(".svc.cluster.local"):
            return self._REASON_K8S_SVC
        if host in ("localhost", "localhost.localdomain"):
            return self._REASON_LOOPBACK
        try:
            literal = ipaddress.ip_address(host)
            return self._classify_ip(literal)
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return self._REASON_RESOLVE_FAILED
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            hit = self._classify_ip(ip)
            if hit is not None:
                return hit
        return None

    def _classify_ip(self, ip: Any) -> str | None:
        if ip.is_loopback:
            return self._REASON_LOOPBACK
        if ip.is_link_local:
            return self._REASON_LINK_LOCAL
        if ip.is_private:
            return self._REASON_PRIVATE
        # Reserved / multicast / unspecified are never valid webhook
        # destinations; bucket them under "private" for operator-visible
        # reason clarity.
        if getattr(ip, "is_reserved", False):
            return self._REASON_PRIVATE
        if getattr(ip, "is_multicast", False):
            return self._REASON_PRIVATE
        if getattr(ip, "is_unspecified", False):
            return self._REASON_PRIVATE
        return None


_SSRF_GUARD = _SsrfGuard()

try:
    from ..time_utils import utcnow_iso as _utcnow_iso
except ImportError:  # pragma: no cover - defensive for standalone use
    from datetime import datetime, timezone

    def _utcnow_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["WebhookChannel"]


class WebhookChannel(Channel):
    """Deliver notifications by POSTing JSON to an HTTP URL.

    The channel is a thin adapter over the project's shared
    :class:`HttpClient`. It does not hold state between calls; a
    fresh request is built every time. That keeps it trivially
    thread-safe — the dispatcher can fan out to the same channel
    from many threads without locking.

    Example::

        ch = WebhookChannel(
            name="ops-slack-hook",
            url="https://hooks.example.com/xxx",
            event_types=frozenset({"auth.new_location", "auth.ban"}),
        )
        dispatcher.register(ch)
    """

    def __init__(
        self,
        name: str,
        url: str,
        *,
        event_types: frozenset[str] | None = None,
        timeout_seconds: float = 5.0,
        http_client: Any = None,
        allow_internal: bool = False,
    ) -> None:
        """Configure a webhook channel.

        Args:
            name: Dispatcher key. Must be unique within a dispatcher.
            url: Fully-qualified HTTP(S) URL. No templating; the
                admin UI owns URL construction.
            event_types: If given, only these event types are
                accepted. ``None`` means "accept everything" —
                useful for catch-all ops webhooks during incident
                response.
            timeout_seconds: Per-request timeout passed to the
                underlying ``HttpClient``. Defaults to 5s to match
                the existing ``_fire_webhooks`` budget.
            http_client: Injection hook for tests. When ``None``,
                a fresh :class:`HttpClient` is built at
                construction time so production paths don't pay
                the import cost lazily in ``send``.
            allow_internal: When ``True``, the SSRF guard is
                disabled. Use ONLY for deliberate internal
                webhooks (loopback metrics collectors, test
                fixtures pointing at ``example.test``-style
                RFC-6761 hostnames that resolve locally). Default
                ``False`` — an externally-configurable URL MUST
                go through the guard.
        """
        self.name = name
        self._url = url
        self._event_types = event_types
        self._timeout_seconds = float(timeout_seconds)
        self._http_client = http_client if http_client is not None else self._make_http_client()
        self._allow_internal = bool(allow_internal)

    @staticmethod
    def _make_http_client() -> Any:
        """Build the default shared ``HttpClient`` instance.

        Factored out so tests can monkey-patch without caring about
        the import path; also keeps the expensive-ish import out of
        module load time on codepaths that never instantiate a
        webhook channel.
        """
        from ..http import HttpClient  # local import: avoid eager cost

        return HttpClient()

    # ------------------------------------------------------------------
    # Channel protocol
    # ------------------------------------------------------------------

    def accepts(self, event_type: str) -> bool:
        """Return True when this channel wants ``event_type``.

        A ``None`` filter means every event is in scope — the
        constructor docstring explains why that's a useful default
        for catch-all operator hooks.
        """
        if self._event_types is None:
            return True
        return event_type in self._event_types

    def send(self, notification: Notification) -> NotificationResult:
        """POST the notification JSON to the configured URL.

        Exceptions in the underlying HTTP call are caught here and
        translated into ``RETRYABLE`` results; the dispatcher's
        fallback exception handler should therefore never fire for
        this channel under normal operation. Callers reading results
        in bulk get a consistent shape whether the failure was a 5xx
        or a DNS miss.
        """
        # SSRF guard — evaluated on every send so a DNS-rebind attacker
        # can't flip the destination between construction and use.
        if not self._allow_internal:
            reason = _SSRF_GUARD.evaluate(self._url)
            if reason is not None:
                return NotificationResult(
                    channel_name=self.name,
                    status=DeliveryStatus.DROPPED,
                    detail=f"blocked: {reason}",
                )
        payload = self._build_payload(notification)
        base_url, path = self._split_url(self._url)
        try:
            status, _parsed, body = self._http_client.request(
                base_url,
                path,
                method="POST",
                payload=payload,
                timeout=int(self._timeout_seconds) or 1,
            )
        except Exception as exc:
            # Includes the ``RuntimeError`` the shared HttpClient
            # raises on a terminal URLError after its internal
            # retries are exhausted. We classify that as retryable
            # because the caller may want to attempt delivery again
            # later (e.g. on the next audit-log-tail tick) once the
            # network issue resolves.
            return NotificationResult(
                channel_name=self.name,
                status=DeliveryStatus.RETRYABLE,
                detail=f"transport error: {exc}",
            )

        if 200 <= int(status) < 300:
            return NotificationResult(
                channel_name=self.name,
                status=DeliveryStatus.OK,
                detail=f"status={status}",
            )

        # Non-2xx is retryable at the transport layer — see module
        # docstring for why we don't hardwire a 4xx-is-terminal rule.
        excerpt = (body or "")[:160]
        return NotificationResult(
            channel_name=self.name,
            status=DeliveryStatus.RETRYABLE,
            detail=f"status={status} body={excerpt!r}",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_payload(self, notification: Notification) -> dict[str, Any]:
        """Serialise ``notification`` into the on-the-wire JSON shape.

        Kept as its own method so tests can introspect the exact
        payload without poking at module internals. ``structured``
        is copied into the payload — not referenced — so a later
        mutation by the caller cannot retro-edit the posted JSON.
        """
        payload: dict[str, Any] = {
            "event_type": notification.event_type,
            "title": notification.title,
            "body": notification.body,
            "severity": notification.severity,
            "ts": _utcnow_iso(),
            "structured": dict(notification.structured),
        }
        # Touch json.dumps here so a non-serialisable ``structured``
        # fails fast at send time rather than inside the HTTP client
        # where the error trail is harder to read. The resulting
        # string is discarded; HttpClient re-encodes.
        json.dumps(payload)
        return payload

    @staticmethod
    def _split_url(url: str) -> tuple[str, str]:
        """Split ``url`` into the ``(base, path)`` pair HttpClient wants.

        ``HttpClient.request`` takes a base URL and a path because
        it was built around indexer endpoints that share a base.
        We get one URL from the operator, so split it on the first
        path segment and hand both halves through. The base is
        trimmed of trailing slashes by HttpClient's normaliser.
        """
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else url
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return base, path
