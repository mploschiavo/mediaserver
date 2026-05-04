"""Indexer-pipeline wiring for the *arr family.

Lifecycle-method port of the legacy ``push_indexers`` job handler
(ADR-0005 Phase 3 cutover, follow-on to the *-jellyfin-notifier
proof-of-pattern in ``notifier_wiring.py``).

The legacy ``push_indexers`` handler ran the entire Prowlarr indexer
pipeline (`indexer_steps` runner phase): a heavyweight bootstrap
flow that touched every *arr at once. This module narrows the
surface to the per-promise question both ``sonarr-has-indexers``
and ``radarr-has-indexers`` actually ask:

  * Probe: "Does THIS *arr have at least one indexer right now?"
  * Ensurer: "If not, ask Prowlarr to push them — Prowlarr's
    ``ApplicationIndexerSync`` command fans out to every registered
    app. From this *arr's point of view, a successful ``forceSync``
    is the per-arr ensure."

``IndexerPipelineWirer`` owns:

  * The HTTP shape of ``/app/<svc>/api/v3/indexer`` (per-*arr) and
    ``/app/prowlarr/api/v1/command`` (Prowlarr's command endpoint).
  * Probe semantics: ``ok`` if any indexers exist, ``failed`` if
    zero, ``unknown`` on probe error / missing prereq.
  * Ensurer semantics: idempotent — short-circuit when the *arr
    already has indexers (no need to bother Prowlarr). When zero
    indexers exist, POST ``ApplicationIndexerSync`` with
    ``forceSync=true`` so Prowlarr re-pushes even apps it thinks
    are already in sync (matches the legacy job's behavior).
  * Tri-state outcome (transient on prereq missing or Prowlarr
    unreachable, permanent on 4xx, success on probe-already-ok or
    successful sync trigger).

The Servarr lifecycle methods are thin delegators — they discover
the arr api key via ``ServarrLifecycle.discover_api_key`` and pass
it into the wirer along with the orchestration context. The wirer
discovers the Prowlarr api key from ``ctx.secrets`` / ``os.environ``
in the same way the existing notifier wirer discovers the Jellyfin
key.
"""

from __future__ import annotations

import json
import os
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


_PROWLARR_HOST = "prowlarr"
_PROWLARR_PORT = 9696
_PROWLARR_SCHEME = "http"
_PROWLARR_API_KEY_ENV = "PROWLARR_API_KEY"
_PROWLARR_COMMAND_NAME = "ApplicationIndexerSync"
_INDEXER_HTTP_LIST_TIMEOUT_SECONDS = 5
_INDEXER_HTTP_POST_TIMEOUT_SECONDS = 30

# *arrs that expose ``/api/v3/indexer`` and participate in
# Prowlarr's ApplicationIndexerSync. ``prowlarr`` itself doesn't
# (it's the source); ``readarr`` runs an older indexer schema and
# has no has-indexers promise of its own.
_ARR_API_VERSIONS: dict[str, str] = {
    "sonarr": "v3",
    "radarr": "v3",
    "lidarr": "v1",
}


