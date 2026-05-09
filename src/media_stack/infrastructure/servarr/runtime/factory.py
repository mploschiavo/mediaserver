#!/usr/bin/env python3
"""Shared runtime service factories for Servarr-related operations.

ADR-0012 Phase 2 — replaces 15 loose module-level helpers with two
constructor-injected classes:

* :class:`ServarrTechnologyHelpers` owns the small token/URL/category
  utilities that used to live as private module helpers. They keep the
  underscore prefix on the class so they remain considered "private"
  by the ``NO_TYPE_HINTS_PUBLIC_METHODS`` ratchet.
* :class:`ServarrRuntimeFactory` builds runtime service instances
  (``ArrService``, torrent/usenet client services, Prowlarr,
  indexer-sync, queue-cleanup, policy, health, auth) by composing the
  helper class together with the runtime-platform primitives and the
  service-registry resolver.

Module-level aliases (``_detect_arr_api_base``, ``_arr_service``, …)
preserve the import surface used by ``arr_ops``, ``qbit_ops``,
``sab_ops``, ``hygiene_ops``, ``prowlarr_ops``, ``runtime_ops`` and the
test suite.
"""

from __future__ import annotations

from typing import Any, Callable

from media_stack.application.servarr.arr_queue_cleanup_service import ArrQueueCleanupService
from media_stack.application.servarr.arr_service import ArrService
from media_stack.infrastructure.servarr.runtime.common import (
    get_arr_quality_profile,
    normalize_remote_path_mappings,
    resolve_arr_quality_preferences,
)
from media_stack.services.apps.prowlarr.indexer_sync_service import ArrIndexerSyncService
from media_stack.services.auth_service import AuthService
from media_stack.services.health_service import HealthService
from media_stack.services.runtime_platform import (
    bool_cfg,
    coerce_list,
    field_list,
    field_map,
    http_request,
    log,
    normalize_token,
    normalize_url,
    resolve_path,
    to_int,
)
from media_stack.services.runtime_service_registry import (
    get_runtime_binding,
    get_runtime_context_cfg,
    resolve_app_service_class,
)


class ServarrTechnologyHelpers:
    """Small URL/category/token helpers shared by the runtime factory.

    Methods retain their leading underscore on purpose: they are private
    helpers and the ``NO_TYPE_HINTS_PUBLIC_METHODS`` ratchet treats
    underscore-prefixed methods as exempt.
    """

    def __init__(
        self,
        *,
        get_runtime_context_cfg: Callable[[], Any] = get_runtime_context_cfg,
        get_runtime_binding: Callable[[str], str] = get_runtime_binding,
        arr_service_provider: Callable[[], ArrService] | None = None,
    ) -> None:
        self._get_runtime_context_cfg = get_runtime_context_cfg
        self._get_runtime_binding = get_runtime_binding
        self._arr_service_provider = arr_service_provider

    def bind_arr_service_provider(
        self, provider: Callable[[], ArrService]
    ) -> None:
        """Late-bind the provider for ``_arr_service`` to break the cycle.

        ``_choose_category`` and ``_normalize_mapping_path`` delegate to
        an :class:`ArrService` instance, but the runtime factory owns
        ``_arr_service``. Late binding keeps the helper independent of
        the factory's construction order.
        """
        self._arr_service_provider = provider

    def _detect_arr_api_base(self, app_name, app_url, api_key):
        """Detect API base with retry — delegates to arr_ops.detect_arr_api_base."""
        from .arr_ops import detect_arr_api_base
        return detect_arr_api_base(app_name, app_url, api_key)

    def _choose_category(self, app_cfg, client_cfg):
        if self._arr_service_provider is None:
            raise RuntimeError(
                "ServarrTechnologyHelpers._choose_category requires an arr_service "
                "provider; bind one via bind_arr_service_provider()."
            )
        return self._arr_service_provider().choose_category(app_cfg, client_cfg)

    def _normalize_mapping_path(self, path_value):
        if self._arr_service_provider is None:
            raise RuntimeError(
                "ServarrTechnologyHelpers._normalize_mapping_path requires an "
                "arr_service provider; bind one via bind_arr_service_provider()."
            )
        return self._arr_service_provider().normalize_mapping_path(path_value)

    def _canonicalize_technology(self, token: str) -> str:
        raw = str(token or "").strip().lower()
        if not raw:
            return ""
        runtime_context = self._get_runtime_context_cfg()
        aliases = (
            runtime_context.get("technology_aliases")
            if isinstance(runtime_context, dict)
            else {}
        )
        if isinstance(aliases, dict):
            alias_value = aliases.get(raw)
            alias_token = str(alias_value or "").strip().lower()
            if alias_token:
                return alias_token
        return raw

    def _infer_torrent_client_technology(self, cfg=None) -> str:
        if isinstance(cfg, dict):
            for key in ("_technology_key", "_technology", "technology", "client_key"):
                value = self._canonicalize_technology(str(cfg.get(key) or ""))
                if value:
                    return value
        runtime_bound = self._canonicalize_technology(
            self._get_runtime_binding("torrent_client")
        )
        if runtime_bound:
            return runtime_bound
        return ""

    def _infer_usenet_client_technology(self, cfg=None) -> str:
        if isinstance(cfg, dict):
            for key in ("_technology_key", "_technology", "technology", "client_key"):
                value = self._canonicalize_technology(str(cfg.get(key) or ""))
                if value:
                    return value
        runtime_bound = self._canonicalize_technology(
            self._get_runtime_binding("usenet_client")
        )
        if runtime_bound:
            return runtime_bound
        return ""


