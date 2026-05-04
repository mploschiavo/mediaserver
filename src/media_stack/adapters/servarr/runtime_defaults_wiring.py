"""Runtime-defaults wiring for the *arr family.

Lifecycle-method port of the legacy ``apply_arr_runtime_defaults``
job handler (ADR-0005 Phase 3 cutover, three promises sharing one
ensurer per the Bazarr monolithic-handler lesson).

The legacy ``apply_arr_runtime_defaults`` is a monolithic dispatcher:
ONE call walks every *arr in ``arr_apps`` and applies up to four
distinct invariants in a single pass:

  * Radarr quality-profile language → ``Any`` (the
    ``radarr-quality-profiles`` promise's "non-empty + usable
    profile" guarantee — without ``language=Any``, every English
    release ships in profiles whose default rejects them)
  * SAB + delay-profile reconcile against
    ``download_clients.sabnzbd.configure_arr_clients``
  * Per-arr import-list ``enableAuto=True`` (the
    ``radarr-import-lists-auto`` promise; no Sonarr promise binds
    to it but the legacy handler reconciles Sonarr too)
  * Lidarr / Readarr per-quality size-cap + Unknown-Text format

So splitting into N ensurers — one per promise — would mean N
redundant POSTs that each clobber the shared *arr settings the
previous call just patched. ``RuntimeDefaultsWirer`` follows the
Bazarr monolithic-handler decision: ONE shared ``ensure`` method
that delegates to the legacy ``apply_arr_runtime_defaults``
implementation (wide-handler delegation pattern from the Jellyseerr
family — keeps the legacy handler as the source of truth and avoids
re-implementing ~100 LoC of multi-arr orchestration in the wirer).

The wirer owns N distinct probes — one per promise:

  * ``probe_quality_profiles``  — sonarr-quality-profiles +
    radarr-quality-profiles (the same probe shape on
    ``/api/v3/qualityprofile``: at least one profile present)
  * ``probe_import_lists_auto`` — radarr-import-lists-auto only.
    Short-circuits ``ok`` for non-radarr service ids using the
    ``JellyfinNotifierWirer`` unsupported-service pattern
    (sonarr has no import-lists promise; non-radarr lifecycle
    instances would call the method but the dispatcher should
    no-op rather than report ``failed``).

The shared ensurer takes injected ``configure_handler`` +
``job_context_factory`` callables (Jellyseerr lesson) so the
heavyweight legacy implementation stays the source of truth and
the lifecycle method's lazy import goes through the
``services/apps/core`` module the legacy handler already lives in.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

from media_stack.adapters._shared.lifecycle_wirer_base import (
    LifecycleWirerBase,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


# --- HTTP timing -----------------------------------------------------

_PROBE_HTTP_TIMEOUT_SECONDS = 5

# --- *arr quality-profile + import-list paths -----------------------
#
# Each *arr in the family has its own API version; the path shape
# is identical though. ``api_version`` lookup mirrors the existing
# ``IndexerPipelineWirer._ARR_API_VERSIONS`` table.
_ARR_API_VERSIONS: Mapping[str, str] = {
    "sonarr": "v3",
    "radarr": "v3",
    "lidarr": "v1",
    "readarr": "v1",
}

_QUALITY_PROFILE_PATH_FMT = "/api/{ver}/qualityprofile"
_IMPORT_LIST_PATH_FMT = "/api/{ver}/importlist"

# --- Promise-level service support ----------------------------------
#
# sonarr-quality-profiles + radarr-quality-profiles BOTH bind to
# the quality-profile probe. Other *arrs have their own quality
# profiles too but no promise asserts the count.
_QUALITY_PROFILE_SUPPORTED: frozenset[str] = frozenset({"sonarr", "radarr"})

# Only radarr-import-lists-auto exists today. The wirer's
# ``probe_import_lists_auto`` must short-circuit ``ok`` for sonarr
# (its lifecycle instance won't actually call it because no Sonarr
# promise binds to import-lists, but the wirer guards anyway —
# pattern lifted from ``JellyfinNotifierWirer``: services not in
# the supported set return ``ok`` with ``reason: unsupported_service``
# rather than fabricating failure).
_IMPORT_LISTS_AUTO_SUPPORTED: frozenset[str] = frozenset({"radarr"})


class RuntimeDefaultsWirer(LifecycleWirerBase):
    """Per-*arr runtime-defaults wiring.

    Constructor-injected ``configure_handler`` + ``job_context_factory``
    so the heavyweight legacy ``apply_arr_runtime_defaults`` stays the
    source of truth (wide-handler delegation pattern). Per-call
    ``service_id`` + arr_api_key + ``OrchestrationContext`` for the
    probes.

    Stateless — the same instance handles every *arr.
    """

    def __init__(
        self,
        *,
        configure_handler: Callable[[Any], Mapping[str, Any]] | None = None,
        job_context_factory: Callable[[], Any] | None = None,
        probe_timeout_seconds: int = _PROBE_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        # Lazy default: the legacy handler + JobContext live in
        # ``services/apps/core/job_adapters`` and ``application/jobs/
        # framework`` respectively. Importing eagerly here would pull
        # the controller-side runtime through the adapter import
        # boundary and trip the hexagon ratchet — defer until first
        # call. Tests inject test doubles via the constructor.
        self._configure_handler_override = configure_handler
        self._job_context_factory_override = job_context_factory
        self._probe_timeout_seconds = probe_timeout_seconds

    # === Probes =========================================================
    #
    # Two distinct probes — one per promise binding. Both share the
    # prereq guard helper so the public methods stay narrow.

    def probe_quality_profiles(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        """Probe ``/api/<ver>/qualityprofile`` — promise asserts the
        list is non-empty (at least one usable profile so a fresh
        install can grab content). Maps to sonarr-quality-profiles
        and radarr-quality-profiles."""
        guard = self._guard_probe_prereqs(
            service_id, arr_api_key, ctx, _QUALITY_PROFILE_SUPPORTED,
        )
        if guard is not None:
            return guard
        url = self._quality_profile_url(service_id, ctx)
        body = self._http_get_json(url, arr_api_key or "")
        if body is None:
            return self._probe_unknown(
                ctx,
                f"could not list quality profiles at {url}",
                evidence={"url": url, "service_id": service_id},
            )
        if isinstance(body, list) and body:
            return self._probe_ok(
                ctx,
                f"{len(body)} quality profile(s) configured",
                evidence={"url": url, "profile_count": len(body)},
            )
        return self._probe_failed(
            ctx,
            f"no quality profiles at {url}",
            evidence={"url": url, "profile_count": 0},
        )

    def probe_import_lists_auto(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        """Probe ``/api/v3/importlist`` — promise asserts every enabled
        import list has ``enableAuto=True`` so seeded TMDb/Trakt lists
        actually fetch on a fresh deploy. Radarr-only.

        Sonarr has no import-lists promise, so calls from a sonarr
        lifecycle instance short-circuit ``ok`` (unsupported-service
        pattern from ``JellyfinNotifierWirer``).

        Body of the probe is in ``_classify_import_lists`` — keeps the
        public method narrow."""
        guard = self._guard_probe_prereqs(
            service_id, arr_api_key, ctx, _IMPORT_LISTS_AUTO_SUPPORTED,
        )
        if guard is not None:
            return guard
        url = self._import_list_url(service_id, ctx)
        body = self._http_get_json(url, arr_api_key or "")
        return self._classify_import_lists(body, url, service_id, ctx)

    def _classify_import_lists(
        self,
        body: Any,
        url: str,
        service_id: str,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        """Map the import-list GET response to a tri-state ProbeResult.

        Pre-flight (unreachable / wrong-shape / empty list) is
        handled here; once we have a non-empty list of dicts,
        classification of enabled-vs-auto-flag invariants is
        delegated to ``_classify_import_list_auto_state``."""
        if body is None:
            return self._probe_unknown(
                ctx,
                f"could not list import lists at {url}",
                evidence={"url": url, "service_id": service_id},
            )
        if not isinstance(body, list):
            return self._probe_unknown(
                ctx,
                f"unexpected import-list shape at {url}",
                evidence={"url": url, "service_id": service_id},
            )
        # Empty list ⇒ failed (fresh install hasn't seeded any lists
        # yet OR the seed handler hasn't run; the ensurer ultimately
        # depends on the seed-arr-import-lists job populating this
        # endpoint, but the promise asserts presence + auto-on, so
        # zero lists is the failed state).
        if not body:
            return self._probe_failed(
                ctx,
                f"no import lists configured at {url}",
                evidence={"url": url, "import_list_count": 0},
            )
        return self._classify_import_list_auto_state(body, url, ctx)

    def _classify_import_list_auto_state(
        self,
        body: list[Any],
        url: str,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        """The promise's assert: every ENABLED list has
        ``enableAuto=True``. Disabled lists are operator-intent and
        left alone — matching the legacy
        ``patch_arr_import_lists_auto`` behaviour."""
        enabled = [il for il in body if il.get("enabled")]
        if not enabled:
            return self._probe_failed(
                ctx,
                f"no enabled import lists at {url}",
                evidence={
                    "url": url,
                    "import_list_count": len(body),
                    "enabled_count": 0,
                },
            )
        missing_auto = [
            (il.get("name") or "?") for il in enabled
            if il.get("enableAuto") is not True
        ]
        if missing_auto:
            return self._probe_failed(
                ctx,
                f"{len(missing_auto)} enabled import list(s) missing "
                f"enableAuto=True at {url}",
                evidence={
                    "url": url,
                    "enabled_count": len(enabled),
                    "missing_auto": missing_auto,
                },
            )
        return self._probe_ok(
            ctx,
            f"all {len(enabled)} enabled import lists have enableAuto=True",
            evidence={
                "url": url,
                "enabled_count": len(enabled),
            },
        )

    # === Ensurer =======================================================
    #
    # ONE shared ensurer for all three promises (rationale at module
    # top). Delegates to the legacy ``apply_arr_runtime_defaults``
    # handler via injected callables so the wirer stays narrow and
    # the legacy implementation remains the source of truth.

    def ensure_runtime_defaults(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> Outcome[None]:
        """Apply runtime defaults to the *arr family.

        Delegates to the legacy ``apply_arr_runtime_defaults`` handler
        which iterates internally over every configured *arr — calling
        it once from any of the three promises' ensurer dispatch is
        sufficient (the second + third call see ``updated: {}`` with
        no work done). The ``service_id`` + arr_api_key arguments are
        accepted for protocol-uniformity with the other wirers but
        the legacy handler doesn't need them — it discovers per-arr
        keys from ``ctx.api_key()`` itself.
        """
        configure_handler, job_context_factory = self._resolve_dependencies()
        if configure_handler is None or job_context_factory is None:
            return self._outcome_permanent(
                "runtime-defaults handler unavailable — could not "
                "import legacy apply_arr_runtime_defaults",
                evidence={"service_id": service_id},
            )
        try:
            job_ctx = job_context_factory()
            summary = configure_handler(job_ctx)
        except Exception as exc:  # noqa: BLE001
            return self._outcome_transient(
                f"runtime-defaults handler raised: {exc}",
                evidence={"service_id": service_id, "error": str(exc)},
            )
        # Legacy handler returns a dict of action + per-arr counts;
        # surface in evidence for the run-history audit trail.
        evidence: dict[str, Any] = {"service_id": service_id}
        if isinstance(summary, Mapping):
            evidence["summary"] = dict(summary)
        return self._outcome_success(evidence=evidence)

    # === Probe helpers ================================================

    def _guard_probe_prereqs(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
        supported_services: frozenset[str],
    ) -> ProbeResult | None:
        """Return a short-circuit ProbeResult when service_id /
        config / api-key prereqs aren't met; ``None`` when the probe
        body should run."""
        if service_id not in supported_services:
            # Unsupported-service short-circuit — ok with reason so
            # the orchestrator records "no signal here" rather than
            # bucketing into drift/broken (per the
            # unknown-as-actionable bug-class memo).
            return self._probe_ok(
                ctx,
                f"{service_id} doesn't carry this promise",
                evidence={"reason": "unsupported_service"},
            )
        if service_id not in _ARR_API_VERSIONS:
            return self._probe_unknown(
                ctx,
                f"{service_id} not in api-version map",
                evidence={"service_id": service_id},
            )
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return self._probe_unknown(
                ctx,
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not arr_api_key:
            return self._probe_unknown(
                ctx,
                "no arr api key — cannot probe",
                evidence={"service_id": service_id},
            )
        return None

    def _quality_profile_url(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        version = _ARR_API_VERSIONS.get(service_id, "")
        base = self._arr_base_url(service_id, ctx)
        if not base or not version:
            return ""
        path = _QUALITY_PROFILE_PATH_FMT.format(ver=version)
        return f"{base}{path}"

    def _import_list_url(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        version = _ARR_API_VERSIONS.get(service_id, "")
        base = self._arr_base_url(service_id, ctx)
        if not base or not version:
            return ""
        path = _IMPORT_LIST_PATH_FMT.format(ver=version)
        return f"{base}{path}"

    def _arr_base_url(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        """Resolve ``<scheme>://<host>:<port>/app/<service_id>``.

        Mirrors ``IndexerPipelineWirer._arr_base_url`` — *arrs serve
        at ``/app/<service_id>/`` URL base; bare ``/api/...`` returns
        a 307 redirect that urllib drops POST bodies on. Always go
        through the prefixed path (consistent with the rest of the
        adapter family even though probes are GETs)."""
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}/app/{service_id}"

    def _http_get_json(
        self, url: str, arr_api_key: str,
    ) -> Any | None:
        """GET ``url`` with X-Api-Key, return parsed JSON or ``None``
        on any error."""
        if not url:
            return None
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
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    # === Dependency resolution ========================================

    def _resolve_dependencies(
        self,
    ) -> tuple[Callable[[Any], Mapping[str, Any]] | None, Callable[[], Any] | None]:
        """Resolve (configure_handler, job_context_factory).

        Constructor overrides win (tests inject doubles); otherwise
        lazy-import the legacy handler + JobContext from the
        ``services/`` shim path so the adapters→application hexagon
        ratchet stays clean (matches the Jellyseerr config-wiring
        pattern)."""
        if (
            self._configure_handler_override is not None
            and self._job_context_factory_override is not None
        ):
            return (
                self._configure_handler_override,
                self._job_context_factory_override,
            )
        try:
            from media_stack.services.apps.core.job_adapters import (
                apply_arr_runtime_defaults as _legacy_handler,
            )
            from media_stack.services.jobs.framework import JobContext
        except Exception:  # noqa: BLE001
            return (
                self._configure_handler_override,
                self._job_context_factory_override,
            )
        handler = self._configure_handler_override or _legacy_handler
        factory = self._job_context_factory_override or (lambda: JobContext())
        return handler, factory


__all__ = ["RuntimeDefaultsWirer"]
