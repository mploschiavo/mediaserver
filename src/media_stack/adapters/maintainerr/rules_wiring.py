"""Maintainerr rules-linked-to-arr wiring for the rule-link family.

Lifecycle-method port of the legacy ``ensure_maintainerr_integrations``
job handler (ADR-0005 Phase 3 cutover). The class lives here rather
than inline in ``maintainerr/lifecycle.py`` so the lifecycle module
stays focused on the ``NoApiKeyLifecycleBase`` Protocol surface.

``MaintainerrCollectionsWirer`` owns:

  * The HTTP shape of Maintainerr's collections endpoint
    (GET ``/app/maintainerr/api/collections``) — used by the probe
    to assert that movie/show rules link to a non-None
    ``radarrSettingsId`` / ``sonarrSettingsId``.
  * Idempotent skip logic — the probe is the same shape the legacy
    promise's ``http_json`` probe used; if it's already ok, the
    ensurer short-circuits without invoking the heavyweight
    ``ensure_maintainerr_integrations`` flow.
  * Tri-state outcome semantics (transient when prereq missing /
    Maintainerr unreachable, permanent on structural issues, success
    on probe-already-ok or successful integration ensurer).

Why wide-handler delegation?
============================

The legacy code path that performs the actual ``radarrSettingsId`` /
``sonarrSettingsId`` linkage is ``MaintainerrRuleSyncService``
invoked from ``MaintainerrService.ensure_integrations`` — itself
called from the ``ensure_maintainerr_integrations`` job handler with
the four-arg signature ``(cfg, config_root, arr_apps, wait_timeout)``.
The path covers: settings test connections (Radarr / Sonarr /
Jellyseerr / Tautulli), per-arr POST-then-GET reconcile of integration
records, then ``MaintainerrRuleSyncService.sync_policy_rules`` which
DELETE+POSTs every policy rule with the resolved
``radarrSettingsId`` / ``sonarrSettingsId``. >150 LoC of multi-
subsystem orchestration, JobContext-bound. Re-implementing inside the
wirer would duplicate that surface; the recipe's wide-handler delegation
addendum (Jellyseerr lesson) covers exactly this shape.

So the wirer takes injected ``configure_handler`` + ``job_context_factory``
callables on ``ensure_rules_linked_to_arr``, mirroring the Jellyseerr
``ensure_arr_servers`` pattern. The probe stays in-process so a
probe-ok run never invokes the heavyweight handler.

Note on the legacy ``ensured_by: configure-collections`` reference:
that string pointed at the Jellyfin auto-collections job
(``configure-collections`` lives in ``contracts/services/jellyfin.yaml``
and is implemented by ``ensure_jellyfin_auto_collections_config``),
which has nothing to do with Maintainerr's rule-arr linkage. The
cutover untangles the misnomer — the lifecycle ensurer reaches the
real handler (``ensure_maintainerr_integrations``) while the Jellyfin
``configure-collections`` job stays registered for its own
auto-collections purpose.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from media_stack.adapters._shared.lifecycle_wirer_base import (
    LifecycleWirerBase,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


_COLLECTIONS_PATH = "/app/maintainerr/api/collections"
_PROBE_TIMEOUT_SECONDS = 5
_MOVIE_TYPE = "movie"
_SHOW_TYPE = "show"
_RADARR_LINK_FIELD = "radarrSettingsId"
_SONARR_LINK_FIELD = "sonarrSettingsId"


class MaintainerrCollectionsWirer(LifecycleWirerBase):
    """Rule-link wiring for the ``maintainerr-rules-linked-to-arr``
    promise.

    Stateless beyond constructor-injected coordinates (collections
    path / probe timeout). Per-call parameters: ``ctx``
    (OrchestrationContext) + a callable ``configure_handler`` for the
    ensurer that performs the side-effecting integration reconcile
    (delegates to the existing ``ensure_maintainerr_integrations`` job
    handler so the rule-sync + per-arr reconcile + test-connection
    surface isn't duplicated here).
    """

    def __init__(
        self,
        *,
        collections_path: str = _COLLECTIONS_PATH,
        probe_timeout_seconds: int = _PROBE_TIMEOUT_SECONDS,
    ) -> None:
        self._collections_path = collections_path
        self._probe_timeout_seconds = probe_timeout_seconds

    # --- probe ------------------------------------------------------

    def probe(self, ctx: OrchestrationContext) -> ProbeResult:
        """Probe Maintainerr's live collections endpoint and assert
        that AT LEAST ONE movie rule links to ``radarrSettingsId`` or
        AT LEAST ONE show rule links to ``sonarrSettingsId``.

        Mirrors the legacy ``http_json`` probe shape from
        ``contracts/services/maintainerr.yaml``. Returns ``unknown``
        when the endpoint is unreachable / unparseable so the
        orchestrator retries; ``failed`` only when the structural
        contents say "no link" (the actionable signal); ``ok`` when
        any movie/show rule has its respective link populated.
        """
        url = self._collections_url(ctx)
        if not url:
            return self._probe_unknown(
                ctx,
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        body = self._fetch_collections(url)
        if body is None:
            return self._probe_unknown(
                ctx,
                f"could not fetch collections at {url}",
                evidence={"url": url},
            )
        if not isinstance(body, list):
            return self._probe_failed(
                ctx,
                f"collections endpoint returned non-list at {url}",
                evidence={"url": url, "type": type(body).__name__},
            )
        if not body:
            return self._probe_failed(
                ctx,
                f"collections endpoint empty at {url}",
                evidence={"url": url, "collection_count": 0},
            )
        linked = self._count_linked(body)
        total = len(body)
        if linked == 0:
            return self._probe_failed(
                ctx,
                f"no movie/show collection links to radarr/sonarr "
                f"({total} collection(s) all unlinked)",
                evidence={
                    "url": url,
                    "collection_count": total,
                    "linked_count": 0,
                },
            )
        return self._probe_ok(
            ctx,
            f"{linked}/{total} collection(s) linked to radarr/sonarr",
            evidence={
                "url": url,
                "collection_count": total,
                "linked_count": linked,
            },
        )

    # --- ensurer (wide-handler delegation) --------------------------

    def ensure(
        self,
        ctx: OrchestrationContext,
        *,
        configure_handler: Callable[..., Any],
        job_context_factory: Callable[[], Any],
    ) -> Outcome[None]:
        """Delegate to the existing ``ensure_maintainerr_integrations``
        handler.

        ``configure_handler`` is the callable
        ``(cfg, config_root, arr_apps, wait_timeout) -> None`` from
        ``application.maintainerr.runtime_ops``.
        ``job_context_factory`` is a callable ``() -> JobContext`` —
        usually ``JobContext`` itself, or a stub in tests.

        Idempotent skip when the probe already ok'd: the existing
        handler's reconcile is itself idempotent (only DELETE+POSTs
        rules whose payload changed and only POSTs missing arr
        records), but probing first surfaces the "already linked"
        case cleanly without spinning up the test-connections + arr-
        registry round trip.
        """
        probe = self.probe(ctx)
        if probe.is_ok:
            return self._outcome_success(
                evidence={
                    "reason": "already_linked",
                    "url": self._collections_url(ctx),
                },
            )
        try:
            job_ctx = job_context_factory()
        except Exception as exc:  # noqa: BLE001
            return self._outcome_transient(
                f"could not build JobContext: {exc}",
                evidence={"error": str(exc)},
            )
        cfg, config_root, arr_apps, wait_timeout = self._unpack_job_ctx(job_ctx)
        if cfg is None or config_root is None:
            return self._outcome_transient(
                "JobContext missing cfg / config_root — cannot dispatch "
                "ensure_maintainerr_integrations",
                evidence={
                    "has_cfg": cfg is not None,
                    "has_config_root": config_root is not None,
                },
            )
        try:
            configure_handler(cfg, config_root, arr_apps, wait_timeout)
        except Exception as exc:  # noqa: BLE001
            return self._outcome_transient(
                f"ensure_maintainerr_integrations raised: {exc}",
                evidence={"error": str(exc)},
            )
        return self._outcome_success(
            evidence={
                "delegated_to": "ensure_maintainerr_integrations",
                "config_root": str(config_root),
            },
        )

    # --- internals --------------------------------------------------

    def _collections_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}{self._collections_path}"

    def _fetch_collections(self, url: str) -> Any:
        try:
            with urllib.request.urlopen(
                url, timeout=self._probe_timeout_seconds,
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

    def _count_linked(self, collections: list[Any]) -> int:
        linked = 0
        for entry in collections:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("type")
            if kind == _MOVIE_TYPE and entry.get(_RADARR_LINK_FIELD) is not None:
                linked += 1
            elif kind == _SHOW_TYPE and entry.get(_SONARR_LINK_FIELD) is not None:
                linked += 1
        return linked

    def _unpack_job_ctx(self, job_ctx: Any) -> tuple[Any, Any, Any, Any]:
        """Pull the four ``ensure_maintainerr_integrations`` args from
        a ``JobContext``-shaped object. Tolerant of missing
        ``arr_apps`` / ``wait_timeout`` (defaults to empty list /
        framework default) so adapter tests can pass a minimal stub."""
        cfg = getattr(job_ctx, "cfg", None)
        config_root = getattr(job_ctx, "config_root", None)
        arr_apps = getattr(job_ctx, "arr_apps", None) or []
        wait_timeout = getattr(job_ctx, "wait_timeout", None)
        return cfg, config_root, arr_apps, wait_timeout


__all__ = ["MaintainerrCollectionsWirer"]
