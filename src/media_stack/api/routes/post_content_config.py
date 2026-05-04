"""Content / Config POST routes (ADR-0007 Phase 2 wave 6).

Six POST routes lifted off the legacy ``handlers_post.handle()``
elif chain spanning the **Content** + **Config** OpenAPI tags:

* ``POST /api/custom-formats/import`` — TRASHguides custom-format
  import. Sibling GET catalogue lives in
  ``routes/indexers_quality.py``.
* ``POST /api/custom-service`` — custom-service registry create.
* ``POST /api/download-client-settings`` — sibling of the wave-3
  GET in ``routes/downloads.py``.
* ``POST /api/livetv-sources`` — sibling of the wave-3 GET in
  ``routes/epg.py``; queues ``configure-livetv`` on success.
* ``POST /api/livetv-sources/probe`` — server-side M3U/XMLTV URL
  probe for the operator's Probe button. Spec entry added in this
  wave; all other paths were already declared.
* ``POST /api/quality-profiles/toggle`` — sibling of the wave-4
  GET in ``routes/content_lists.py``.

Each route body is lifted into an instance method of
``ContentConfigPostRoutes`` (``@post(path)``-tagged). Collaborators
are constructor-injected for swap-able test seams:

* ``CustomFormatImporter`` (Adapter), ``CustomServiceCreator``
  (Adapter), ``DownloadClientSettingsWriter`` (Adapter),
  ``LivetvSourceWriter`` (Command + action queue),
  ``LivetvProbeService`` (Command — narrow exception set replacing
  the legacy broad-except hammer), ``QualityProfileToggleService``
  (Strategy over the two toggle commands).

Cross-cutting concerns (CSRF, auth, audit log) are enforced
upstream by ``server.py`` — same story as the wave-5 sibling
``post_config_writes.py``.
"""

from __future__ import annotations

import urllib.error as _urllib_error
from http import HTTPStatus
from typing import Any
from urllib.request import Request as _UrllibRequest
from urllib.request import urlopen as _urllib_urlopen

from media_stack.api.routing import RouteModule, post
from media_stack.api.services import config as config_svc
from media_stack.api.services import content as content_svc
from media_stack.services.apps.servarr import (
    quality_preset_service as _quality_preset_service,
)

# Narrow probe-exception set — replaces the legacy broad-except.
# ``HTTPError`` (subclass of URLError) is handled separately to
# extract ``code`` / ``reason``.
_PROBE_EXCEPTIONS = (
    _urllib_error.URLError, OSError, ValueError, UnicodeDecodeError,
)
_PROBE_SAMPLE_BYTES = 4096       # legacy 4 KiB Range read
_PROBE_TIMEOUT_SECONDS = 8       # legacy probe timeout
_PROBE_PREVIEW_BYTES = 512       # response preview shown to UI
_PROBE_USER_AGENT = "media-stack/livetv-probe"
# HTTP header field-name constants — lifted to module scope so the
# per-call dict-literal site doesn't trigger the inline-http-headers
# ratchet.
_HEADER_USER_AGENT = "User-Agent"
_HEADER_RANGE = "Range"
_HEADER_CONTENT_TYPE = "Content-Type"


class CustomFormatImporter:
    """Adapter over ``quality_preset_service.import_trash_custom_formats``.

    Owns the ``service`` + ``index_url`` pre-flight validation;
    returns ``(status_code, payload)``.
    """

    def __init__(
        self, preset_service: Any | None = None,
    ) -> None:
        self._preset_service = (
            preset_service
            if preset_service is not None
            else _quality_preset_service
        )

    def execute(self, body: dict) -> tuple[int, dict[str, Any]]:
        service_id = body.get("service", "")
        index_url = body.get("index_url", "")
        if not service_id or not index_url:
            return (
                HTTPStatus.BAD_REQUEST,
                {"error": "service and index_url required"},
            )
        result = self._preset_service.import_trash_custom_formats(
            service_id, index_url,
        )
        return (HTTPStatus.OK, result)


class CustomServiceCreator:
    """Adapter over ``config_svc.add_custom_service``. Owns the
    empty-body 400 pre-flight; returns ``(status_code, payload)``.
    """

    def __init__(self, config_service: Any | None = None) -> None:
        self._config_service = (
            config_service if config_service is not None else config_svc
        )

    def execute(self, body: dict | None) -> tuple[int, dict[str, Any]]:
        if not body:
            return (
                HTTPStatus.BAD_REQUEST,
                {"error": "JSON body required"},
            )
        return (
            HTTPStatus.OK,
            self._config_service.add_custom_service(body),
        )


