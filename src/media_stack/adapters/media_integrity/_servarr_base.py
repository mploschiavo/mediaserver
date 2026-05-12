"""Shared Servarr HTTP adapter.

Every *arr in the Servarr family (Radarr/Sonarr/Lidarr/Readarr)
exposes an ~identical HTTP surface:

- ``X-Api-Key`` header auth
- ``/api/v{version}/config/mediamanagement`` (GET + PUT)
- ``/api/v{version}/config/naming`` (GET + PUT)
- ``/api/v{version}/qualityprofile`` (GET)
- ``/api/v{version}/{media}file`` (DELETE single, GET by parent)
- ``/api/v{version}/{media}`` (GET releases, PUT one release)

Per-app quirks (``/moviefile/{id}`` vs. ``/episodefile/{id}``, the
``autoUnmonitorPreviouslyDownloaded{Movies,Episodes,Tracks,Books}``
family, Sonarr's series→episode flattening) are handled by the
subclass hooks on this base. The reconciler and enforcer don't
import anything from here — they consume ``ArrApp`` from the
protocol module.

HTTP is pluggable via the ``HttpClient`` protocol so tests don't
hit the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from media_stack.domain.media_integrity.arr_protocol import (
    AdapterCapabilities,
    MediaFile,
    MediaRelease,
    QualityProfile,
)


DEFAULT_TIMEOUT_SEC = 15.0


# ---------------------------------------------------------------------------
# HTTP abstraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpResponse:
    """Minimal HTTP response surface the adapter needs."""

    status: int
    body: bytes


class HttpClient(Protocol):
    """Pluggable HTTP transport. Default is ``UrllibHttpClient``;
    tests substitute a fake that returns canned responses."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> HttpResponse:
        ...


# ADR-0011 Phase 1 — ``ServarrHttpError`` lifted into the domain
# layer (``domain/media_integrity/servarr_http_error.py``) so the
# structural scrubber can recognise its shape without a domain →
# adapters deferred import. Re-exported here for back-compat with
# adapter code that imports from this module.
from media_stack.domain.media_integrity.servarr_http_error import (
    ServarrHttpError,
)


