"""MediaBrowser notifier wiring for the *arr family.

Lifecycle-method port of the legacy ``ensure_arr_jellyfin_notifier``
job handler (ADR-0005 Phase 3 cutover). The class lives here rather
than inline in ``servarr/lifecycle.py`` so the lifecycle module
stays focused on the core ``ServiceLifecycle`` Protocol surface
(probe_running / probe_has_api_key / mint_api_key / persist_api_key).

``JellyfinNotifierWirer`` owns:

  * The HTTP shape of the *arr notification API (``/app/<svc>/api/<ver>/notification``)
  * The MediaBrowser payload structure (host / port / api key / event flags)
  * The per-*arr event-flag map (sonarr / radarr / lidarr each name
    their events differently — sending the wrong name silently drops
    events)
  * Idempotent skip logic (don't POST if a notifier with our name
    already exists)
  * Tri-state outcome semantics (transient on prereq missing, permanent
    on 4xx, success on POST or already-configured)

The Servarr lifecycle methods are thin delegators — they discover the
arr api key via ``ServarrLifecycle.discover_api_key`` and pass it
into the wirer along with the orchestration context.
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


_JELLYFIN_NOTIFIER_NAME = "media-stack-jellyfin"
_JELLYFIN_NOTIFIER_HOST = "jellyfin"
_JELLYFIN_NOTIFIER_PORT = 8096
_NOTIFIER_HTTP_LIST_TIMEOUT_SECONDS = 5
_NOTIFIER_HTTP_POST_TIMEOUT_SECONDS = 30
_JELLYFIN_API_KEY_ENV = "JELLYFIN_API_KEY"

# *arrs not in this map (prowlarr, readarr) don't support the
# MediaBrowser notifier; the wirer's probe / ensure short-circuit
# for those service ids.
_ARR_API_VERSIONS: dict[str, str] = {
    "sonarr": "v3",
    "radarr": "v3",
    "lidarr": "v1",
}

# Each *arr names its events differently. Sending an unknown flag
# is silently ignored — so the union-of-all-names approach would
# leave each app missing its own critical events. Map per-app
# explicitly. The semantic intent is identical: "fire on any
# change to a file on disk."
_NOTIFIER_COMMON_OFF: dict[str, bool] = {
    "onGrab": False,
    "onHealthIssue": False,
    "onHealthRestored": False,
    "onApplicationUpdate": False,
    "onManualInteractionRequired": False,
}

_NOTIFIER_EVENT_FLAGS: dict[str, dict[str, bool]] = {
    "sonarr": {
        **_NOTIFIER_COMMON_OFF,
        "onDownload": True,
        "onUpgrade": True,
        "onImportComplete": True,
        "onRename": True,
        "onSeriesAdd": False,
        "onSeriesDelete": True,
        "onEpisodeFileDelete": True,
        "onEpisodeFileDeleteForUpgrade": False,
    },
    "radarr": {
        **_NOTIFIER_COMMON_OFF,
        "onDownload": True,
        "onUpgrade": True,
        "onRename": True,
        "onMovieAdded": False,
        "onMovieDelete": True,
        "onMovieFileDelete": True,
        "onMovieFileDeleteForUpgrade": False,
    },
    "lidarr": {
        **_NOTIFIER_COMMON_OFF,
        "onReleaseImport": True,
        "onUpgrade": True,
        "onRename": True,
        "onTrackRetag": True,
        "onArtistAdd": False,
        "onArtistDelete": True,
        "onAlbumDelete": True,
        "onDownloadFailure": False,
        "onImportFailure": False,
    },
}


class JellyfinNotifierWirer(LifecycleWirerBase):
    """Per-*arr Jellyfin MediaBrowser-notifier wiring.

    Constructor-injected notifier identity (name / host / port);
    per-call ``service_id`` + arr_api_key + context. Stateless —
    the same instance handles every *arr.
    """

    def __init__(
        self,
        *,
        notifier_name: str = _JELLYFIN_NOTIFIER_NAME,
        notifier_host: str = _JELLYFIN_NOTIFIER_HOST,
        notifier_port: int = _JELLYFIN_NOTIFIER_PORT,
    ) -> None:
        self._notifier_name = notifier_name
        self._notifier_host = notifier_host
        self._notifier_port = notifier_port

    def probe(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        if service_id not in _ARR_API_VERSIONS:
            return ProbeResult.ok(
                f"{service_id} doesn't support MediaBrowser notifier",
                evidence={"reason": "unsupported_service"},
                evaluated_at=ctx.now(),
            )
        url = self._notifier_endpoint(service_id, ctx)
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
        existing = self._list_notifiers(url, arr_api_key)
        if existing is None:
            return ProbeResult.unknown(
                f"could not list notifiers at {url}",
                evidence={"url": url, "service_id": service_id},
                evaluated_at=ctx.now(),
            )
        return self._classify_probe_result(existing, url, ctx)

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
        url = self._notifier_endpoint(service_id, ctx)
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
        jf_key = self._discover_jellyfin_key(ctx)
        if not jf_key:
            return Outcome.failure(
                f"no {_JELLYFIN_API_KEY_ENV} — orchestrator will retry "
                "after jellyfin-api-key-discoverable reaches ok",
                transient=True,
                evidence={"url": url},
            )
        existing = self._list_notifiers(url, arr_api_key)
        if existing is None:
            return Outcome.failure(
                f"could not list existing notifiers at {url}",
                transient=True,
                evidence={"url": url},
            )
        if self._notifier_present(existing):
            return Outcome.success(
                None,
                evidence={"reason": "already_configured", "url": url},
            )
        return self._post_notifier(service_id, url, arr_api_key, jf_key)

    def _notifier_endpoint(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        version = _ARR_API_VERSIONS.get(service_id, "")
        if not version:
            return ""
        # *arrs serve at ``/app/<service_id>/`` URL base. The bare
        # ``/api/...`` returns a 307 redirect; urllib drops the POST
        # body on 307, so the request lands payloadless and the *arr
        # rejects it. Always go through the prefixed path.
        return (
            f"{scheme}://{host}:{port}/app/{service_id}"
            f"/api/{version}/notification"
        )

    def _list_notifiers(
        self, url: str, arr_api_key: str,
    ) -> list[Any] | None:
        try:
            req = urllib.request.Request(url, headers={"X-Api-Key": arr_api_key})
            with urllib.request.urlopen(
                req, timeout=_NOTIFIER_HTTP_LIST_TIMEOUT_SECONDS,
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

    def _notifier_present(self, existing: list[Any]) -> bool:
        return any(
            isinstance(n, dict) and n.get("name") == self._notifier_name
            for n in existing
        )

    def _classify_probe_result(
        self, existing: list[Any], url: str, ctx: OrchestrationContext,
    ) -> ProbeResult:
        for entry in existing:
            if (
                isinstance(entry, dict)
                and entry.get("name") == self._notifier_name
            ):
                return ProbeResult.ok(
                    f"{self._notifier_name} configured",
                    evidence={"url": url, "notifier_id": entry.get("id")},
                    evaluated_at=ctx.now(),
                )
        return ProbeResult.failed(
            f"{self._notifier_name} not present at {url}",
            evidence={"url": url, "found_count": len(existing)},
            evaluated_at=ctx.now(),
        )

    def _discover_jellyfin_key(self, ctx: OrchestrationContext) -> str:
        return self._discover_secret(ctx, _JELLYFIN_API_KEY_ENV)

    def _post_notifier(
        self,
        service_id: str,
        url: str,
        arr_api_key: str,
        jf_key: str,
    ) -> Outcome[None]:
        payload = self._build_payload(service_id, jf_key)
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                method="POST",
                headers={
                    "X-Api-Key": arr_api_key,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(
                req, timeout=_NOTIFIER_HTTP_POST_TIMEOUT_SECONDS,
            ) as resp:
                return self._outcome_success(
                    evidence={"http_status": resp.status, "url": url},
                )
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError,
        ) as exc:
            return self._classify_http_outcome(exc, url=url)

    def _build_payload(
        self, service_id: str, jf_key: str,
    ) -> dict[str, Any]:
        flags = _NOTIFIER_EVENT_FLAGS[service_id]
        return {
            "name": self._notifier_name,
            "implementation": "MediaBrowser",
            "configContract": "MediaBrowserSettings",
            **flags,
            # ``updateLibrary=True`` tells Jellyfin to scan the
            # affected path on each event; ``notify=False`` skips
            # the in-app banner so users don't see a popup per
            # import.
            "fields": [
                {"name": "host",          "value": self._notifier_host},
                {"name": "port",          "value": self._notifier_port},
                {"name": "useSsl",        "value": False},
                {"name": "urlBase",       "value": ""},
                {"name": "apiKey",        "value": jf_key},
                {"name": "notify",        "value": False},
                {"name": "updateLibrary", "value": True},
            ],
        }


__all__ = ["JellyfinNotifierWirer"]