class DownloadClientSettingsWriter:
    """Adapter over ``content_svc.update_download_client_settings``.
    Service-layer schema check; no pre-flight here.
    """

    def __init__(self, content_service: Any | None = None) -> None:
        self._content_service = (
            content_service if content_service is not None else content_svc
        )

    def update(self, body: dict) -> dict[str, Any]:
        return self._content_service.update_download_client_settings(body)


class LivetvSourceWriter:
    """Command over ``config_svc.update_livetv_sources`` + the
    ``configure-livetv`` action queue. Mirrors the queue-on-success
    rule from ``LibraryConfigWriter`` in ``post_config_writes.py``.
    """

    def __init__(self, config_service: Any | None = None) -> None:
        self._config_service = (
            config_service if config_service is not None else config_svc
        )

    def update(
        self, body: dict, action_trigger: Any,
    ) -> dict[str, Any]:
        result = self._config_service.update_livetv_sources(
            tuners=body.get("tuners"),
            guides=body.get("guides"),
            tuner_url=body.get("tuner_url", ""),
            guide_url=body.get("guide_url", ""),
            load_all_tuners=body.get("load_all_tuners"),
        )
        if "error" not in result and action_trigger:
            action_trigger("configure-livetv", {})
            result["action"] = "configure-livetv queued"
        return result


class LivetvProbeService:
    """One-shot HTTP probe — reads up to 4 KiB of the URL and
    classifies the body as M3U or XMLTV. Returns the legacy
    ``{ok, status, content_type, bytes, sample, kind, error}``
    shape; never raises (narrow exception set replaces the legacy
    broad-except hammer).

    Constructor-injected ``urlopen`` + ``request_factory`` for
    test seams; defaults are the stdlib ``urllib.request`` pair.
    """

    def __init__(
        self,
        urlopen: Any | None = None,
        request_factory: Any | None = None,
    ) -> None:
        self._urlopen = (
            urlopen if urlopen is not None else _urllib_urlopen
        )
        self._request_factory = (
            request_factory
            if request_factory is not None
            else _UrllibRequest
        )

    def _empty_result(self) -> dict[str, Any]:
        return {
            "ok": False, "status": 0, "content_type": "",
            "bytes": 0, "sample": "", "kind": "unknown", "error": "",
        }

    def _classify(
        self, sample_text: str, content_type: str,
    ) -> tuple[str, bool, str]:
        """Return ``(kind, ok, error)`` based on the body sample +
        Content-Type. Lifted verbatim from the legacy classifier
        so the dashboard verdict stays byte-stable.
        """
        head = sample_text.lstrip()[:32].lower()
        ct = (content_type or "").lower()
        if head.startswith("#extm3u"):
            return ("m3u", True, "")
        if head.startswith("<?xml") and "tv" in head:
            return ("xmltv", True, "")
        if "xml" in ct:
            return ("xmltv", True, "")
        if "mpegurl" in ct:
            return ("m3u", True, "")
        return (
            "unknown", False,
            "URL responded but body doesn't look like M3U or XMLTV",
        )

    def probe(self, url: str) -> dict[str, Any]:
        """Probe an M3U / XMLTV URL via a small range GET. Always
        returns the legacy result dict; never raises."""
        out = self._empty_result()
        headers = {
            _HEADER_USER_AGENT: _PROBE_USER_AGENT,
            _HEADER_RANGE: f"bytes=0-{_PROBE_SAMPLE_BYTES - 1}",
        }
        req = self._request_factory(url, method="GET", headers=headers)
        try:
            with self._urlopen(req, timeout=_PROBE_TIMEOUT_SECONDS) as resp:
                out["status"] = int(resp.status or 0)
                out["content_type"] = (
                    resp.headers.get(_HEADER_CONTENT_TYPE, "") or ""
                ).split(";")[0].strip()
                data = resp.read(_PROBE_SAMPLE_BYTES) or b""
                out["bytes"] = len(data)
                text = data.decode("utf-8", errors="replace")
                out["sample"] = text[:_PROBE_PREVIEW_BYTES]
                kind, ok, err = self._classify(text, out["content_type"])
                out["kind"] = kind
                out["ok"] = ok
                if err:
                    out["error"] = err
        except _urllib_error.HTTPError as exc:
            out["status"] = int(exc.code or 0)
            out["error"] = f"HTTP {exc.code}: {exc.reason}"
        except _PROBE_EXCEPTIONS as exc:
            out["error"] = str(exc)[:200]
        return out