class IndexerPipelineWirer(LifecycleWirerBase):
    """Per-*arr indexer-pipeline wiring.

    Constructor-injected Prowlarr coordinates (host / port);
    per-call ``service_id`` + arr_api_key + context. Stateless —
    the same instance handles every *arr.
    """

    def __init__(
        self,
        *,
        prowlarr_host: str = _PROWLARR_HOST,
        prowlarr_port: int = _PROWLARR_PORT,
        prowlarr_scheme: str = _PROWLARR_SCHEME,
        list_timeout_seconds: int = _INDEXER_HTTP_LIST_TIMEOUT_SECONDS,
        post_timeout_seconds: int = _INDEXER_HTTP_POST_TIMEOUT_SECONDS,
    ) -> None:
        self._prowlarr_host = prowlarr_host
        self._prowlarr_port = prowlarr_port
        self._prowlarr_scheme = prowlarr_scheme
        self._list_timeout_seconds = list_timeout_seconds
        self._post_timeout_seconds = post_timeout_seconds

    def probe(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        guard = self._guard_probe_prereqs(service_id, arr_api_key, ctx)
        if guard is not None:
            return guard
        url = self._indexer_endpoint(service_id, ctx)
        existing = self._list_indexers(url, arr_api_key or "")
        if existing is None:
            return ProbeResult.unknown(
                f"could not list indexers at {url}",
                evidence={"url": url, "service_id": service_id},
                evaluated_at=ctx.now(),
            )
        return self._classify_probe_result(existing, url, ctx)

    def _guard_probe_prereqs(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult | None:
        """Return a short-circuit ProbeResult when service_id /
        config / api-key prereqs aren't met; ``None`` when probe
        should proceed. Folding these gates into one guard keeps
        the public ``probe()`` method's body narrow."""
        if service_id not in _ARR_API_VERSIONS:
            return ProbeResult.ok(
                f"{service_id} doesn't participate in indexer pipeline",
                evidence={"reason": "unsupported_service"},
                evaluated_at=ctx.now(),
            )
        url = self._indexer_endpoint(service_id, ctx)
        if not url:
            return ProbeResult.unknown(
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
                evaluated_at=ctx.now(),
            )
        if not arr_api_key:
            return ProbeResult.unknown(
                "no arr api key — cannot probe",
                evidence={"url": url, "service_id": service_id},
                evaluated_at=ctx.now(),
            )
        return None

    def ensure(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> Outcome[None]:
        if service_id not in _ARR_API_VERSIONS:
            return Outcome.success(
                None,
                evidence={"reason": "unsupported_service"},
            )
        url = self._indexer_endpoint(service_id, ctx)
        if not url:
            return Outcome.failure(
                "no host/port in config — cannot ensure",
                transient=False,
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not arr_api_key:
            return Outcome.failure(
                f"no {service_id} api key — orchestrator will retry "
                "after probe_has_api_key reaches ok",
                transient=True,
                evidence={"url": url},
            )
        existing = self._list_indexers(url, arr_api_key)
        if existing is None:
            return Outcome.failure(
                f"could not list existing indexers at {url}",
                transient=True,
                evidence={"url": url},
            )
        if self._has_indexers(existing):
            return Outcome.success(
                None,
                evidence={
                    "reason": "already_configured",
                    "url": url,
                    "indexer_count": len(existing),
                },
            )
        prowlarr_key = self._discover_prowlarr_key(ctx)
        if not prowlarr_key:
            return Outcome.failure(
                f"no {_PROWLARR_API_KEY_ENV} — orchestrator will retry "
                "after prowlarr's probe_has_api_key reaches ok",
                transient=True,
                evidence={"url": url},
            )
        return self._trigger_prowlarr_sync(url, prowlarr_key, service_id)

    def _indexer_endpoint(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        # *arrs serve at ``/app/<service_id>/`` URL base. The bare
        # ``/api/...`` returns a 307 redirect; urllib drops POST
        # bodies on 307. Always go through the prefixed path.
        version = _ARR_API_VERSIONS.get(service_id, "")
        base = self._arr_base_url(service_id, ctx)
        return f"{base}/api/{version}/indexer" if base and version else ""

    def _arr_base_url(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        """Resolve ``<scheme>://<host>:<port>/app/<service_id>``
        from the orchestration config. Returns the empty string if
        host or port is missing — callers pair the empty result
        with a probe-unknown / ensure-failure short-circuit."""
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}/app/{service_id}"

    def _prowlarr_command_endpoint(self) -> str:
        # Prowlarr's command endpoint always lives under ``/app/prowlarr/``
        # for the same urlBase reason — direct
        # ``<scheme>://prowlarr:9696/api/...`` works on a fresh stack but
        # starts 307'ing once Prowlarr's ``Application URL`` setting
        # gets wired (which the bootstrap does). Going through the
        # prefixed path is the always-safe form.
        return (
            f"{self._prowlarr_scheme}://"
            f"{self._prowlarr_host}:{self._prowlarr_port}"
            f"/app/prowlarr/api/v1/command"
        )

    def _list_indexers(
        self, url: str, arr_api_key: str,
    ) -> list[Any] | None:
        try:
            req = urllib.request.Request(
                url, headers={"X-Api-Key": arr_api_key},
            )
            with urllib.request.urlopen(
                req, timeout=self._list_timeout_seconds,
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

    def _has_indexers(self, existing: list[Any]) -> bool:
        return len(existing) >= 1

    def _classify_probe_result(
        self, existing: list[Any], url: str, ctx: OrchestrationContext,
    ) -> ProbeResult:
        if self._has_indexers(existing):
            return ProbeResult.ok(
                f"{len(existing)} indexer(s) configured",
                evidence={"url": url, "indexer_count": len(existing)},
                evaluated_at=ctx.now(),
            )
        return ProbeResult.failed(
            f"no indexers at {url}",
            evidence={"url": url, "indexer_count": 0},
            evaluated_at=ctx.now(),
        )

    def _discover_prowlarr_key(self, ctx: OrchestrationContext) -> str:
        return (
            (ctx.secrets.get(_PROWLARR_API_KEY_ENV) or "").strip()
            or os.environ.get(_PROWLARR_API_KEY_ENV, "").strip()
        )

    def _trigger_prowlarr_sync(
        self,
        arr_indexer_url: str,
        prowlarr_key: str,
        service_id: str,
    ) -> Outcome[None]:
        command_url = self._prowlarr_command_endpoint()
        # ``forceSync=true`` makes Prowlarr push EVERY indexer to
        # every registered app, even when its internal sync state
        # thinks they're already there. Without it, an *arr that
        # lost its indexers (e.g. radarr deleted them on a 400) won't
        # get them back until something marks the app dirty — which
        # usually never happens. Same flag the legacy
        # ``ProwlarrApplicationOps.trigger_sync`` uses.
        payload = {
            "name": _PROWLARR_COMMAND_NAME,
            "forceSync": True,
        }
        try:
            req = urllib.request.Request(
                command_url,
                data=json.dumps(payload).encode(),
                method="POST",
                headers={
                    "X-Api-Key": prowlarr_key,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(
                req, timeout=self._post_timeout_seconds,
            ) as resp:
                return Outcome.success(
                    None,
                    evidence={
                        "http_status": resp.status,
                        "command_url": command_url,
                        "arr_indexer_url": arr_indexer_url,
                        "service_id": service_id,
                        "command": _PROWLARR_COMMAND_NAME,
                    },
                )
        except urllib.error.HTTPError as exc:
            return Outcome.failure(
                f"HTTP {exc.code} from {command_url}",
                transient=False,
                evidence={
                    "http_status": exc.code,
                    "command_url": command_url,
                    "service_id": service_id,
                },
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return Outcome.failure(
                f"prowlarr unreachable at {command_url}: {exc}",
                transient=True,
                evidence={
                    "command_url": command_url,
                    "service_id": service_id,
                    "error": str(exc),
                },
            )


__all__ = ["IndexerPipelineWirer"]