class ServarrRuntimeFactory:
    """Constructs runtime service instances for Servarr operations.

    All runtime-platform primitives and the service-registry resolver
    are constructor-injected so unit tests can swap them with fakes.
    """

    def __init__(
        self,
        *,
        helpers: ServarrTechnologyHelpers,
        resolve_app_service_class: Callable[..., Any] = resolve_app_service_class,
        http_request: Callable[..., Any] = http_request,
        log: Callable[..., Any] = log,
        field_map: Callable[..., Any] = field_map,
        field_list: Callable[..., Any] = field_list,
        coerce_list: Callable[..., Any] = coerce_list,
        to_int: Callable[..., Any] = to_int,
        bool_cfg: Callable[..., Any] = bool_cfg,
        normalize_url: Callable[..., Any] = normalize_url,
        normalize_token: Callable[..., Any] = normalize_token,
        resolve_path: Callable[..., Any] = resolve_path,
        normalize_remote_path_mappings: Callable[..., Any] = normalize_remote_path_mappings,
        resolve_arr_quality_preferences: Callable[..., Any] = resolve_arr_quality_preferences,
        get_arr_quality_profile: Callable[..., Any] = get_arr_quality_profile,
    ) -> None:
        self._helpers = helpers
        self._resolve_app_service_class = resolve_app_service_class
        self._http_request = http_request
        self._log = log
        self._field_map = field_map
        self._field_list = field_list
        self._coerce_list = coerce_list
        self._to_int = to_int
        self._bool_cfg = bool_cfg
        self._normalize_url = normalize_url
        self._normalize_token = normalize_token
        self._resolve_path = resolve_path
        self._normalize_remote_path_mappings = normalize_remote_path_mappings
        self._resolve_arr_quality_preferences = resolve_arr_quality_preferences
        self._get_arr_quality_profile = get_arr_quality_profile

    def arr_service(self, cfg=None) -> ArrService:
        service_cls = self._resolve_app_service_class("arr_service", ArrService)
        return service_cls(
            http_request=self._http_request,
            log=self._log,
            field_map=self._field_map,
            field_list=self._field_list,
            coerce_list=self._coerce_list,
            to_int=self._to_int,
            normalize_remote_path_mappings=self._normalize_remote_path_mappings,
        )

    def torrent_client_service(self, cfg=None) -> Any:
        technology = self._helpers._infer_torrent_client_technology(cfg)
        if not technology:
            raise RuntimeError(
                "Unable to resolve active torrent client technology for runtime operation. "
                "Set technology_bindings.torrent_client and ensure runtime context is initialized."
            )
        service_cls = self._resolve_app_service_class(
            "torrent_client_service",
            object,
            technology=technology,
        )
        return service_cls(
            log=self._log,
            normalize_url=self._normalize_url,
            bool_cfg=self._bool_cfg,
            to_int=self._to_int,
            coerce_list=self._coerce_list,
        )

    def usenet_client_service(self, cfg=None) -> Any:
        technology = self._helpers._infer_usenet_client_technology(cfg)
        if not technology:
            raise RuntimeError(
                "Unable to resolve active usenet client technology for runtime operation. "
                "Set technology_bindings.usenet_client and ensure runtime context is initialized."
            )
        service_cls = self._resolve_app_service_class(
            "usenet_client_service",
            object,
            technology=technology,
        )
        return service_cls(
            http_request=self._http_request,
            normalize_url=self._normalize_url,
            normalize_mapping_path=self._helpers._normalize_mapping_path,
            choose_category=self._helpers._choose_category,
            coerce_list=self._coerce_list,
            resolve_path=self._resolve_path,
            log=self._log,
        )

    def prowlarr_service(self, cfg=None) -> Any:
        service_cls = self._resolve_app_service_class(
            "prowlarr_service", object, technology="prowlarr"
        )
        return service_cls(
            http_request=self._http_request,
            field_map=self._field_map,
            field_list=self._field_list,
            log=self._log,
        )

    def arr_indexer_sync_service(self, cfg=None) -> ArrIndexerSyncService:
        service_cls = self._resolve_app_service_class(
            "arr_indexer_sync_service", ArrIndexerSyncService
        )
        return service_cls(
            http_request=self._http_request,
            detect_arr_api_base=self._helpers._detect_arr_api_base,
            log=self._log,
        )

    def servarr_policy_service(self, cfg=None) -> Any:
        service_cls = self._resolve_app_service_class("servarr_policy_service", object)
        return service_cls(
            http_request=self._http_request,
            bool_cfg=self._bool_cfg,
            coerce_list=self._coerce_list,
            normalize_token=self._normalize_token,
            to_int=self._to_int,
            resolve_arr_quality_preferences=self._resolve_arr_quality_preferences,
            get_arr_quality_profile=self._get_arr_quality_profile,
            log=self._log,
        )

    def arr_queue_cleanup_service(self, cfg=None) -> ArrQueueCleanupService:
        service_cls = self._resolve_app_service_class(
            "arr_queue_cleanup_service", ArrQueueCleanupService
        )
        return service_cls(
            http_request=self._http_request,
            bool_cfg=self._bool_cfg,
            coerce_list=self._coerce_list,
            to_int=self._to_int,
            normalize_token=self._normalize_token,
            resolve_arr_overrides_by_app=(
                lambda cfg_section, app_cfg: self.servarr_policy_service().resolve_overrides_by_app(
                    cfg_section,
                    app_cfg,
                )
            ),
            log=self._log,
        )

    def health_service(self, cfg=None) -> HealthService:
        service_cls = self._resolve_app_service_class("health_service", HealthService)
        return service_cls(
            http_request=self._http_request,
            log=self._log,
        )

    def auth_service(self, cfg=None) -> AuthService:
        service_cls = self._resolve_app_service_class("auth_service", AuthService)
        return service_cls(
            http_request=self._http_request,
            log=self._log,
            bool_cfg=self._bool_cfg,
        )


