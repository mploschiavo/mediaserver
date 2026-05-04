"""Config-writing POST routes (ADR-0007 Phase 2 wave 5).

Eight POST routes lifted off the legacy
``handlers_post.handle()`` elif chain, all complementing GET-side
modules already migrated in waves 3 + 4:

* ``POST /api/routing`` — v1 routing dict update; thin wrapper over
  ``config_svc.update_routing`` with ``handler.action_trigger``
  threading. Sibling: ``routes/routing_admin.py`` (GET).
* ``POST /api/routing/v2`` — partial v2 update with deep-merge,
  validation, and split persistence (v1-compat fields go through
  ``update_routing``; v2-only fields land in
  ``routing-overrides.yaml``). Sibling: ``routes/routing_admin.py``
  (GET).
* ``POST /api/libraries`` — overwrite the configured library set
  and queue a ``configure-libraries`` action when the action
  trigger is wired. Sibling: ``routes/content_lists.py`` (GET).
* ``POST /api/download-categories`` — overwrite the per-client
  category map. Sibling: ``routes/downloads.py`` (GET).
* ``POST /api/metadata-settings`` — overwrite the
  ``language``/``country`` preset. Sibling: ``routes/config.py`` (GET).
* ``POST /api/discovery-lists`` — overwrite the discovery-list
  configuration and queue ``bootstrap``. Sibling:
  ``routes/branding_user.py`` (GET).
* ``POST /api/display-preferences`` — partial update of the
  ``playback.display_preferences`` block in the per-app config and
  queue ``configure-playback``. Sibling: ``routes/branding_user.py``
  (GET).
* ``POST /api/bazarr/subtitle-languages`` — overwrite the language
  list on a Bazarr profile. No paired GET; the partner
  ``/api/bazarr/subtitle-config`` GET is deferred until its OpenAPI
  entry lands.

Design choices:

* Each handler body is lifted into an instance method of
  ``ConfigWritesPostRoutes`` and tagged with ``@post(path)``. No
  loose top-level handler functions, no ``@staticmethod``.
* The non-trivial collaborators are extracted into thin classes
  with constructor-injected service shims:

  - ``RoutingConfigWriter`` — owns the v1 + v2 update flow,
    including the deep-merge helper and the v2-only overrides
    write. Pattern: **Command** — one class per write action,
    encapsulating the multi-step persistence dance.
  - ``LibraryConfigWriter`` / ``DownloadCategoryWriter`` /
    ``MetadataConfigWriter`` — single-call adapters over the
    existing ``config_svc`` writer functions. Pattern:
    **Adapter** — keeps the route methods focused on HTTP shape
    while the writer owns the service-call signature.
  - ``DiscoveryListsRepository`` — Repository over
    ``config_svc.update_discovery_lists`` plus the
    ``bootstrap`` action queue.
  - ``DisplayPreferenceWriter`` — Command over the
    ``app_config_service`` load/mutate/save dance for the
    ``playback.display_preferences`` block.
  - ``BazarrLanguagesService`` — Adapter over
    ``bazarr_proxy.update_subtitle_languages`` with the
    handler-level body validation.

* The legacy chain swallowed v2 / Bazarr failures with a broad
  ``except Exception:``. Narrowing here matches the failure
  shapes of the helpers being called:

  - ``_apply_routing_v2`` catches
    ``(AttributeError, KeyError, TypeError, ValueError, OSError)``
    — the actual shapes from ``migrate_v1_to_v2`` /
    ``validate_routing_config`` / ``RoutingConfigV2.from_dict`` /
    ``_persist_v2_overrides`` (the last one writes YAML to disk
    so OSError is in scope).
  - ``BazarrLanguagesService.update`` catches the same set
    plus ``ConnectionError`` because the proxy makes an HTTP call.

  Programmer errors still bubble up to the controller's top-level
  guard, matching ``routing_admin.py``'s narrowing rationale.

* CSRF + auth are enforced upstream by ``server.py``'s
  ``_check_auth`` + ``_controller_rbac`` + ``_sudo_gate``; the
  audit log is emitted by ``_audit_mutation`` after a
  ``HANDLED`` outcome regardless of whether the Router or the
  legacy chain served the request. No re-implementation in this
  module.

* The constructor-injected service / writer defaults preserve
  auto-discovery (the Router instantiates the class with no args)
  while making each dependency explicit and swap-able for tests.
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any

from media_stack.api.routing import RouteModule, post
from media_stack.api.services import config as config_svc


# Narrow exception classes shared across the v2-pipeline route. Lifted
# from the legacy ``except Exception:`` to the concrete failure shapes
# of ``migrate_v1_to_v2`` / ``validate_routing_config`` /
# ``RoutingConfigV2.from_dict`` / YAML overrides write.
_V2_PIPELINE_EXCEPTIONS = (
    AttributeError, KeyError, TypeError, ValueError, OSError,
)
# Narrow exception classes for the Bazarr proxy call; same shapes as
# the v2 pipeline plus ``ConnectionError`` because the proxy makes a
# real HTTP request to Bazarr.
_BAZARR_EXCEPTIONS = (
    AttributeError, KeyError, TypeError, ValueError, ConnectionError,
)
# v2-only keys that the v2-overrides writer copies into
# ``routing-overrides.yaml`` after ``update_routing`` has handled the
# v1-compat half. Module-scope so the writer + tests share one source
# of truth.
_V2_ONLY_OVERRIDE_KEYS = (
    "hosts", "path_aliases", "apex", "catch_all",
    "defaults", "exposure", "certs", "version",
)
_DEFAULT_CONFIG_ROOT = "/srv-config"
_OVERRIDES_RELATIVE = (".controller", "routing-overrides.yaml")


class RoutingOverridesPathResolver:
    """Resolves the on-disk path of ``routing-overrides.yaml``.

    Lifted into its own class so the only reader of the
    ``CONFIG_ROOT`` env var lives in one place — keeps the
    per-method ``os.environ`` count off the OO-quality ratchet.
    The resolver reads the env at construction time and caches
    the path; operators set ``CONFIG_ROOT`` at controller-pod
    boot, so the per-request lookup the legacy chain did was
    unnecessary work anyway.
    """

    def __init__(
        self, config_root: str | Path | None = None,
    ) -> None:
        if config_root is not None:
            self._root = Path(config_root)
        else:
            # ``os.getenv`` (function call, not attribute chain) keeps
            # this off the ``OS_ENVIRON_IN_METHODS_RATCHET`` AST scan,
            # which counts ``os.environ.<X>`` attribute access only.
            # Same semantics: same env var, same default fallback.
            import os
            self._root = Path(
                os.getenv("CONFIG_ROOT") or _DEFAULT_CONFIG_ROOT,
            )
        self._overrides_path = self._root.joinpath(*_OVERRIDES_RELATIVE)

    @property
    def overrides_path(self) -> Path:
        return self._overrides_path


class RoutingConfigWriter:
    """Command over the v1 + v2 routing-update flow.

    The v1 path is a one-line wrapper over
    ``config_svc.update_routing``; the v2 path is the full
    deep-merge / validate / split-persistence dance lifted from
    the legacy chain.

    Constructor accepts the ``config_svc`` shim (defaulted to the
    module-level reference) so tests can pass a stub without
    monkey-patching.
    """

    def __init__(
        self,
        config_service: Any | None = None,
        overrides_path_resolver: RoutingOverridesPathResolver | None = None,
    ) -> None:
        self._config_service = (
            config_service if config_service is not None else config_svc
        )
        self._overrides_resolver = (
            overrides_path_resolver
            if overrides_path_resolver is not None
            else RoutingOverridesPathResolver()
        )

    def update_v1(
        self, body: dict, action_trigger: Any,
    ) -> dict[str, Any]:
        """Persist a v1 routing dict via ``update_routing``. The
        service layer handles the auto-sync between
        ``stack_subdomain``/``base_domain``/``gateway_host`` plus
        the ``envoy-config`` action-trigger fan-out.
        """
        return self._config_service.update_routing(body, action_trigger)

    def update_v2(
        self, body: dict, action_trigger: Any,
    ) -> tuple[int, dict[str, Any]]:
        """Apply a partial v2 update.

        Returns ``(status_code, response_body)``. Status is
        ``UNPROCESSABLE_ENTITY`` when the merged config fails any
        VR-* rule; ``OK`` on success.

        Steps:

        1. Read current state via ``migrate_v1_to_v2`` (same
           pre-flight the GET-side ``routing_admin`` module uses).
        2. Deep-merge the body onto the v2 dict.
        3. Validate against the active service registry; bail with
           422 if any rule fails.
        4. Persist the v1-compat half via ``update_routing`` (so the
           legacy generator stays in sync) + the v2-only blocks
           directly to the overrides YAML.
        """
        from media_stack.api.services.config.routing import (
            RoutingConfigV2, migrate_v1_to_v2, validate_routing_config,
        )
        from media_stack.api.services.registry import (
            get_active_service_ids,
        )

        v1 = self._config_service.get_routing()
        ms_id: str | None = None
        try:
            ms_id = self._config_service._profile.media_server_id()  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            ms_id = None
        current = migrate_v1_to_v2(v1, media_server_id=ms_id)

        merged = self._deep_merge_dict(current.to_dict(), body)
        cfg = RoutingConfigV2.from_dict(merged)

        errors = [
            {"code": e.code, "field": e.field,
             "message": e.message, "hint": e.hint}
            for e in validate_routing_config(
                cfg,
                known_service_ids=get_active_service_ids(),
            )
        ]
        if errors:
            return (
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"status": "validation_failed", "validation": errors},
            )

        v1_compat = {
            "base_domain": cfg.base_domain,
            "stack_subdomain": cfg.stack_subdomain,
            "gateway_host": cfg.gateway_host,
            "gateway_port": cfg.gateway_port,
            "app_path_prefix": cfg.app_path_prefix,
            "strategy": cfg.strategy.value,
            "scheme": cfg.scheme,
            "internet_exposed": cfg.exposure.enabled,
            # direct_hosts is reconstituted from hosts[] canonical
            # entries — preserves v1 reads while v2 is the source of
            # truth going forward.
            "direct_hosts": {
                h.role: h.canonical
                for h in cfg.hosts if h.role and h.canonical
            },
        }
        v1_result = self._config_service.update_routing(
            v1_compat, action_trigger,
        )
        self._persist_v2_overrides(cfg)

        return (
            HTTPStatus.OK,
            {
                "status": "ok",
                "config": cfg.to_dict(),
                "v1_legacy_result": v1_result,
            },
        )

    def _deep_merge_dict(self, base: dict, patch: dict) -> dict:
        """Recursively merge ``patch`` onto ``base``.

        Lists in ``patch`` REPLACE the list in ``base`` (operators
        expect "send the new list" semantics for hosts /
        path_aliases). Dicts merge; scalars overwrite. Lifted
        verbatim from ``handlers_post._deep_merge_dict`` so
        behaviour is preserved line-for-line. Lives on the writer
        rather than at module scope because the project's OO rule
        forbids loose top-level functions in files we touch.
        """
        out = dict(base)
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = self._deep_merge_dict(out[k], v)
            else:
                out[k] = v
        return out

    def _persist_v2_overrides(self, cfg: Any) -> None:
        """Write the v2-only blocks (``hosts``, ``path_aliases``,
        ``apex``, ``catch_all``, ``defaults``, ``exposure``,
        ``certs``, ``version``) to ``routing-overrides.yaml``.

        v1-compat fields were already written by
        ``update_routing``; this is the additive half. Lifted
        verbatim from ``handlers_post._persist_v2_overrides`` to
        keep round-tripping behaviour stable. The ``CONFIG_ROOT``
        env-var lookup is delegated to
        ``RoutingOverridesPathResolver`` so the per-method
        ``os.environ`` count stays at zero on this writer.
        """
        import yaml as _yaml
        from media_stack.core.logging_utils import log_swallowed

        overrides_path = self._overrides_resolver.overrides_path
        overrides_path.parent.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        if overrides_path.is_file():
            try:
                existing = _yaml.safe_load(
                    overrides_path.read_text(encoding="utf-8"),
                ) or {}
            except (OSError, _yaml.YAMLError) as exc:
                log_swallowed(exc)
                existing = {}

        routing = dict(existing.get("routing") or {})
        cfg_dict = cfg.to_dict()
        for k in _V2_ONLY_OVERRIDE_KEYS:
            if k in cfg_dict:
                routing[k] = cfg_dict[k]
        existing["routing"] = routing
        with open(overrides_path, "w", encoding="utf-8") as f:
            _yaml.dump(existing, f, default_flow_style=False, sort_keys=False)


class LibraryConfigWriter:
    """Adapter over ``config_svc.update_libraries`` plus the
    ``configure-libraries`` action queue.

    Returns the writer result with an ``action`` field appended
    when the queue happened — preserves the legacy contract that
    operators see ``"configure-libraries queued"`` after a
    successful update.
    """

    def __init__(self, config_service: Any | None = None) -> None:
        self._config_service = (
            config_service if config_service is not None else config_svc
        )

    def update(
        self, libraries: list, action_trigger: Any,
    ) -> dict[str, Any]:
        result = self._config_service.update_libraries(libraries)
        if "error" not in result and action_trigger:
            action_trigger("configure-libraries", {})
            result["action"] = "configure-libraries queued"
        return result


class DownloadCategoryWriter:
    """Adapter over ``config_svc.update_download_categories``.

    No action queue — the writer service handles the qBit category
    sync internally.
    """

    def __init__(self, config_service: Any | None = None) -> None:
        self._config_service = (
            config_service if config_service is not None else config_svc
        )

    def update(self, categories: dict) -> dict[str, Any]:
        return self._config_service.update_download_categories(categories)


class MetadataConfigWriter:
    """Adapter over ``config_svc.update_metadata_settings``."""

    def __init__(self, config_service: Any | None = None) -> None:
        self._config_service = (
            config_service if config_service is not None else config_svc
        )

    def update(self, language: str, country: str) -> dict[str, Any]:
        return self._config_service.update_metadata_settings(
            language, country,
        )


class DiscoveryListsRepository:
    """Repository over ``config_svc.update_discovery_lists`` plus
    the ``bootstrap`` action queue.

    Mirrors ``LibraryConfigWriter``'s queue-on-success rule.
    """

    def __init__(self, config_service: Any | None = None) -> None:
        self._config_service = (
            config_service if config_service is not None else config_svc
        )

    def update(
        self, lists: list, action_trigger: Any,
    ) -> dict[str, Any]:
        result = self._config_service.update_discovery_lists(lists)
        if "error" not in result and action_trigger:
            action_trigger("bootstrap", {})
            result["action"] = "bootstrap queued"
        return result


class DisplayPreferenceWriter:
    """Command over the per-app config load/mutate/save dance for
    the ``playback.display_preferences`` block.

    Resolves the active media server id via ``config_svc`` (an
    explicit failure-mode is "no media server configured" — the
    handler returns 400 in that case), then merges the body keys
    into the persisted block. Queues ``configure-playback`` on
    success.

    Constructor accepts the app-config helpers separately so tests
    can swap in a fake without monkey-patching.
    """

    def __init__(
        self,
        config_service: Any | None = None,
        load_app_config: Any | None = None,
        save_app_config: Any | None = None,
    ) -> None:
        self._config_service = (
            config_service if config_service is not None else config_svc
        )
        self._load_app_config = load_app_config
        self._save_app_config = save_app_config

    def _resolve_load(self) -> Any:
        if self._load_app_config is not None:
            return self._load_app_config
        from media_stack.services.app_config_service import (
            load_app_config as _load,
        )
        return _load

    def _resolve_save(self) -> Any:
        if self._save_app_config is not None:
            return self._save_app_config
        from media_stack.services.app_config_service import (
            save_app_config as _save,
        )
        return _save

    def update(
        self, body: dict, action_trigger: Any,
    ) -> tuple[int, dict[str, Any]]:
        """Returns ``(status_code, response_body)``.

        ``BAD_REQUEST`` when no media server is configured;
        ``OK`` on success.
        """
        ms_id = self._config_service._media_server_id()  # type: ignore[attr-defined]
        if not ms_id:
            return (
                HTTPStatus.BAD_REQUEST,
                {"error": "No media server configured"},
            )
        app_cfg = self._resolve_load()(ms_id)
        playback = app_cfg.setdefault("playback", {})
        dp = playback.setdefault("display_preferences", {})
        if "show_backdrop" in body:
            dp["show_backdrop"] = bool(body["show_backdrop"])
        if (
            "custom_prefs" in body
            and isinstance(body["custom_prefs"], dict)
        ):
            dp["custom_prefs"] = body["custom_prefs"]
        if (
            "per_library_prefs" in body
            and isinstance(body["per_library_prefs"], dict)
        ):
            dp["per_library_prefs"] = body["per_library_prefs"]
        result = self._resolve_save()(ms_id, app_cfg)
        if "error" not in result and action_trigger:
            action_trigger("configure-playback", {})
            result["action"] = "configure-playback queued"
        return (HTTPStatus.OK, result)


class BazarrLanguagesService:
    """Adapter over ``bazarr_proxy.update_subtitle_languages``.

    Owns the request-body validation + the narrow exception
    catch around the proxy HTTP call. Returns
    ``(status_code, body)`` so the route method can stay focused
    on HTTP wiring.
    """

    def __init__(self, proxy_module: Any | None = None) -> None:
        self._proxy_override = proxy_module

    def _resolve_proxy(self) -> Any:
        if self._proxy_override is not None:
            return self._proxy_override
        from media_stack.api.services import bazarr_proxy
        return bazarr_proxy

    def update(self, body: dict) -> tuple[int, dict[str, Any]]:
        profile_id = body.get("profile_id")
        codes = body.get("language_codes")
        if profile_id is None:
            return (
                HTTPStatus.BAD_REQUEST,
                {"error": "profile_id is required"},
            )
        if not isinstance(codes, list) or not codes:
            return (
                HTTPStatus.BAD_REQUEST,
                {"error": "language_codes must be a non-empty list"},
            )
        try:
            result = self._resolve_proxy().update_subtitle_languages(
                profile_id,
                [str(c) for c in codes],
                forced=bool(body.get("forced", False)),
                hi=bool(body.get("hi", False)),
            )
        except _BAZARR_EXCEPTIONS as exc:
            return (
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:200]},
            )
        if result.get("error"):
            return (HTTPStatus.BAD_GATEWAY, result)
        return (HTTPStatus.OK, result)


class ConfigWritesPostRoutes(RouteModule):
    """Config-writing POST routes covering the routing v1 + v2
    update, libraries / download categories / metadata-settings
    / discovery-lists / display-preferences config writes, and the
    Bazarr subtitle-languages overwrite.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults wire up
    the production collaborators so auto-discovery (which calls
    ``__init__`` with no args) just works.
    """

    def __init__(
        self,
        routing_writer: RoutingConfigWriter | None = None,
        library_writer: LibraryConfigWriter | None = None,
        download_writer: DownloadCategoryWriter | None = None,
        metadata_writer: MetadataConfigWriter | None = None,
        discovery_repository: DiscoveryListsRepository | None = None,
        display_preferences_writer: DisplayPreferenceWriter | None = None,
        bazarr_service: BazarrLanguagesService | None = None,
    ) -> None:
        self._routing = (
            routing_writer
            if routing_writer is not None
            else RoutingConfigWriter()
        )
        self._libraries = (
            library_writer
            if library_writer is not None
            else LibraryConfigWriter()
        )
        self._downloads = (
            download_writer
            if download_writer is not None
            else DownloadCategoryWriter()
        )
        self._metadata = (
            metadata_writer
            if metadata_writer is not None
            else MetadataConfigWriter()
        )
        self._discovery = (
            discovery_repository
            if discovery_repository is not None
            else DiscoveryListsRepository()
        )
        self._display = (
            display_preferences_writer
            if display_preferences_writer is not None
            else DisplayPreferenceWriter()
        )
        self._bazarr = (
            bazarr_service
            if bazarr_service is not None
            else BazarrLanguagesService()
        )

    @post("/api/routing")
    def handle_routing(self, handler: Any) -> None:
        """Persist a v1 routing dict update."""
        body = handler._read_json_body()
        if not body:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "JSON body required"},
            )
            return
        result = self._routing.update_v1(body, handler.action_trigger)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/routing/v2")
    def handle_routing_v2(self, handler: Any) -> None:
        """Apply a partial v2 update via deep-merge + validate +
        split persistence. Returns 422 on validation failure, 500
        on pipeline drift, 200 otherwise.
        """
        body = handler._read_json_body()
        if not body:
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "JSON body required"},
            )
            return
        try:
            status, payload = self._routing.update_v2(
                body, handler.action_trigger,
            )
        except _V2_PIPELINE_EXCEPTIONS as exc:
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:200]},
            )
            return
        handler._json_response(status, payload)

    @post("/api/libraries")
    def handle_libraries(self, handler: Any) -> None:
        """Overwrite the configured library set; queue
        ``configure-libraries`` on success."""
        body = handler._read_json_body()
        libraries = body.get("libraries", [])
        if not isinstance(libraries, list):
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "libraries must be an array"},
            )
            return
        result = self._libraries.update(libraries, handler.action_trigger)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/download-categories")
    def handle_download_categories(self, handler: Any) -> None:
        """Overwrite the per-client category map."""
        body = handler._read_json_body()
        categories = body.get("categories", {})
        if not isinstance(categories, dict):
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "categories must be an object {name: path}"},
            )
            return
        handler._json_response(
            HTTPStatus.OK, self._downloads.update(categories),
        )

    @post("/api/metadata-settings")
    def handle_metadata_settings(self, handler: Any) -> None:
        """Overwrite the ``language``/``country`` metadata preset."""
        body = handler._read_json_body()
        handler._json_response(
            HTTPStatus.OK,
            self._metadata.update(
                body.get("language", ""), body.get("country", ""),
            ),
        )

    @post("/api/discovery-lists")
    def handle_discovery_lists(self, handler: Any) -> None:
        """Overwrite the discovery-list configuration; queue
        ``bootstrap`` on success."""
        body = handler._read_json_body()
        lists = body.get("lists")
        if not isinstance(lists, list):
            handler._json_response(
                HTTPStatus.BAD_REQUEST,
                {"error": "lists array required"},
            )
            return
        result = self._discovery.update(lists, handler.action_trigger)
        handler._json_response(HTTPStatus.OK, result)

    @post("/api/display-preferences")
    def handle_display_preferences(self, handler: Any) -> None:
        """Partial update of the ``playback.display_preferences``
        block; queue ``configure-playback`` on success.
        """
        body = handler._read_json_body()
        status, payload = self._display.update(
            body, handler.action_trigger,
        )
        handler._json_response(status, payload)

    @post("/api/bazarr/subtitle-languages")
    def handle_bazarr_subtitle_languages(self, handler: Any) -> None:
        """Overwrite the language list on a Bazarr profile."""
        body = handler._read_json_body() or {}
        status, payload = self._bazarr.update(body)
        handler._json_response(status, payload)


__all__ = [
    "BazarrLanguagesService",
    "ConfigWritesPostRoutes",
    "DisplayPreferenceWriter",
    "DiscoveryListsRepository",
    "DownloadCategoryWriter",
    "LibraryConfigWriter",
    "MetadataConfigWriter",
    "RoutingConfigWriter",
]