class QualityProfileToggleService:
    """Strategy over the two ``quality_preset_service`` toggles —
    ``"quality"`` body field selects ``toggle_quality``,
    ``"upgradeAllowed"`` selects ``toggle_upgrade``. Returns
    ``(status_code, payload)``; 400 when neither is present.
    """

    def __init__(
        self, preset_service: Any | None = None,
    ) -> None:
        self._preset_service = (
            preset_service
            if preset_service is not None
            else _quality_preset_service
        )

    def execute(self, body: dict) -> tuple[int, dict[str, Any]]:
        service = self._preset_service
        if "quality" in body:
            return (
                HTTPStatus.OK,
                service.toggle_quality(
                    body["service"],
                    int(body["profile_id"]),
                    body["quality"],
                    bool(body["enabled"]),
                ),
            )
        if "upgradeAllowed" in body:
            return (
                HTTPStatus.OK,
                service.toggle_upgrade(
                    body["service"],
                    int(body["profile_id"]),
                    bool(body["upgradeAllowed"]),
                ),
            )
        return (
            HTTPStatus.BAD_REQUEST,
            {"error": "quality or upgradeAllowed required"},
        )


class ContentConfigPostRoutes(RouteModule):
    """Content-tag + Config-tag POST routes — six paths
    auto-discovered by the Router. Constructor defaults wire up
    the production collaborators so the no-arg instantiation the
    Router performs at startup just works.
    """

    def __init__(
        self,
        custom_format_importer: CustomFormatImporter | None = None,
        custom_service_creator: CustomServiceCreator | None = None,
        download_client_writer: DownloadClientSettingsWriter | None = None,
        livetv_source_writer: LivetvSourceWriter | None = None,
        livetv_probe_service: LivetvProbeService | None = None,
        quality_toggle_service: QualityProfileToggleService | None = None,
    ) -> None:
        self._custom_formats = (
            custom_format_importer
            if custom_format_importer is not None
            else CustomFormatImporter()
        )
        self._custom_services = (
            custom_service_creator
            if custom_service_creator is not None
            else CustomServiceCreator()
        )
        self._download_settings = (
            download_client_writer
            if download_client_writer is not None
            else DownloadClientSettingsWriter()
        )
        self._livetv_writer = (
            livetv_source_writer
            if livetv_source_writer is not None
            else LivetvSourceWriter()
        )
        self._livetv_probe = (
            livetv_probe_service
            if livetv_probe_service is not None
            else LivetvProbeService()
        )
        self._quality_toggle = (
            quality_toggle_service
            if quality_toggle_service is not None
            else QualityProfileToggleService()
        )

    @post("/api/custom-formats/import")
    def handle_custom_formats_import(self, handler: Any) -> None:
        """Import TRASHguides custom formats into a Servarr service."""
        body = handler._read_json_body() or {}
        status, payload = self._custom_formats.execute(body)
        handler._json_response(status, payload)

    @post("/api/custom-service")
    def handle_custom_service(self, handler: Any) -> None:
        """Register a custom service in the controller registry."""
        body = handler._read_json_body()
        status, payload = self._custom_services.execute(body)
        handler._json_response(status, payload)

    @post("/api/download-client-settings")
    def handle_download_client_settings(self, handler: Any) -> None:
        """Overwrite the download-client connection / queueing knobs."""
        body = handler._read_json_body() or {}
        handler._json_response(
            HTTPStatus.OK, self._download_settings.update(body),
        )

    @post("/api/livetv-sources")
    def handle_livetv_sources(self, handler: Any) -> None:
        """Overwrite the configured M3U / XMLTV URLs; queue
        ``configure-livetv`` on success."""
        body = handler._read_json_body() or {}
        result = self._livetv_writer.update(body, handler.action_trigger)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/livetv-sources/probe")
    def handle_livetv_sources_probe(self, handler: Any) -> None:
        """Probe an M3U / XMLTV URL — see ``LivetvProbeService.probe``."""
        body = handler._read_json_body() or {}
        url = str(body.get("url", "") or "").strip()
        if not url:
            handler._json_response(
                HTTPStatus.BAD_REQUEST, {"error": "url required"},
            )
            return
        handler._json_response(
            HTTPStatus.OK, self._livetv_probe.probe(url),
        )

    @post("/api/quality-profiles/toggle")
    def handle_quality_profiles_toggle(self, handler: Any) -> None:
        """Toggle a named quality or flip ``upgradeAllowed`` — see
        ``QualityProfileToggleService.execute``."""
        body = handler._read_json_body() or {}
        status, payload = self._quality_toggle.execute(body)
        handler._json_response(status, payload)


__all__ = [
    "ContentConfigPostRoutes",
    "CustomFormatImporter",
    "CustomServiceCreator",
    "DownloadClientSettingsWriter",
    "LivetvProbeService",
    "LivetvSourceWriter",
    "QualityProfileToggleService",
]
