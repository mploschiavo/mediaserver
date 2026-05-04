"""Seed-series wiring for Sonarr.

Lifecycle-method port of the legacy ``ensure_sonarr_seed_series``
job handler (ADR-0005 Phase 3 cutover, wide-handler delegation
addendum). The class lives here rather than inline in
``servarr/lifecycle.py`` so the lifecycle module stays focused on
the core ``ServiceLifecycle`` Protocol surface.

The legacy ``ensure_sonarr_seed_series`` handler in
``services.apps.core.job_adapters`` is wide (~100 LoC of
multi-subsystem orchestration: read seed config from
``contracts/defaults/arr.yaml``, fetch ``qualityprofile`` +
``rootfolder`` + existing series, look up tvdbId per seed name via
Sonarr's ``/series/lookup``, and POST each new series). Following
the Jellyseerr wide-handler pattern, the wirer owns ONLY the
idempotent probe (count series, skip when sufficient) and delegates
ensure-time mutation to the legacy handler via injected
``configure_handler`` + ``job_context_factory`` callables.

``SeedSeriesWirer`` owns:

  * The HTTP shape of ``/app/sonarr/api/v3/series`` (the probe).
  * Idempotent skip semantics: if Sonarr already has at least
    ``_SEED_SERIES_OK_THRESHOLD`` series, the probe is ``ok`` and
    the ensurer short-circuits without invoking the heavyweight
    legacy handler.
  * Tri-state outcome semantics (transient on prereq missing,
    permanent on structural issues, success on probe-already-ok or
    handler completion).
  * Sonarr-only short-circuit: ``ServarrLifecycle`` covers every
    *arr (sonarr/radarr/lidarr/readarr/prowlarr); this promise is
    Sonarr-specific so other service ids return ok / success with
    ``reason=unsupported_service``.

The Servarr lifecycle methods are thin delegators — they discover
the Sonarr api key via ``ServarrLifecycle.discover_api_key`` and
pass it (plus the lazy-imported handler + JobContext factory) into
the wirer. The lifecycle's lazy import goes through the
``services/`` shim path (``services.apps.core.job_adapters``)
NOT ``application/`` so the adapters → application hexagon ratchet
stays clean.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from media_stack.adapters._shared.lifecycle_wirer_base import (
    LifecycleWirerBase,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


# Sonarr is the only *arr that carries a seed-series promise. The
# class-level ServarrLifecycle covers all five *arrs (one Protocol
# instance per service_id); for the others the seed-series wirer's
# methods short-circuit.
_SUPPORTED_SERVICE_ID = "sonarr"
_SONARR_API_VERSION = "v3"
_SERIES_PATH = "series"
# The promise's previous ``http_json`` probe asserted
# ``len(response) >= 5`` against ``/api/v3/series``. Preserving the
# threshold verbatim means the cutover doesn't shift the semantic
# bar — anything that previously passed continues to pass.
_SEED_SERIES_OK_THRESHOLD = 5
_PROBE_TIMEOUT_SECONDS = 5


class SeedSeriesWirer(LifecycleWirerBase):
    """Per-Sonarr seed-series wiring.

    Stateless beyond constructor-injected identity (supported
    service id, OK threshold, probe timeout). Per-call parameters
    are ``service_id`` + arr_api_key + ``OrchestrationContext`` for
    the probe; the ensurer additionally takes the ``configure_handler``
    + ``job_context_factory`` callables (Jellyseerr wide-handler
    delegation pattern) so the heavyweight legacy implementation
    remains the single source of truth for seed-series mutation.
    """

    def __init__(
        self,
        *,
        supported_service_id: str = _SUPPORTED_SERVICE_ID,
        ok_threshold: int = _SEED_SERIES_OK_THRESHOLD,
        probe_timeout_seconds: int = _PROBE_TIMEOUT_SECONDS,
    ) -> None:
        self._supported_service_id = supported_service_id
        self._ok_threshold = ok_threshold
        self._probe_timeout_seconds = probe_timeout_seconds

    # --- probe ------------------------------------------------------

    def probe(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        if service_id != self._supported_service_id:
            return self._probe_ok(
                ctx,
                f"{service_id} doesn't carry a seed-series promise",
                evidence={"reason": "unsupported_service"},
            )
        url = self._series_endpoint(service_id, ctx)
        if not url:
            return self._probe_unknown(
                ctx,
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not arr_api_key:
            return self._probe_unknown(
                ctx,
                "no sonarr api key — cannot probe",
                evidence={"url": url, "service_id": service_id},
            )
        existing = self._list_series(url, arr_api_key)
        if existing is None:
            return self._probe_unknown(
                ctx,
                f"could not list series at {url}",
                evidence={"url": url, "service_id": service_id},
            )
        return self._classify_probe_result(existing, url, ctx)

    # --- ensurer (wide-handler delegation) --------------------------

    def ensure(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
        *,
        configure_handler: Any,
        job_context_factory: Any,
    ) -> Outcome[None]:
        """Delegate to the legacy ``ensure_sonarr_seed_series`` handler.

        ``configure_handler`` is the callable ``(JobContext) -> dict|None``
        — usually
        ``services.apps.core.job_adapters.ensure_sonarr_seed_series``,
        or a stub in tests. ``job_context_factory`` is a callable
        ``() -> JobContext`` (typically ``JobContext`` itself, or a
        stub).

        Idempotent skip when the probe is already ok: the legacy
        handler's own short-circuits cover this too (Sonarr's
        ``/series`` POST is idempotent on tvdbId), but probing
        first avoids spinning up the qualityprofile + rootfolder +
        existing-series GET burst when nothing needs to change.
        """
        if service_id != self._supported_service_id:
            return self._outcome_success(
                evidence={"reason": "unsupported_service"},
            )
        probe = self.probe(service_id, arr_api_key, ctx)
        if probe.is_ok and probe.evidence.get("reason") != "unsupported_service":
            return self._outcome_success(
                evidence={
                    "reason": "already_configured",
                    "series_count": probe.evidence.get("series_count"),
                },
            )
        try:
            job_ctx = job_context_factory()
        except Exception as exc:  # noqa: BLE001
            return self._outcome_transient(
                f"could not build JobContext: {exc}",
                evidence={"error": str(exc), "service_id": service_id},
            )
        try:
            result = configure_handler(job_ctx)
        except Exception as exc:  # noqa: BLE001
            return self._outcome_transient(
                f"ensure_sonarr_seed_series raised: {exc}",
                evidence={"error": str(exc), "service_id": service_id},
            )
        return self._outcome_success(
            evidence={
                "result": result if isinstance(result, dict) else None,
                "service_id": service_id,
            },
        )

    # --- internals --------------------------------------------------

    def _series_endpoint(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        # Sonarr serves under ``/app/sonarr/`` URL base. The bare
        # ``/api/...`` returns a 307 redirect; always go through the
        # prefixed path (matches IndexerPipelineWirer's convention).
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return (
            f"{scheme}://{host}:{port}"
            f"/app/{service_id}/api/{_SONARR_API_VERSION}/{_SERIES_PATH}"
        )

    def _list_series(
        self, url: str, arr_api_key: str,
    ) -> list[Any] | None:
        try:
            req = urllib.request.Request(
                url, headers={"X-Api-Key": arr_api_key},
            )
            with urllib.request.urlopen(
                req, timeout=self._probe_timeout_seconds,
            ) as resp:
                body = resp.read()
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError,
        ):
            return None
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else []

    def _classify_probe_result(
        self,
        existing: list[Any],
        url: str,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        count = len(existing)
        if count >= self._ok_threshold:
            return self._probe_ok(
                ctx,
                f"{count} series configured (>= {self._ok_threshold})",
                evidence={
                    "url": url,
                    "series_count": count,
                    "threshold": self._ok_threshold,
                },
            )
        return self._probe_failed(
            ctx,
            f"{count} series at {url} (< {self._ok_threshold})",
            evidence={
                "url": url,
                "series_count": count,
                "threshold": self._ok_threshold,
            },
        )


__all__ = ["SeedSeriesWirer"]
