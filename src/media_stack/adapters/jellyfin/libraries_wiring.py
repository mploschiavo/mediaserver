"""Jellyfin library wiring (ADR-0005 Phase 5b — the 10th and final wirer).

Lifecycle-method port of the legacy ``ensure_jellyfin_libraries`` job
handler. Closes Phase 5b: every ``ensure-*`` job that bound a contract
promise via string ``ensured_by`` is now reachable through a
``{type: lifecycle, ...}`` dispatch.

Single-service wirer — Jellyfin owns one library set, so the wirer
takes ``(jellyfin_api_key, ctx)`` rather than the Servarr family's
``(service_id, arr_api_key, ctx)`` triple.

``JellyfinLibrariesWirer`` owns:
  * The desired-library spec (Movies / TV Shows / Music / Books).
    Library paths come from the MediaType catalog (single SoT in
    ``contracts/catalog/media_types.yaml``); the Jellyfin-specific
    display name + ``CollectionType`` come from a module-level
    mapping (Jellyfin upstream lore, not user-tunable).
  * ``GET /Library/VirtualFolders`` readback — authenticated via
    ``X-Emby-Token`` (Jellyfin 10.11+ — keeps the credential out of
    access logs that historically ingested the legacy query-string
    auth variant).
  * ``POST /Library/VirtualFolders`` create flow — URL-encoded
    query-param shape (name + collectionType + paths +
    refreshLibrary, posted with an empty body); mirrors the legacy
    handler exactly.
  * Probe: ``ok`` when every desired library is present at the
    expected path, ``failed`` with missing/drifted evidence, ``unknown``
    on read failure / no api key.
  * Ensurer: idempotent — short-circuit when probe already ok;
    otherwise POST each missing library. Tri-state outcome
    (transient on prereq missing or unreachable; permanent on 4xx).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
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
from media_stack.infrastructure.media.catalog import load_media_types


# --- HTTP timing -----------------------------------------------------

_LIST_HTTP_TIMEOUT_SECONDS = 10
_WRITE_HTTP_TIMEOUT_SECONDS = 15

# Both list + create live at the same path; create distinguishes via
# ``method=POST`` + URL-encoded query string (matches legacy handler).
_VIRTUAL_FOLDERS_PATH = "/Library/VirtualFolders"

# Jellyfin 10.11 ``X-Emby-Token`` replaces the legacy query-string
# auth — header keeps the credential out of access logs.
_EMBY_TOKEN_HEADER = "X-Emby-Token"

# (display_name, collection_type, media_path). media_path sourced
# from the MediaType catalog SoT; display_name + collection_type are
# Jellyfin-specific labels (catalog ``name=tv`` → Jellyfin ``tvshows``).
_DefaultLibrarySpec = tuple[str, str, str]

# Catalog-key → (jellyfin display name, jellyfin collection_type).
# Jellyfin-specific labels not in the catalog. Adding a new catalog
# entry without an entry here is a silent skip (entry just won't be
# probed/ensured) — intentional, since not every catalog type has a
# Jellyfin library binding.
_JELLYFIN_LABELS_BY_CATALOG_NAME: dict[str, tuple[str, str]] = {
    "tv":     ("TV Shows", "tvshows"),
    "movies": ("Movies",   "movies"),
    "music":  ("Music",    "music"),
    "books":  ("Books",    "books"),
}


class JellyfinLibrariesWirer(LifecycleWirerBase):
    """Single-service wirer — Jellyfin owns one library set.

    Constructor-injected library spec + HTTP timeouts. Per-call:
    ``jellyfin_api_key`` + ``OrchestrationContext``. Stateless — the
    same instance handles every call.
    """

    def __init__(
        self,
        *,
        library_specs: tuple[_DefaultLibrarySpec, ...] | None = None,
        list_timeout_seconds: int = _LIST_HTTP_TIMEOUT_SECONDS,
        write_timeout_seconds: int = _WRITE_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        # ``None`` → derive from the MediaType catalog at construct
        # time. Tests inject a literal tuple to override; the
        # production singleton constructed in
        # ``adapters/jellyfin/lifecycle.py`` passes nothing and gets
        # the catalog-driven default.
        if library_specs is None:
            library_specs = self._default_library_specs_from_catalog()
        self._library_specs = tuple(library_specs)
        self._list_timeout_seconds = list_timeout_seconds
        self._write_timeout_seconds = write_timeout_seconds

    def _default_library_specs_from_catalog(
        self,
    ) -> tuple[_DefaultLibrarySpec, ...]:
        """Materialize the default library spec from the MediaType
        catalog. Library paths come from the catalog (single SoT);
        Jellyfin-specific display name + collection_type come from
        the module-level lookup. Catalog miss → empty spec (caller
        surfaces ``probe_unknown`` on first call rather than crashing
        at import)."""
        catalog = load_media_types()
        specs: list[_DefaultLibrarySpec] = []
        for catalog_name, labels in (
            _JELLYFIN_LABELS_BY_CATALOG_NAME.items()
        ):
            media = catalog.get(catalog_name)
            if media is None:
                continue
            display_name, collection_type = labels
            specs.append(
                (display_name, collection_type, media.library_path),
            )
        return tuple(specs)

    # === Probe =========================================================

    def probe(
        self,
        jellyfin_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        guard = self._guard_probe_prereqs(jellyfin_api_key, ctx)
        if guard is not None:
            return guard
        url = self._virtual_folders_url(ctx)
        existing = self._list_libraries(url, jellyfin_api_key or "")
        if existing is None:
            return self._probe_unknown(
                ctx,
                f"could not list libraries at {url}",
                evidence={"url": url},
            )
        return self._classify_probe_result(existing, url, ctx)

    def _guard_probe_prereqs(
        self,
        jellyfin_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult | None:
        """Short-circuit ProbeResult when prereqs aren't met; ``None``
        when probe should proceed."""
        url = self._virtual_folders_url(ctx)
        if not url:
            return self._probe_unknown(
                ctx,
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not jellyfin_api_key:
            return self._probe_unknown(
                ctx,
                "no jellyfin api key — cannot probe",
                evidence={"url": url},
            )
        return None

    # === Ensurer ======================================================

    def ensure(
        self,
        jellyfin_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> Outcome[None]:
        url = self._virtual_folders_url(ctx)
        if not url:
            return self._outcome_permanent(
                "no host/port in config — cannot ensure",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not jellyfin_api_key:
            return self._outcome_transient(
                "no jellyfin api key — orchestrator will retry "
                "after probe_has_api_key reaches ok",
                evidence={"url": url},
            )
        existing = self._list_libraries(url, jellyfin_api_key)
        if existing is None:
            return self._outcome_transient(
                f"could not list existing libraries at {url}",
                evidence={"url": url},
            )
        missing = self._missing_libraries(existing)
        if not missing:
            return self._outcome_success(
                evidence={
                    "reason": "already_configured",
                    "url": url,
                    "library_count": len(existing),
                },
            )
        return self._post_missing_libraries(
            missing, url, jellyfin_api_key,
        )

    # === Endpoint helpers ==============================================

    def _virtual_folders_url(self, ctx: OrchestrationContext) -> str:
        """Resolve ``<scheme>://<host>:<port>/Library/VirtualFolders``.

        Empty string when host / port aren't in config — the probe /
        ensurer treat that as "config not loaded yet" and surface
        ``unknown`` / ``permanent`` respectively."""
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}{_VIRTUAL_FOLDERS_PATH}"

    # === HTTP plumbing =================================================

    def _list_libraries(
        self, url: str, jellyfin_api_key: str,
    ) -> list[Any] | None:
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    _EMBY_TOKEN_HEADER: jellyfin_api_key,
                },
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

    def _post_missing_libraries(
        self,
        missing: list[_DefaultLibrarySpec],
        url: str,
        jellyfin_api_key: str,
    ) -> Outcome[None]:
        headers = {_EMBY_TOKEN_HEADER: jellyfin_api_key}
        added: list[str] = []
        for name, ctype, path in missing:
            params = urllib.parse.urlencode({
                "name": name,
                "collectionType": ctype,
                "paths": path,
                "refreshLibrary": "false",
            })
            target_url = f"{url}?{params}"
            try:
                req = urllib.request.Request(
                    target_url, method="POST", headers=headers,
                )
                with urllib.request.urlopen(
                    req, timeout=self._write_timeout_seconds,
                ):
                    added.append(name)
            except (
                urllib.error.HTTPError, urllib.error.URLError,
                OSError, TimeoutError,
            ) as exc:
                return self._classify_http_outcome(exc, url=target_url)
        return self._outcome_success(
            evidence={
                "url": url,
                "added": added,
                "operation": "created",
            },
        )

    # === Classification helpers =======================================

    def _classify_probe_result(
        self,
        existing: list[Any],
        url: str,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        missing = self._missing_libraries(existing)
        drifted = self._drifted_libraries(existing)
        if not missing and not drifted:
            return self._probe_ok(
                ctx,
                "all expected libraries present",
                evidence={
                    "url": url,
                    "library_count": len(existing),
                    "expected_count": len(self._library_specs),
                },
            )
        evidence: dict[str, Any] = {
            "url": url,
            "library_count": len(existing),
            "missing": [name for name, _, _ in missing],
        }
        # Distinguish "library missing entirely" from "library present
        # but path drifted" so operator dashboards can show a
        # meaningful detail line. Both cases need re-ensuring; the
        # legacy ensurer's POST is keyed on (Name, CollectionType)
        # so a path-drifted entry doesn't get re-created — operator
        # action is needed to reconcile. Surface drift explicitly.
        if drifted:
            evidence["drifted"] = drifted
            if not missing:
                return self._probe_failed(
                    ctx,
                    "libraries present but path drifted from /media/* "
                    "— manual reconcile needed (ensurer matches on "
                    "name+type and won't re-create)",
                    evidence=evidence,
                )
        return self._probe_failed(
            ctx,
            f"missing libraries: {[name for name, _, _ in missing]}",
            evidence=evidence,
        )

    def _missing_libraries(
        self, existing: list[Any],
    ) -> list[_DefaultLibrarySpec]:
        """Return the subset of ``self._library_specs`` not present
        in ``existing``. Match is on (Name, CollectionType) — same
        key the legacy handler uses."""
        have = {
            (entry.get("Name"), entry.get("CollectionType"))
            for entry in (existing or [])
            if isinstance(entry, dict)
        }
        return [
            spec for spec in self._library_specs
            if (spec[0], spec[1]) not in have
        ]

    def _drifted_libraries(
        self, existing: list[Any],
    ) -> list[dict[str, Any]]:
        """Return libraries whose name + type match a desired entry
        but whose Locations don't include the expected media path.
        Surfaces as evidence on probe failure — operator dashboards
        can tell warmup-in-progress from genuinely-drifted state."""
        wanted_by_key = {
            (name, ctype): path
            for name, ctype, path in self._library_specs
        }
        drifted: list[dict[str, Any]] = []
        for entry in (existing or []):
            if not isinstance(entry, dict):
                continue
            key = (entry.get("Name"), entry.get("CollectionType"))
            wanted_path = wanted_by_key.get(key)
            if wanted_path is None:
                continue
            locations = entry.get("Locations") or []
            if isinstance(locations, list) and wanted_path in locations:
                continue
            drifted.append({
                "name": entry.get("Name"),
                "collection_type": entry.get("CollectionType"),
                "expected_path": wanted_path,
                "actual_locations": locations,
            })
        return drifted


__all__ = ["JellyfinLibrariesWirer"]