# ---------------------------------------------------------------------------
# Module-level singletons + aliases preserve the legacy call-surface used by
# arr_ops/qbit_ops/sab_ops/hygiene_ops/prowlarr_ops and the existing tests.
# ---------------------------------------------------------------------------
_HELPERS = ServarrTechnologyHelpers()
_FACTORY = ServarrRuntimeFactory(helpers=_HELPERS)
_HELPERS.bind_arr_service_provider(_FACTORY.arr_service)

_detect_arr_api_base = _HELPERS._detect_arr_api_base
_choose_category = _HELPERS._choose_category
_normalize_mapping_path = _HELPERS._normalize_mapping_path
_canonicalize_technology = _HELPERS._canonicalize_technology
_infer_torrent_client_technology = _HELPERS._infer_torrent_client_technology
_infer_usenet_client_technology = _HELPERS._infer_usenet_client_technology

_arr_service = _FACTORY.arr_service
_torrent_client_service = _FACTORY.torrent_client_service
_usenet_client_service = _FACTORY.usenet_client_service
_prowlarr_service = _FACTORY.prowlarr_service
_arr_indexer_sync_service = _FACTORY.arr_indexer_sync_service
_servarr_policy_service = _FACTORY.servarr_policy_service
_arr_queue_cleanup_service = _FACTORY.arr_queue_cleanup_service
_health_service = _FACTORY.health_service
_auth_service = _FACTORY.auth_service
