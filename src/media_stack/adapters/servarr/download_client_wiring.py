"""Download-client wiring for the *arr family.

Lifecycle-method port of the legacy ``ensure_arr_download_client``
job handler (ADR-0005 Phase 5b — the deferred 9th wirer; the eight
shipped earlier in Phase 3 are notifier / indexer-pipeline /
runtime-defaults / seed-series / bazarr-config / jellyseerr-config /
qbit-categories / maintainerr-rules).

The legacy ``ensure_arr_download_client`` is a monolithic dispatcher:
ONE call walks every *arr in ``arr_specs`` and POSTs / PUTs
qBittorrent into each *arr's download-client list with the right
per-arr category. Splitting the legacy handler isn't necessary the
way it was for ``apply_arr_runtime_defaults`` (which patches
*shared* settings docs that would clobber each other) — each *arr's
``/downloadclient`` list is independent, so a per-arr ensurer can
reach the same endpoint without stepping on its siblings.

So ``DownloadClientWirer`` follows the simpler ``IndexerPipelineWirer``
shape (NOT the wide-handler delegation pattern): the wirer owns
the full HTTP lifecycle (list-existing → match-by-implementation →
PUT-or-POST) directly, mirroring the legacy handler's payload
shape verbatim. The legacy handler stays REGISTERED for
``run_job(name)`` until Phase 5b.5.

``DownloadClientWirer`` owns:

  * The per-*arr API version / category-field / category-value map
    (``tvCategory=tv`` for Sonarr, ``movieCategory=movies`` for
    Radarr, ``musicCategory=music`` for Lidarr,
    ``bookCategory=books`` for Readarr — same map the legacy handler
    asserts).
  * The HTTP shape of ``/app/<svc>/api/<ver>/downloadclient`` (per-
    *arr list / POST-create / PUT-update with the qBittorrent
    payload).
  * Probe semantics: ``ok`` if a qBit entry exists, is enabled, and
    has the right category; ``failed`` otherwise; ``unknown`` on
    probe error / missing prereq.
  * Ensurer semantics: idempotent — short-circuit when probe
    already says ok. Otherwise POST (no existing match) or PUT
    (match present but drifted) the canonical payload.
  * Tri-state outcome (transient on prereq missing or arr unreachable,
    permanent on 4xx, success on probe-already-ok or POST/PUT
    completion).

The Servarr lifecycle method is a thin delegator — it discovers
the arr api key via ``ServarrLifecycle.discover_api_key`` and passes
it into the wirer along with the orchestration context. qBittorrent
credentials come from constructor injection (defaulting to the
canonical env-var read) so tests can override without touching
process env.
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
from media_stack.infrastructure.qbittorrent import (
    QBITTORRENT_DEFAULT_HOST,
    QBITTORRENT_DEFAULT_WEBUI_PORT,
    QBITTORRENT_FACTORY_DEFAULT_PASSWORD,
    QBITTORRENT_FACTORY_DEFAULT_USERNAME,
)


# --- HTTP timing -----------------------------------------------------

_LIST_HTTP_TIMEOUT_SECONDS = 10
_WRITE_HTTP_TIMEOUT_SECONDS = 10

# --- qBittorrent identity ------------------------------------------
#
# Connection target for the *arr's download-client config —
# host/port describe where the *arr should reach qBit, NOT where
# the controller reaches qBit. Username/password resolved through
# the LifecycleWirerBase ctx.secrets-then-os.environ fallback so
# the wirer doesn't read process env directly (one canonical path
# for credential discovery; tests inject via ctx.secrets). Host /
# port + factory-default creds all sourced from the canonical
# ``infrastructure.qbittorrent`` SoT — the allowlisted upstream-
# constants module — so this wirer doesn't redeclare them.
_QBIT_USERNAME_ENV = "QBIT_USERNAME"
_QBIT_PASSWORD_ENV = "QBIT_PASSWORD"  # noqa: S105
_QBIT_IMPLEMENTATION = "QBittorrent"
_QBIT_CONFIG_CONTRACT = "QBittorrentSettings"
_DOWNLOAD_CLIENT_NAME = "qBittorrent"

# --- Per-*arr category + api-version map ---------------------------
#
# Mirrors the legacy ``arr_specs`` table exactly. Tuple shape is
# (api_version, category_field_name, category_value). Adding /
# removing a service id here changes the supported set.
_ARR_DOWNLOAD_CLIENT_SPECS: dict[str, tuple[str, str, str]] = {
    "sonarr":  ("v3", "tvCategory",    "tv"),
    "radarr":  ("v3", "movieCategory", "movies"),
    "lidarr":  ("v1", "musicCategory", "music"),
    "readarr": ("v1", "bookCategory",  "books"),
}


class DownloadClientWirer(LifecycleWirerBase):
    """Per-*arr qBittorrent download-client wiring.

    Constructor-injected qBittorrent credentials (callables so the
    legacy handler's env-var read pattern is preserved without
    binding env state at import time); per-call ``service_id`` +
    arr_api_key + ``OrchestrationContext``. Stateless — the same
    instance handles every *arr.
    """

    def __init__(
        self,
        *,
        qbit_host: str = QBITTORRENT_DEFAULT_HOST,
        qbit_port: int = QBITTORRENT_DEFAULT_WEBUI_PORT,
        list_timeout_seconds: int = _LIST_HTTP_TIMEOUT_SECONDS,
        write_timeout_seconds: int = _WRITE_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._qbit_host = qbit_host
        self._qbit_port = qbit_port
        self._list_timeout_seconds = list_timeout_seconds
        self._write_timeout_seconds = write_timeout_seconds

    def _qbit_username(self, ctx: OrchestrationContext) -> str:
        """ctx.secrets → os.environ → factory-default username.
        Tests inject via ctx.secrets so the secret-discovery path is
        identical to every other wirer in the family."""
        return (
            self._discover_secret(ctx, _QBIT_USERNAME_ENV)
            or QBITTORRENT_FACTORY_DEFAULT_USERNAME
        )

    def _qbit_password(self, ctx: OrchestrationContext) -> str:
        """Same shape as ``_qbit_username`` — ctx.secrets first,
        env fallback, then upstream factory default."""
        return (
            self._discover_secret(ctx, _QBIT_PASSWORD_ENV)
            or QBITTORRENT_FACTORY_DEFAULT_PASSWORD
        )

    # === Probe =========================================================

    def probe(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        guard = self._guard_probe_prereqs(service_id, arr_api_key, ctx)
        if guard is not None:
            return guard
        url = self._download_client_endpoint(service_id, ctx)
        existing = self._list_download_clients(url, arr_api_key or "")
        if existing is None:
            return self._probe_unknown(
                ctx,
                f"could not list download clients at {url}",
                evidence={"url": url, "service_id": service_id},
            )
        return self._classify_probe_result(existing, service_id, url, ctx)

    def _guard_probe_prereqs(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> ProbeResult | None:
        """Short-circuit ProbeResult when service / config / key
        prereqs aren't met; ``None`` when probe should proceed."""
        if service_id not in _ARR_DOWNLOAD_CLIENT_SPECS:
            return self._probe_ok(
                ctx,
                f"{service_id} doesn't carry a download-client promise",
                evidence={"reason": "unsupported_service"},
            )
        url = self._download_client_endpoint(service_id, ctx)
        if not url:
            return self._probe_unknown(
                ctx,
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not arr_api_key:
            return self._probe_unknown(
                ctx,
                "no arr api key — cannot probe",
                evidence={"url": url, "service_id": service_id},
            )
        return None

    # === Ensurer ======================================================

    def ensure(
        self,
        service_id: str,
        arr_api_key: str | None,
        ctx: OrchestrationContext,
    ) -> Outcome[None]:
        if service_id not in _ARR_DOWNLOAD_CLIENT_SPECS:
            return self._outcome_success(
                evidence={"reason": "unsupported_service"},
            )
        url = self._download_client_endpoint(service_id, ctx)
        if not url:
            return self._outcome_permanent(
                "no host/port in config — cannot ensure",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not arr_api_key:
            return self._outcome_transient(
                f"no {service_id} api key — orchestrator will retry "
                "after probe_has_api_key reaches ok",
                evidence={"url": url},
            )
        existing = self._list_download_clients(url, arr_api_key)
        if existing is None:
            return self._outcome_transient(
                f"could not list existing download clients at {url}",
                evidence={"url": url},
            )
        if self._is_probe_ok(existing, service_id):
            return self._outcome_success(
                evidence={
                    "reason": "already_configured",
                    "url": url,
                    "service_id": service_id,
                },
            )
        return self._upsert_qbit_client(
            existing, service_id, url, arr_api_key, ctx,
        )

    # === Endpoint helpers ==============================================

    def _download_client_endpoint(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        """Resolve ``<scheme>://<host>:<port>/app/<svc>/api/<ver>/downloadclient``.

        *arrs serve at ``/app/<service_id>/`` URL base; the bare
        ``/api/...`` returns a 307 redirect that urllib drops POST
        bodies on. Always go through the prefixed path (matches the
        rest of the wirer family)."""
        spec = _ARR_DOWNLOAD_CLIENT_SPECS.get(service_id)
        if spec is None:
            return ""
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        version = spec[0]
        return (
            f"{scheme}://{host}:{port}"
            f"/app/{service_id}/api/{version}/downloadclient"
        )

    # === HTTP plumbing =================================================

    def _list_download_clients(
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

    def _upsert_qbit_client(
        self,
        existing: list[Any],
        service_id: str,
        url: str,
        arr_api_key: str,
        ctx: OrchestrationContext,
    ) -> Outcome[None]:
        match = self._find_qbit_entry(existing)
        payload = self._build_payload(service_id, ctx)
        headers = {
            "X-Api-Key": arr_api_key,
            "Content-Type": "application/json",
        }
        if match is not None:
            client_id = match.get("id")
            payload["id"] = client_id
            target_url = f"{url}/{client_id}"
            method = "PUT"
        else:
            target_url = url
            method = "POST"
        try:
            req = urllib.request.Request(
                target_url,
                data=json.dumps(payload).encode(),
                method=method,
                headers=headers,
            )
            with urllib.request.urlopen(
                req, timeout=self._write_timeout_seconds,
            ) as resp:
                return self._outcome_success(
                    evidence={
                        "http_status": resp.status,
                        "url": target_url,
                        "method": method,
                        "service_id": service_id,
                        "operation": "updated" if match else "created",
                    },
                )
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError,
        ) as exc:
            return self._classify_http_outcome(exc, url=target_url)

    # === Classification helpers =======================================

    def _classify_probe_result(
        self,
        existing: list[Any],
        service_id: str,
        url: str,
        ctx: OrchestrationContext,
    ) -> ProbeResult:
        if self._is_probe_ok(existing, service_id):
            return self._probe_ok(
                ctx,
                f"{_DOWNLOAD_CLIENT_NAME} configured with correct category",
                evidence={
                    "url": url,
                    "service_id": service_id,
                    "client_count": len(existing),
                },
            )
        # Distinguish "no qBit at all" from "qBit present but
        # category drifted" so operator dashboards can show a
        # meaningful detail line.
        match = self._find_qbit_entry(existing)
        if match is None:
            return self._probe_failed(
                ctx,
                f"no {_DOWNLOAD_CLIENT_NAME} entry at {url}",
                evidence={
                    "url": url,
                    "service_id": service_id,
                    "client_count": len(existing),
                },
            )
        return self._probe_failed(
            ctx,
            f"{_DOWNLOAD_CLIENT_NAME} entry present but enable / "
            "category drifted from desired state",
            evidence={
                "url": url,
                "service_id": service_id,
                "qbit_id": match.get("id"),
                "qbit_enabled": bool(match.get("enable")),
            },
        )

    def _is_probe_ok(
        self, existing: list[Any], service_id: str,
    ) -> bool:
        spec = _ARR_DOWNLOAD_CLIENT_SPECS.get(service_id)
        if spec is None:
            return True  # unsupported short-circuit; never reached
        _, cat_field, cat_value = spec
        for entry in existing or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("implementation") != _QBIT_IMPLEMENTATION:
                continue
            if entry.get("enable") is not True:
                continue
            for field in entry.get("fields") or []:
                if (
                    isinstance(field, dict)
                    and field.get("name") == cat_field
                    and field.get("value") == cat_value
                ):
                    return True
        return False

    def _find_qbit_entry(
        self, existing: list[Any],
    ) -> dict[str, Any] | None:
        for entry in existing or []:
            if (
                isinstance(entry, dict)
                and entry.get("implementation") == _QBIT_IMPLEMENTATION
            ):
                return entry
        return None

    # === Payload builder ==============================================

    def _build_payload(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> dict[str, Any]:
        """Mirror the legacy handler's payload shape verbatim. The
        ``priority: 1`` / ``removeCompletedDownloads: False`` /
        ``removeFailedDownloads: True`` flags are what the legacy
        handler writes; flipping any of them here would silently
        drift live deployments on next ensurer run."""
        spec = _ARR_DOWNLOAD_CLIENT_SPECS[service_id]
        _, cat_field, cat_value = spec
        username = self._qbit_username(ctx)
        password = self._qbit_password(ctx)
        fields = [
            {"name": "host",     "value": self._qbit_host},
            {"name": "port",     "value": self._qbit_port},
            {"name": "useSsl",   "value": False},
            {"name": "urlBase",  "value": ""},
            {"name": "username", "value": username},
            {"name": "password", "value": password},
            {"name": cat_field,  "value": cat_value},
        ]
        return {
            "name": _DOWNLOAD_CLIENT_NAME,
            "implementation": _QBIT_IMPLEMENTATION,
            "configContract": _QBIT_CONFIG_CONTRACT,
            "enable": True,
            "priority": 1,
            "removeCompletedDownloads": False,
            "removeFailedDownloads": True,
            "fields": fields,
        }


__all__ = ["DownloadClientWirer"]
