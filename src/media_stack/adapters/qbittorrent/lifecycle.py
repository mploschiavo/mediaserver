"""qBittorrent implementation of ``ServiceLifecycle``.

qBittorrent's authentication model is awkward to map onto the
ServiceLifecycle Protocol: it doesn't issue a static API key. The
WebUI uses session cookies obtained via ``POST /api/v2/auth/login``
with username/password, and the controller wields the password as
its credential.

So in this adapter the "API key" IS the qBittorrent admin password:
  - ``discover_api_key`` reads ``QBITTORRENT_PASSWORD`` (or whatever
    ``api_key_env`` the contract YAML names) from env/secrets.
  - ``probe_has_api_key`` is the cheap inspection — credential
    present → ok; absent → failed. It does NOT attempt a login;
    that would be redundant with ``probe_running`` and would risk
    rate-limiting.
  - ``mint_api_key`` is idempotent (returns existing if found) and
    fails loudly with ``transient=False`` if the credential is
    missing, because qBit can't generate one — an operator must set
    the password env. The lifecycle MUST NOT silently log-and-OK
    failures; that's the bug class ADR-0003 explicitly retires.
  - ``persist_api_key`` writes env + best-effort k8s secret.

The lifecycle observes only; the actual WebUI password sync runs in
``infrastructure.qbittorrent.http_preflight.run_preflight`` from the
bootstrap-phase ``compose_preflight`` / ``http_preflight`` paths.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from media_stack.adapters.qbittorrent.categories_wiring import (
    CategoriesWirer,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)
from media_stack.domain.services.lifecycle_handler_adapter import (
    LifecycleHandlerAdapter,
)


logger = logging.getLogger(__name__)


_DEFAULT_HEALTH_PATH = "/api/v2/app/version"
_DEFAULT_PROBE_TIMEOUT_SECONDS = 5
_DEFAULT_API_KEY_ENV = "QBITTORRENT_PASSWORD"

# Stateless module-level singleton — the wirer is per-call parameterized
# by ``OrchestrationContext`` (host/port/credentials), so one instance
# handles every qBittorrent invocation. Constructor-injected timeouts +
# default credentials keep the magic-number / os.environ surface in the
# wirer module rather than here. ADR-0005 Phase 3 cutover for
# ``ensure-qbittorrent-categories``.
_CATEGORIES_WIRER = CategoriesWirer()


class QbittorrentLifecycle:
    """``ServiceLifecycle`` for qBittorrent.

    Stateless. The "key" is the WebUI admin password.
    """

    service_id: str = "qbittorrent"

    # --- probes -----------------------------------------------------

    def probe_running(self, ctx: OrchestrationContext) -> ProbeResult:
        url = self._health_url(ctx)
        if not url:
            return ProbeResult.failed(
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
                evaluated_at=ctx.now(),
            )
        try:
            with urllib.request.urlopen(
                url, timeout=_DEFAULT_PROBE_TIMEOUT_SECONDS,
            ) as resp:
                # qBit returns 200 even without auth on /app/version,
                # but a 403 also proves the service is up (auth gate
                # is doing its job). Either is "running".
                if resp.status in (200, 403):
                    return ProbeResult.ok(
                        f"responsive at {url}",
                        evidence={"http_status": resp.status, "url": url},
                        evaluated_at=ctx.now(),
                    )
                return ProbeResult.failed(
                    f"unexpected status from {url}: {resp.status}",
                    evidence={"http_status": resp.status, "url": url},
                    evaluated_at=ctx.now(),
                )
        except urllib.error.HTTPError as exc:
            # 403 is "running, just unauthenticated" — see comment
            # above. Other HTTP errors are "verifiably broken".
            if exc.code == 403:
                return ProbeResult.ok(
                    f"responsive at {url} (403 — auth gate active)",
                    evidence={"http_status": 403, "url": url},
                    evaluated_at=ctx.now(),
                )
            return ProbeResult.failed(
                f"HTTP {exc.code} from {url}",
                evidence={"http_status": exc.code, "url": url},
                evaluated_at=ctx.now(),
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return ProbeResult.unknown(
                f"unreachable at {url}: {exc}",
                evidence={"url": url, "error": str(exc)},
                evaluated_at=ctx.now(),
            )

    def probe_has_api_key(self, ctx: OrchestrationContext) -> ProbeResult:
        key = self.discover_api_key(ctx)
        if key:
            return ProbeResult.ok(
                "qbit credential present in env/secrets",
                evidence={"source": self._classify_source(ctx, key)},
                evaluated_at=ctx.now(),
            )
        return ProbeResult.failed(
            "no qbit credential in env/secrets — operator must set "
            f"{self._api_key_env(ctx)}",
            evidence={"env_var_checked": self._api_key_env(ctx)},
            evaluated_at=ctx.now(),
        )

    # --- discover ---------------------------------------------------

    def discover_api_key(self, ctx: OrchestrationContext) -> str | None:
        env_var = self._api_key_env(ctx)
        value = (ctx.secrets.get(env_var) or os.environ.get(env_var) or "").strip()
        return value or None

    # --- mint -------------------------------------------------------

    def mint_api_key(self, ctx: OrchestrationContext) -> Outcome[str]:
        existing = self.discover_api_key(ctx)
        if existing:
            return Outcome.success(
                existing,
                attempts=0,
                evidence={"reason": "already_discoverable"},
            )
        # qBit can't be "minted" — credential is operator-supplied.
        # Failing loudly is the point: the legacy
        # ensure-qbittorrent-categories job would log a login failure
        # and return status=ok, masking the real problem. Don't.
        return Outcome.failure(
            f"qbit credential not set in env/secrets; operator must "
            f"provide {self._api_key_env(ctx)}",
            transient=False,
            evidence={"env_var": self._api_key_env(ctx)},
        )

    # --- persist ----------------------------------------------------

    def persist_api_key(
        self, key: str, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        env_var = self._api_key_env(ctx)
        if not key:
            return Outcome.failure(
                "refusing to persist empty key",
                transient=False,
                evidence={"env_var": env_var},
            )
        os.environ[env_var] = key
        try:
            from media_stack.services.apps.core.job_adapters import (
                _persist_preflight_keys_to_secret_safe,
                _stub_state,
            )
            secret_result = _persist_preflight_keys_to_secret_safe(
                _stub_state(), {env_var: key},
            )
            return Outcome.success(
                evidence={
                    "env_written": env_var,
                    "secret_status": str(
                        (secret_result or {}).get("status") or secret_result,
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return Outcome.failure(
                f"env written; secret patch failed: {exc}",
                transient=True,
                evidence={"env_written": env_var, "error": str(exc)},
            )

    # --- Credentials wiring (ADR-0013 Phase 2) ----------------------
    #
    # Verifies that the stored ``STACK_ADMIN_USERNAME`` /
    # ``STACK_ADMIN_PASSWORD`` authenticate against qBittorrent's
    # WebUI. Replaces the legacy ``runner.run`` path's inline login
    # check (the one that produced today's compose error
    # ``run-legacy-pipeline: qBittorrent login failed with secret
    # credentials``). Lives on the framework now: orchestrator ticks
    # the promise, the ensurer reports ok / transient / permanent,
    # the cooldown machinery handles backoff.
    #
    # The probe and ensure share the same HTTP-login check; the
    # ensurer adds no side effects (qBittorrent's password rotation
    # itself stays in ``infrastructure.qbittorrent.compose_preflight``
    # because resetting requires container access — docker exec /
    # kubectl exec — which the lifecycle layer does not have. The
    # ensurer's job is to *verify* and surface the failure honestly,
    # so the legacy runner can short-circuit and the operator knows
    # exactly why.

    def probe_credentials_synced(self, ctx: OrchestrationContext) -> ProbeResult:
        """Cheap HTTP check that stored stack-admin creds authenticate.

        ``ok`` if ``POST /api/v2/auth/login`` returns ``Ok.``;
        ``failed`` if the response is non-OK (credentials mismatched);
        ``unknown`` if the WebUI is unreachable (transient).
        """
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ProbeResult.failed(
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
                evaluated_at=ctx.now(),
            )
        username, password = self._stack_admin_creds(ctx)
        if not password:
            return ProbeResult.failed(
                "no STACK_ADMIN_PASSWORD in env/secrets",
                evidence={"username_present": bool(username)},
                evaluated_at=ctx.now(),
            )
        scheme = (ctx.config.get("scheme") or "http").strip()
        login_path = (ctx.config.get("login_path") or "/api/v2/auth/login").strip()
        url = f"{scheme}://{host}:{port}{login_path}"
        body = (
            f"username={urllib.parse.quote(username, safe='')}"
            f"&password={urllib.parse.quote(password, safe='')}"
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": f"{scheme}://{host}:{port}",
                "Referer": f"{scheme}://{host}:{port}/",
                "User-Agent": "media-stack-controller/lifecycle",
            },
        )
        try:
            with urllib.request.urlopen(
                req, timeout=_DEFAULT_PROBE_TIMEOUT_SECONDS,
            ) as resp:
                payload = resp.read().decode("utf-8", errors="replace").strip()
                if resp.status == 200 and payload.startswith("Ok."):
                    return ProbeResult.ok(
                        "stack-admin creds authenticate at qBittorrent",
                        evidence={"url": url},
                        evaluated_at=ctx.now(),
                    )
                return ProbeResult.failed(
                    f"login returned http={resp.status} body={payload[:32]!r}",
                    evidence={"url": url, "http_status": resp.status},
                    evaluated_at=ctx.now(),
                )
        except urllib.error.HTTPError as exc:
            return ProbeResult.failed(
                f"login HTTP {exc.code} — credentials likely mismatched",
                evidence={"url": url, "http_status": exc.code},
                evaluated_at=ctx.now(),
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return ProbeResult.unknown(
                f"qBittorrent WebUI unreachable at {url}: {exc}",
                evidence={"url": url, "error": str(exc)},
                evaluated_at=ctx.now(),
            )

    def ensure_credentials(self, ctx: OrchestrationContext) -> Outcome[None]:
        """Ensure the stored stack-admin credentials authenticate.

        On the framework path this is a verify-only ensurer: the
        actual password rotation is done by
        ``compose_preflight.ensure_compose_torrent_client_credentials``
        because rotating requires container access (docker exec /
        kubectl exec) which the lifecycle layer does not have.

        Outcomes:
        * success — login OK; promise satisfied, orchestrator stops
          ticking until the next reconcile cycle.
        * failure(transient=True) — WebUI unreachable; orchestrator
          retries with backoff.
        * failure(transient=False) — credentials mismatched; the
          operator must run the compose / k8s preflight to reset
          qBittorrent's stored password. Phase 3+ of ADR-0013 will
          fold the rotation into a sibling ensurer that takes a
          container-access port from ``OrchestrationContext.extra``.
        """
        probe = self.probe_credentials_synced(ctx)
        if probe.status == "ok":
            return Outcome.success(
                evidence=dict(probe.evidence or {}),
            )
        # Probe.unknown → transient (network issue), Probe.failed →
        # permanent (cred mismatch). The probe encodes that already.
        return Outcome.failure(
            probe.detail or "credential verification failed",
            transient=(probe.status == "unknown"),
            evidence=dict(probe.evidence or {}),
        )

    def _stack_admin_creds(
        self, ctx: OrchestrationContext,
    ) -> tuple[str, str]:
        """Resolve stack-admin username + password from env/secrets.

        Order: ``ctx.secrets`` first (the orchestrator-injected secrets
        bag), then ``os.environ`` as fallback. Defaults: ``admin`` for
        username, empty password (probe will fail loudly if unset).
        """
        def _read(key: str) -> str:
            return (
                (ctx.secrets.get(key) or "").strip()
                or os.environ.get(key, "").strip()
            )
        return (_read("STACK_ADMIN_USERNAME") or "admin", _read("STACK_ADMIN_PASSWORD"))

    # --- Categories wiring (ADR-0005 Phase 3) -----------------------
    #
    # Both methods delegate to ``CategoriesWirer`` (in
    # ``categories_wiring.py``). The lifecycle owns the credential
    # discovery contract via ``discover_api_key`` (used by the
    # orchestrator pre-bootstrap), the wirer owns the cookie-jar
    # session login + per-category POST shape.

    def probe_categories(self, ctx: OrchestrationContext) -> ProbeResult:
        return _CATEGORIES_WIRER.probe(ctx)

    def ensure_categories(self, ctx: OrchestrationContext) -> Outcome[None]:
        return _CATEGORIES_WIRER.ensure(ctx)

    # --- helpers ----------------------------------------------------

    def _health_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        path = ctx.config.get("health_path") or _DEFAULT_HEALTH_PATH
        return f"{scheme}://{host}:{port}{path}"

    def _api_key_env(self, ctx: OrchestrationContext) -> str:
        """Resolve the env-var name that holds the qBit password.

        Defaults to ``QBITTORRENT_PASSWORD``; overridable per-service
        via the contract YAML's ``api_key_env`` field. Folded onto
        the lifecycle from a loose helper per ADR-0012.
        """
        return (ctx.config.get("api_key_env") or _DEFAULT_API_KEY_ENV).strip()

    def _classify_source(self, ctx: OrchestrationContext, key: str) -> str:
        """Identify which credential store served the discovered key.

        Returns ``"secrets"`` if the value matches the runtime secrets
        bag, ``"env"`` if it matches ``os.environ`` directly, or
        ``"unknown"`` otherwise. Used in ``probe_has_api_key`` evidence
        to make the audit trail explicit.
        """
        env_var = self._api_key_env(ctx)
        if (ctx.secrets.get(env_var) or "").strip() == key:
            return "secrets"
        if os.environ.get(env_var, "").strip() == key:
            return "env"
        return "unknown"


# Module-level singleton + aliases preserve the historical
# ``_api_key_env`` / ``_classify_source`` import surface for any caller
# that imported them by name. ADR-0012 rule 10.
_INSTANCE = QbittorrentLifecycle()
_api_key_env = _INSTANCE._api_key_env
_classify_source = _INSTANCE._classify_source

# ADR-0013 Phase 2 — module-level Job handler bound from the
# ``ensure_credentials`` lifecycle method. The contract entry
# ``"qbittorrent:ensure-credentials"`` in ``contracts/services/
# qbittorrent.yaml`` references this name; the application-layer
# ``_make_lifecycle_wrapper`` translates the Job framework's
# ``JobContext`` into the ``OrchestrationContext`` shape this method
# expects (same mechanism Bazarr / Sonarr / etc. lifecycles use).
ensure_credentials = LifecycleHandlerAdapter.bind(
    QbittorrentLifecycle, "ensure_credentials",
)


# Type-check at import.
_check: ServiceLifecycle = _INSTANCE
del _check


__all__ = ["QbittorrentLifecycle", "ensure_credentials"]
