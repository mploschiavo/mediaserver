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


# Type-check at import.
_check: ServiceLifecycle = _INSTANCE
del _check


__all__ = ["QbittorrentLifecycle"]