class UrllibHttpClient:
    """Default transport — stdlib urllib. No deps, no surprises.

    Follows 3xx redirects explicitly (up to ``_MAX_REDIRECTS`` hops)
    so Servarr instances configured with a URL base
    (``http://radarr:7878/api/...`` → ``/app/radarr/api/...``) just
    work. Same-host-only guard prevents an SSRF vector if a
    compromised adapter server sends us a Location pointing
    elsewhere.
    """

    _MAX_REDIRECTS = 3

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> HttpResponse:
        current_url = url
        current_body = body
        current_method = method
        for _ in range(self._MAX_REDIRECTS + 1):
            req = urllib.request.Request(
                current_url, data=current_body, method=current_method, headers=headers,
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return HttpResponse(status=resp.status, body=resp.read())
            except urllib.error.HTTPError as exc:
                status = exc.code
                if status in (301, 302, 303, 307, 308):
                    location = exc.headers.get("Location", "") if exc.headers else ""
                    new_url = self._safe_redirect_target(current_url, location)
                    if not new_url:
                        return HttpResponse(status=status, body=exc.read() or b"")
                    exc.close()
                    # 303 always downgrades to GET per RFC 7231; 301/302
                    # downgrade only for historical POST→GET behavior. For
                    # 307/308 the method + body MUST be preserved.
                    if status == 303:
                        current_method = "GET"
                        current_body = None
                    elif status in (301, 302) and current_method in ("POST",):
                        current_method = "GET"
                        current_body = None
                    current_url = new_url
                    continue
                return HttpResponse(status=status, body=exc.read() or b"")
        return HttpResponse(status=status, body=b"too many redirects")

    def _safe_redirect_target(self, source_url: str, location: str) -> str:
        """Resolve ``location`` against ``source_url`` and refuse cross-host
        redirects (SSRF guard). Returns the absolute target or ``""`` if
        the redirect must not be followed."""
        if not location:
            return ""
        try:
            src = urllib.parse.urlsplit(source_url)
            target = urllib.parse.urlsplit(
                urllib.parse.urljoin(source_url, location),
            )
        except ValueError:
            return ""
        if not target.scheme or not target.netloc:
            return ""
        if target.scheme != src.scheme or target.netloc != src.netloc:
            return ""
        return urllib.parse.urlunsplit(target)


_INSTANCE = UrllibHttpClient()

# Module-level alias preserves the legacy underscore-prefixed import
# name (``from _servarr_base import _safe_redirect_target``) for any
# caller / test that imports the helper directly. The body doesn't
# read instance state so the singleton-bound method is functionally
# equivalent to the original loose helper.
_safe_redirect_target = _INSTANCE._safe_redirect_target


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------


class _ServarrBaseAdapter:
    """Common Servarr HTTP plumbing shared by all four *arr adapters.

    Subclasses MUST set:
    - ``name`` (class attr, e.g. "radarr")
    - ``api_version`` (class attr, "v3" or "v1")
    - ``_media_file_endpoint`` (class attr, e.g. "moviefile")
    - ``_media_endpoint`` (class attr, e.g. "movie")
    - ``_MEDIA_MANAGEMENT_FIELDS`` (class attr, canonical→app map)
    - ``_NAMING_FIELDS`` (class attr, canonical→app map)

    Subclasses MUST override:
    - ``_release_from_raw(raw)`` — build MediaRelease from API item
    - ``_file_from_raw(raw, release_id)`` — build MediaFile from API item
    - ``_list_files_for(release_id)`` — per-app file listing (parent query)
    - ``list_releases()`` — returns flat list (Sonarr flattens
      series→episode; others are 1:1)
    """

    # -- Subclass MUST override -----------------------------------------

    name: str = ""
    api_version: str = ""
    _media_file_endpoint: str = ""  # e.g. "moviefile"
    _media_endpoint: str = ""  # e.g. "movie"
    # JSON field on the file row that carries the parent release id.
    # Radarr: "movieId"; Lidarr: "albumId"; Readarr: "bookId". Sonarr
    # overrides ``list_releases_for_file`` entirely (multi-episode).
    _parent_file_field: str = ""
    _MEDIA_MANAGEMENT_FIELDS: dict[str, str] = {}
    _NAMING_FIELDS: dict[str, str] = {}

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        media_root: str,
        http_client: HttpClient | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        if not self.name or not self.api_version:
            raise TypeError(
                f"{type(self).__name__} must set class attributes "
                "'name' and 'api_version'"
            )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.media_root = media_root
        self._http = http_client or UrllibHttpClient()
        self._timeout = timeout_sec
        self._cached_media_management: dict[str, Any] | None = None
        self.capabilities = self._probe_capabilities()

    # -- ArrApp protocol surface ----------------------------------------

    def media_management_field_map(self) -> dict[str, str]:
        return dict(self._MEDIA_MANAGEMENT_FIELDS)

    def naming_field_map(self) -> dict[str, str]:
        return dict(self._NAMING_FIELDS)

    def get_media_management(self) -> dict[str, Any]:
        cfg = self._request_json("GET", "/config/mediamanagement")
        if not isinstance(cfg, dict):
            raise ServarrHttpError(
                200,
                self._url("/config/mediamanagement"),
                b"expected object, got " + json.dumps(cfg).encode(),
            )
        self._cached_media_management = cfg
        return cfg

    def put_media_management(self, cfg: dict[str, Any]) -> None:
        self._request_json("PUT", "/config/mediamanagement", body=cfg)
        self._cached_media_management = None

    def get_naming(self) -> dict[str, Any]:
        cfg = self._request_json("GET", "/config/naming")
        if not isinstance(cfg, dict):
            raise ServarrHttpError(
                200,
                self._url("/config/naming"),
                b"expected object, got " + json.dumps(cfg).encode(),
            )
        return cfg

    def put_naming(self, cfg: dict[str, Any]) -> None:
        self._request_json("PUT", "/config/naming", body=cfg)

    def delete_file(self, file_id: str) -> None:
        self._request_raw("DELETE", f"/{self._media_file_endpoint}/{file_id}")

    def quality_profiles(self) -> list[QualityProfile]:
        raw = self._request_json("GET", "/qualityprofile")
        if not isinstance(raw, list):
            return []
        profiles: list[QualityProfile] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            cutoff_id = item.get("cutoff")
            if isinstance(cutoff_id, dict):
                cutoff_id = cutoff_id.get("id")
            items = item.get("items") or []
            if not isinstance(items, list):
                items = []
            profiles.append(
                QualityProfile(
                    id=int(item.get("id", 0)),
                    name=str(item.get("name", "")),
                    cutoff_id=int(cutoff_id) if cutoff_id is not None else 0,
                    items=tuple(i for i in items if isinstance(i, dict)),
                )
            )
        return profiles

    def quality_score(self, file: MediaFile) -> int:
        """Default: trust the score the adapter already stamped onto
        MediaFile at list_files_for time. Servarr's own ranking comes
        through the quality profile's ``items`` ordering."""
        return file.quality_score

    def list_files_for(self, release_id: str) -> list[MediaFile]:
        return self._list_files_for(release_id)

    def list_releases_for_file(self, file_id: str) -> list[str]:
        """Default 1:1 lookup. Subclasses with multi-release files
        (Sonarr's double-episode files) override.

        Failure modes (HTTP error, missing parent field) return
        ``[]``; the reconciler treats an empty list as "linkage
        unconfirmed" and skips the delete defensively."""
        if not self._parent_file_field:
            return []
        try:
            raw = self._request_json(
                "GET", f"/{self._media_file_endpoint}/{file_id}"
            )
        except ServarrHttpError:
            return []
        if not isinstance(raw, dict):
            return []
        parent = raw.get(self._parent_file_field)
        if parent is None:
            return []
        return [str(parent)]

    # -- Default implementations (subclass overrides if needed) --------

    def list_releases(self) -> list[MediaRelease]:
        """Default: ``GET /{_media_endpoint}`` and feed each row
        through ``_release_from_raw``. Sonarr overrides because a
        series → episodes flattening is non-trivial; Radarr / Lidarr
        / Readarr all use this default."""
        raw = self._request_json("GET", f"/{self._media_endpoint}")
        if not isinstance(raw, list):
            return []
        out: list[MediaRelease] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            release = self._release_from_raw(item)
            if release is not None:
                out.append(release)
        return out

    def _list_files_for(self, release_id: str) -> list[MediaFile]:
        """Default: ``GET /{_media_file_endpoint}?{_parent_file_field}=ID``.
        Sonarr overrides because the episode→file relationship is
        many-to-one through episodeIds; Radarr / Lidarr / Readarr
        all use this default."""
        raw = self._request_json(
            "GET",
            f"/{self._media_file_endpoint}?"
            f"{self._parent_file_field}={release_id}",
        )
        if not isinstance(raw, list):
            return []
        out: list[MediaFile] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            mf = self._file_from_raw(item, release_id)
            if mf is not None:
                out.append(mf)
        return out

    # -- Subclass hooks -------------------------------------------------

    def _release_from_raw(self, raw: dict[str, Any]) -> MediaRelease | None:
        raise NotImplementedError

    def _file_from_raw(
        self, raw: dict[str, Any], release_id: str
    ) -> MediaFile | None:
        raise NotImplementedError

    # -- HTTP plumbing --------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}/api/{self.api_version}{path}"

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {"X-Api-Key": self._api_key, "Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request_raw(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[Any] | None = None,
    ) -> HttpResponse:
        url = self._url(path)
        raw_body: bytes | None = None
        has_body = body is not None
        if has_body:
            raw_body = json.dumps(body).encode("utf-8")
        resp = self._http.request(
            method,
            url,
            headers=self._headers(json_body=has_body),
            body=raw_body,
            timeout=self._timeout,
        )
        if resp.status < 200 or resp.status >= 300:
            raise ServarrHttpError(resp.status, url, resp.body)
        return resp

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | list[Any] | None = None,
    ) -> Any:
        resp = self._request_raw(method, path, body=body)
        if not resp.body:
            return {}
        return json.loads(resp.body.decode("utf-8"))

    # -- Capability probing ---------------------------------------------

    def _extract_quality(self, quality_obj: Any) -> tuple[str, int]:
        """Pull ``(name, id)`` out of a Servarr ``quality`` envelope.

        Shape: ``{"quality": {"id": 7, "name": "Bluray-1080p", ...},
        "revision": ...}``. Returns ``("", 0)`` if malformed — the
        adapter is a boundary so we don't raise on bad shapes.
        """
        if not isinstance(quality_obj, dict):
            return "", 0
        inner = quality_obj.get("quality")
        if not isinstance(inner, dict):
            return "", 0
        return str(inner.get("name", "")), int(inner.get("id", 0))

    def _probe_capabilities(self) -> AdapterCapabilities:
        """One GET to ``/config/mediamanagement`` tells us which
        fields this app version actually exposes. That drives the
        ``supports_*`` flags so the enforcer never PUTs a field the
        app will reject."""
        try:
            cfg = self.get_media_management()
        except (ServarrHttpError, urllib.error.URLError, OSError):
            # Offline at construction — conservative defaults so the
            # adapter still installs into the integrity service.
            # ``OSError`` covers connection-refused on fresh compose
            # boots where the *arr container is up but not yet
            # accepting WebUI connections; the controller's periodic
            # re-wire (controller_serve_wiring.refresh_media_integrity)
            # rebuilds adapters once the service is reachable.
            return AdapterCapabilities()
        fields = tuple(sorted(cfg.keys()))
        auto_unmonitor_key = self._MEDIA_MANAGEMENT_FIELDS.get("unmonitor_deleted")
        hardlinks_key = self._MEDIA_MANAGEMENT_FIELDS.get("use_hardlinks", "")
        return AdapterCapabilities(
            supports_auto_unmonitor_deleted=bool(
                auto_unmonitor_key and auto_unmonitor_key in cfg
            ),
            supports_rename=True,
            supports_hardlinks=hardlinks_key in cfg if hardlinks_key else True,
            supports_quality_profile_cutoff=True,
            supports_file_delete=True,
            supports_release_listing=True,
            probed_field_names=fields,
        )
