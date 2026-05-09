"""Jellyfin implementation of ``ServiceLifecycle``.

The single Protocol-shaped surface the orchestrator calls. Internally
delegates to ``infrastructure.jellyfin.*`` so the bootstrap-phase
ensurers (which call the same underlying functions directly) and the
orchestrator share one code path for minting / discovering the API
key.

Method-by-method delegation:

  * ``probe_running``    → ``GET /System/Info/Public`` (cheap, public,
                            no auth needed).
  * ``probe_has_api_key``→ env var first, then ``discover_api_key``.
                            Doesn't call into Jellyfin — pure
                            inspection of what the controller already
                            knows.
  * ``mint_api_key``     → ``infrastructure.jellyfin.http_preflight.
                            run_preflight``. The preflight is itself
                            idempotent — if a key for ``app=media-
                            stack-controller`` already exists it
                            returns it without re-creating.
  * ``discover_api_key`` → ``infrastructure.jellyfin.api_key_db.
                            read_jellyfin_api_key_from_db`` (the
                            canonical SQLite reader with name-
                            preference matching) with env-var
                            short-circuit.
  * ``persist_api_key``  → ``os.environ`` write + best-effort k8s
                            secret patch. Failure of the secret patch
                            returns ``Outcome.failure(transient=True)``
                            so the auto-heal cycle retries.

The lifecycle MUST NOT cache state — every call is a fresh evaluation
against the current world. The orchestrator owns retry + cooldown.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from typing import ClassVar

from media_stack.adapters.jellyfin.libraries_wiring import (
    JellyfinLibrariesWirer,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)
from media_stack.domain.services.jellyfin_lifecycle_api_key_helpers import (
    JellyfinLifecycleApiKeyHelpers,
)


logger = logging.getLogger(__name__)


_DEFAULT_HEALTH_PATH = "/System/Info/Public"
_DEFAULT_PROBE_TIMEOUT_SECONDS = 5
_DEFAULT_API_KEY_ENV = "JELLYFIN_API_KEY"
_DEFAULT_API_KEY_NAME_PREFERENCE = ["Jellyfin", "Jellyseerr"]


# ADR-0005 Phase 5b — the 10th and final wirer. Single-service
# (Jellyfin only) so the wirer takes ``(jellyfin_api_key, ctx)``
# rather than the Servarr family's ``(service_id, arr_api_key, ctx)``
# triple. Stateless module-level singleton; library spec +
# HTTP timeouts constructor-injected for test override. The
# ``jellyfin-libraries`` promise binds via lifecycle dispatch.
_LIBRARIES_WIRER = JellyfinLibrariesWirer()


class JellyfinLifecycle:
    """``ServiceLifecycle`` implementation for Jellyfin.

    Stateless — the constructor takes nothing; per-call config flows
    in through ``OrchestrationContext.config``. This means the same
    instance is reusable across services that share a Jellyfin
    deployment (controller-of-controllers scenarios) without state
    bleed.
    """

    service_id: str = "jellyfin"

    # ADR-0012 Phase B — single configured-instance helper shared with
    # the bazarr/jellyseerr/sabnzbd/*arr family for env/secrets/key
    # resolution; the Jellyfin subclass adds the SQLite-DB extras.
    _API_KEY_HELPERS: ClassVar[JellyfinLifecycleApiKeyHelpers] = (
        JellyfinLifecycleApiKeyHelpers(
            default_api_key_env=_DEFAULT_API_KEY_ENV,
        )
    )

    # --- probes -----------------------------------------------------

    def probe_running(self, ctx: OrchestrationContext) -> ProbeResult:
        url = self._public_info_url(ctx)
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
                if resp.status == 200:
                    return ProbeResult.ok(
                        f"responsive at {url}",
                        evidence={"http_status": 200, "url": url},
                        evaluated_at=ctx.now(),
                    )
                return ProbeResult.failed(
                    f"non-200 from {url}: {resp.status}",
                    evidence={"http_status": resp.status, "url": url},
                    evaluated_at=ctx.now(),
                )
        except urllib.error.HTTPError as exc:
            return ProbeResult.failed(
                f"HTTP {exc.code} from {url}",
                evidence={"http_status": exc.code, "url": url},
                evaluated_at=ctx.now(),
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            # DNS / connection / timeout — distinguish from "verifiably
            # broken" so operators can tell warmup from down.
            return ProbeResult.unknown(
                f"unreachable at {url}: {exc}",
                evidence={"url": url, "error": str(exc)},
                evaluated_at=ctx.now(),
            )

    def probe_has_api_key(self, ctx: OrchestrationContext) -> ProbeResult:
        key = self.discover_api_key(ctx)
        if key:
            return ProbeResult.ok(
                "api key discoverable",
                evidence={
                    "key_length": len(key),
                    "source": self._API_KEY_HELPERS.classify_source(ctx, key),
                },
                evaluated_at=ctx.now(),
            )
        return ProbeResult.failed(
            "no api key in env or jellyfin sqlite db",
            evidence={
                "env_var_checked": self._API_KEY_HELPERS.api_key_env(ctx),
                "db_path": self._API_KEY_HELPERS.api_key_db_path(ctx),
            },
            evaluated_at=ctx.now(),
        )

    # --- discover ---------------------------------------------------

    def discover_api_key(self, ctx: OrchestrationContext) -> str | None:
        env_var = self._API_KEY_HELPERS.api_key_env(ctx)
        env_value = (ctx.secrets.get(env_var) or os.environ.get(env_var) or "").strip()
        if env_value:
            return env_value

        if not self._API_KEY_HELPERS.bool_cfg(
            ctx.config, "auto_discover_api_key_from_db", True,
        ):
            return None

        try:
            from media_stack.infrastructure.jellyfin.api_key_db import (
                read_jellyfin_api_key_from_db,
            )
            token, _source = read_jellyfin_api_key_from_db(
                self._API_KEY_HELPERS.config_root(ctx),
                dict(ctx.config),
                coerce_list=self._API_KEY_HELPERS.coerce_list,
                resolve_path=self._API_KEY_HELPERS.resolve_path,
            )
            return token or None
        except Exception as exc:  # noqa: BLE001
            # The canonical reader raises RuntimeError when the DB
            # isn't there yet, the table is empty, etc. Treat as "key
            # not discoverable right now"; the orchestrator can decide
            # whether to mint.
            logger.debug("discover_api_key: db read failed: %s", exc)
            return None

    # --- mint -------------------------------------------------------

    def mint_api_key(self, ctx: OrchestrationContext) -> Outcome[str]:
        existing = self.discover_api_key(ctx)
        if existing:
            return Outcome.success(
                existing,
                attempts=0,
                elapsed_seconds=0.0,
                evidence={"reason": "already_discoverable"},
            )

        url = self._jellyfin_url(ctx)
        if not url:
            return Outcome.failure(
                "no host/port in config — cannot mint",
                transient=False,
                evidence={"config_keys": sorted(ctx.config.keys())},
            )

        started = ctx.now()
        try:
            from media_stack.infrastructure.jellyfin.http_preflight import run_preflight
            result = run_preflight(jellyfin_url=url, log=lambda m: logger.info(m))
        except Exception as exc:  # noqa: BLE001
            return Outcome.failure(
                f"http_preflight raised: {exc}",
                transient=True,
                attempts=1,
                elapsed_seconds=max(0.0, ctx.now() - started),
                evidence={"url": url, "error": str(exc)},
            )

        minted = (result or {}).get("JELLYFIN_API_KEY", "")
        if not minted:
            return Outcome.failure(
                "preflight returned without an API key",
                transient=True,
                attempts=1,
                elapsed_seconds=max(0.0, ctx.now() - started),
                evidence={
                    "url": url,
                    "result_keys": list((result or {}).keys()),
                },
            )

        return Outcome.success(
            minted,
            attempts=1,
            elapsed_seconds=max(0.0, ctx.now() - started),
            evidence={"url": url, "user_id": (result or {}).get("JELLYFIN_USER_ID", "")},
        )

    # --- persist ----------------------------------------------------

    def persist_api_key(
        self, key: str, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        env_var = self._API_KEY_HELPERS.api_key_env(ctx)
        if not key:
            return Outcome.failure(
                "refusing to persist empty key",
                transient=False,
                evidence={"env_var": env_var},
            )
        os.environ[env_var] = key

        # Best-effort secret patch (k8s only — compose is a no-op).
        # Failure here is transient: the env var alone is enough for
        # the running process; the auto-heal cycle retries the patch.
        try:
            from media_stack.services.apps.core.job_adapters import (
                _persist_preflight_keys_to_secret_safe,
                _stub_state,
            )
            payload: dict[str, str] = {env_var: key}
            secret_result = _persist_preflight_keys_to_secret_safe(
                _stub_state(), payload,
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

    # --- Library wiring (ADR-0005 Phase 5b — the 10th wirer) --------
    #
    # Both methods delegate to ``JellyfinLibrariesWirer`` (in
    # ``libraries_wiring.py``). The lifecycle owns the api-key
    # discovery contract; the wirer owns the
    # ``GET / POST /Library/VirtualFolders`` HTTP shape and the
    # already-configured short-circuit logic.

    def probe_libraries(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return _LIBRARIES_WIRER.probe(self.discover_api_key(ctx), ctx)

    def ensure_libraries(
        self, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        return _LIBRARIES_WIRER.ensure(self.discover_api_key(ctx), ctx)

    # --- helpers ----------------------------------------------------

    def _public_info_url(self, ctx: OrchestrationContext) -> str:
        base = self._jellyfin_url(ctx)
        if not base:
            return ""
        path = ctx.config.get("health_path") or _DEFAULT_HEALTH_PATH
        return f"{base.rstrip('/')}{path}"

    @staticmethod
    def _jellyfin_url(ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}"


# ADR-0012 Phase B — the seven loose module-level helpers
# (``_api_key_env`` / ``_api_key_db_path`` / ``_config_root`` /
# ``_bool_cfg`` / ``_coerce_list`` / ``_resolve_path`` /
# ``_classify_source``) collapsed into the configured-instance
# ``JellyfinLifecycleApiKeyHelpers`` shared with the bazarr /
# jellyseerr / sabnzbd / *arr family. Call sites read as
# ``self._API_KEY_HELPERS.foo(ctx)``; no module-level FunctionDefs
# remain so the OO-discipline ratchet stays at zero for this file.


# Static type-check at import time: a structural mismatch fails here
# instead of at the first orchestrator call.
_check: ServiceLifecycle = JellyfinLifecycle()
del _check


# ADR-0010 Phase 7 — module-level Job-handler aliases the
# ``jellyfin:*`` contract entries reference.
from media_stack.domain.services.lifecycle_handler_adapter import (  # noqa: E402
    LifecycleHandlerAdapter,
)

mint_api_key = LifecycleHandlerAdapter.bind(
    JellyfinLifecycle, "mint_api_key",
)
ensure_libraries = LifecycleHandlerAdapter.bind(
    JellyfinLifecycle, "ensure_libraries",
)


__all__ = [
    "JellyfinLifecycle",
    "mint_api_key",
    "ensure_libraries",
]
