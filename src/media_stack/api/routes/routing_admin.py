"""Routing-admin GET routes (ADR-0007 Phase 2 wave 4).

Five operator-facing GET routes lifted off the legacy
``handlers_get.handle()`` elif chain, all under the ``Routing``
OpenAPI tag and powering the dashboard's Routing tab:

* ``GET /api/routing`` — flat v1 routing dict (legacy shape; the
  config-svc reads profile YAML + overrides and emits the
  operator-facing ``{base_domain, gateway_host, strategy,
  direct_hosts, ...}``).
* ``GET /api/routing/v2`` — v1 dict migrated to ``RoutingConfigV2``
  + a sibling ``validation`` array of non-blocking VR-* errors. PR-4
  read-only; PR-5 lands POST + apply.
* ``GET /api/routing/routes`` — flat operator-friendly route table
  enumerating every URL the gateway emits (per-service path-prefix
  + subdomain, path aliases, apex, catch-all).
* ``GET /api/routing/preview`` — pure-function preview pairing the
  generated Envoy ``route_config`` with the active
  ``EdgeBindingAdapter``'s ``ApplyPlan`` for the current v2 config.
* ``GET /api/routing/effective`` — same shape as ``/api/routing/v2``
  but with ``defaults`` merged into each host's per-field knobs, so
  the UI sees what Envoy actually sees.

Design pattern: **Template Method**. Four of the five routes share
the same pre-flight — load the v1 routing dict, resolve
``media_server_id`` from the profile, fold both into a
``RoutingConfigV2`` via ``migrate_v1_to_v2``. That is extracted
into ``_build_v2_config`` so each route method's body stays focused
on its own response shape (route-table enumeration, Envoy preview,
defaults merge, etc.) without re-typing the boilerplate.

Implementation notes:

* The Router consults this module's registrations BEFORE the legacy
  chain (see ``server.py``); the legacy ``elif`` branches in
  ``handlers_get.py`` stay alive only as fallback during Phase 2.
* Behaviour-preserving migration: every method body is lifted
  verbatim from the legacy chain except where re-use of
  ``_build_v2_config`` collapses the boilerplate. Status codes,
  response shapes, and error envelopes match line-for-line.
* The legacy chain swallowed failures with a broad
  ``except Exception:``. The narrowing here catches
  ``(AttributeError, KeyError, TypeError, ValueError)`` — the actual
  shapes returned by ``migrate_v1_to_v2`` /
  ``generate_route_config_v2`` / ``compute_apply_plan`` when given a
  partially-populated routing dict (missing keys, wrong types, or
  unexpected enum values). A broader catch would shadow programmer
  errors during future routing-schema refactors; out-of-band
  exceptions still bubble up to the controller's top-level guard,
  which is the right behaviour for unexpected errors.
* The ``_profile.media_server_id()`` lookup is wrapped in its own
  narrow ``except (AttributeError, OSError)`` because that path
  reads the profile YAML from disk and could trip on either a
  missing private attr (defensive — the legacy chain used the same
  ``# type: ignore[attr-defined]`` pattern) or a file-system error
  if the profile is unreadable. Resolving to ``None`` falls back to
  the migrator's default behaviour.
* ``defaults``-merge constants for ``/api/routing/effective`` live at
  module scope so the merge logic + tests share one source of
  truth. They're route-local fallbacks — a strictly-empty default is
  the operator-visible "inherited from the schema's default" value.
* ``config_svc`` is imported via the ``from .services import config
  as config_svc`` shim (matches the legacy chain) so the test patch
  surface stays at ``media_stack.api.routes.routing_admin.config_svc``.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import config as config_svc


# Route-local fallback defaults for the ``/api/routing/effective``
# merge. Each row corresponds to a per-host field that the v2 schema
# treats as "inherited if unset"; the merge fills in the
# operator-visible value Envoy actually sees so the UI doesn't have
# to second-guess the schema.
_EFFECTIVE_WEBSOCKET_DEFAULT = False
_EFFECTIVE_TIMEOUT_DEFAULT_S = 0
_EFFECTIVE_BODY_LIMIT_DEFAULT_MB = 0

# Narrow exception classes shared across the v2-pipeline routes.
# Lifted from the legacy chain's ``except Exception:`` to the
# concrete failure shapes for ``migrate_v1_to_v2`` /
# ``generate_route_config_v2`` / ``compute_apply_plan`` /
# ``validate_routing_config`` when fed partial / drifted input.
_V2_PIPELINE_EXCEPTIONS = (
    AttributeError, KeyError, TypeError, ValueError,
)


class RoutingAdminGetRoutes(RouteModule):
    """Routing-tag GET routes covering the v1 read, v2 migration,
    operator-facing route-table enumeration, Envoy preview, and the
    defaults-merged "effective" view. The Router auto-discovers +
    instantiates this class + walks its tagged methods at startup.

    Pattern: **Template Method** — ``_build_v2_config`` is the
    shared pre-flight; the per-route methods are the customised
    response-shaping steps.
    """

    @get("/api/routing")
    def handle_routing(self, handler: Any) -> None:
        """Return the legacy v1 routing dict (flat shape with
        ``base_domain``, ``gateway_host``, ``strategy``,
        ``direct_hosts``, etc.). Read-only thin wrapper over
        ``config_svc.get_routing`` — the service layer handles
        profile YAML + overrides merge.
        """
        handler._json_response(HTTPStatus.OK, config_svc.get_routing())

    @get("/api/routing/v2")
    def handle_routing_v2(self, handler: Any) -> None:
        """Return the v2 view of the routing config: the v1 dict
        migrated through ``migrate_v1_to_v2`` plus a sibling
        ``validation`` array of non-blocking VR-* errors. The UI
        renders the validation entries inline so operators see what
        the schema isn't happy about without being blocked from
        reading the config (v1 → v2 migration may produce a config
        that fails some VRs, e.g. apex default = NONE which is fine
        but other implicit defaults may not be).
        """
        from media_stack.api.services.config.routing import (
            migrate_v1_to_v2,
            validate_routing_config,
        )
        try:
            cfg = self._build_v2_config(migrate_v1_to_v2)
            errors = [
                {"code": e.code, "field": e.field,
                 "message": e.message, "hint": e.hint}
                for e in validate_routing_config(cfg)
            ]
            handler._json_response(HTTPStatus.OK, {
                "config": cfg.to_dict(),
                "validation": errors,
            })
        except _V2_PIPELINE_EXCEPTIONS as exc:
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:200]},
            )

    @get("/api/routing/routes")
    def handle_routing_routes(self, handler: Any) -> None:
        """Operator-facing route table. Enumerates every URL the
        gateway emits, flattened into rows with ``host`` / ``match``
        / ``target`` / ``kind`` / ``source`` so the UI can render a
        sortable table.

        Source rows:
          1. Per-service path-prefix routes (``/app/<svc>/``) when
             strategy is ``path`` or ``hybrid``.
          2. Per-service subdomain + explicit-path entries from the
             v2 ``hosts[]`` list, plus host aliases as redirects.
          3. Path aliases (HTTP redirects).
          4. Apex action (when not ``none``).
          5. Catch-all action.
        """
        from media_stack.api.services.config.routing import (
            migrate_v1_to_v2,
        )
        from media_stack.core.service_registry.registry import (
            SERVICES, get_active_service_ids,
        )
        try:
            cfg = self._build_v2_config(migrate_v1_to_v2)

            rows: list[dict] = []
            strategy = cfg.strategy.value
            gw = cfg.gateway_host
            app_prefix = cfg.app_path_prefix or "/app"
            active = get_active_service_ids()

            # 1. Per-service path-prefix routes (auto, derived).
            if strategy in ("path", "hybrid") and gw:
                for svc in SERVICES:
                    if not svc.web_ui or svc.id not in active:
                        continue
                    rows.append({
                        "host": gw,
                        "match": f"{app_prefix}/{svc.id}/",
                        "target": svc.id,
                        "target_kind": "service",
                        "kind": "auto_path",
                        "source": (
                            f"strategy={strategy}, app_path_prefix="
                            f"{app_prefix} (derived per-service)"
                        ),
                    })

            # 2. Explicit hosts from the v2 config (direct_hosts in
            # v1, hosts[] in v2).
            for h in cfg.hosts:
                if h.canonical and h.canonical != gw:
                    rows.append({
                        "host": h.canonical,
                        "match": "/" if not h.path_prefix else h.path_prefix,
                        "target": h.service_id,
                        "target_kind": "service",
                        "kind": "subdomain",
                        "source": f"hosts[] entry (role={h.role})",
                    })
                if h.canonical == gw and h.path_prefix:
                    rows.append({
                        "host": gw,
                        "match": (
                            h.path_prefix
                            if h.path_prefix.endswith("/")
                            else h.path_prefix + "/"
                        ),
                        "target": h.service_id,
                        "target_kind": "service",
                        "kind": "explicit_path",
                        "source": (
                            f"hosts[] entry (role={h.role}, "
                            "explicit path_prefix override)"
                        ),
                    })
                # Aliases redirect to canonical.
                for alias in h.aliases:
                    rows.append({
                        "host": alias,
                        "match": "/",
                        "target": h.canonical,
                        "target_kind": "redirect",
                        "kind": "host_alias",
                        "source": f"hosts[].aliases for {h.canonical}",
                    })

            # 3. Path aliases (HTTP redirects).
            for p in cfg.path_aliases:
                if not p.from_path or not p.to_path:
                    continue
                rows.append({
                    "host": gw,
                    "match": p.from_path,
                    "target": p.to_path,
                    "target_kind": "redirect",
                    "kind": "path_alias",
                    "source": f"path_aliases[] ({p.code})",
                })

            # 4. Apex.
            if cfg.apex.action.value != "none":
                rows.append({
                    "host": gw,
                    "match": "/ (exact)",
                    "target": (
                        cfg.apex.target
                        or f"({cfg.apex.action.value})"
                    ),
                    "target_kind": (
                        "redirect" if cfg.apex.action.value == "redirect"
                        else cfg.apex.action.value
                    ),
                    "kind": "apex",
                    "source": "apex.action",
                })

            # 5. Catch-all.
            ca = cfg.catch_all
            rows.append({
                "host": gw,
                "match": "/ (catch-all)",
                "target": ca.target or f"({ca.action.value})",
                "target_kind": (
                    "redirect" if ca.action.value == "redirect"
                    else ca.action.value
                ),
                "kind": "catch_all",
                "source": "catch_all.action",
            })

            handler._json_response(HTTPStatus.OK, {
                "rows": rows,
                "summary": {
                    "strategy": strategy,
                    "gateway_host": gw,
                    "app_path_prefix": app_prefix,
                    "active_service_count": len(active),
                },
            })
        except _V2_PIPELINE_EXCEPTIONS as exc:
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:200]},
            )

    @get("/api/routing/preview")
    def handle_routing_preview(self, handler: Any) -> None:
        """Pure-function preview: Envoy ``route_config`` plus the
        active ``EdgeBindingAdapter``'s ``ApplyPlan`` for the
        *current* v2 config (no body to POST in PR-5; that lands in
        a follow-up). Operators see what would actually get applied
        without touching the cluster.

        ``K8sIngressAdapter`` is the only adapter shipped today;
        chosen unconditionally. PR-7 extends this with auto-detect.
        """
        from media_stack.api.services.config.routing import (
            migrate_v1_to_v2,
        )
        from media_stack.services.edge.envoy_route_generator_v2 import (
            generate_route_config_v2,
        )
        from media_stack.services.edge.k8s_ingress_adapter import (
            K8sIngressAdapter,
        )
        try:
            cfg = self._build_v2_config(migrate_v1_to_v2)
            route_config = generate_route_config_v2(cfg)
            plan = K8sIngressAdapter().compute_apply_plan(cfg)
            handler._json_response(HTTPStatus.OK, {
                "envoy": {
                    "route_config": route_config,
                    "vhost_count": len(
                        route_config.get("virtual_hosts", []),
                    ),
                },
                "binding": {
                    "adapter": "k8s_ingress",
                    "steps": [
                        {
                            "kind": s.kind,
                            "description": s.description,
                            "payload": s.payload,
                        }
                        for s in plan.steps
                    ],
                    "warnings": list(plan.warnings),
                },
            })
        except _V2_PIPELINE_EXCEPTIONS as exc:
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:200]},
            )

    @get("/api/routing/effective")
    def handle_routing_effective(self, handler: Any) -> None:
        """Same shape as ``/api/routing/v2`` but with ``defaults``
        merged into each host's per-field knobs, so the UI shows
        what Envoy actually sees instead of operator-facing
        "inherited" placeholders. Read-only.

        Per-host explicit values always win — only fields the host
        left unset (None / empty / falsy) get filled from
        ``defaults``.
        """
        from media_stack.api.services.config.routing import (
            migrate_v1_to_v2,
        )
        try:
            cfg = self._build_v2_config(migrate_v1_to_v2)
            eff = cfg.to_dict()
            defaults = eff.get("defaults") or {}
            for h in eff.get("hosts", []):
                if not h.get("websocket"):
                    h["websocket"] = defaults.get(
                        "websocket", _EFFECTIVE_WEBSOCKET_DEFAULT,
                    )
                if not h.get("auth") and defaults.get("auth"):
                    h["auth"] = dict(defaults["auth"])
                if not h.get("timeout_seconds"):
                    h["timeout_seconds"] = defaults.get(
                        "timeout_seconds", _EFFECTIVE_TIMEOUT_DEFAULT_S,
                    )
                if not h.get("body_limit_mb"):
                    h["body_limit_mb"] = defaults.get(
                        "body_limit_mb",
                        _EFFECTIVE_BODY_LIMIT_DEFAULT_MB,
                    )
                if not h.get("headers") and defaults.get("headers"):
                    h["headers"] = dict(defaults["headers"])
            handler._json_response(HTTPStatus.OK, {"config": eff})
        except _V2_PIPELINE_EXCEPTIONS as exc:
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:200]},
            )

    # --- shared pre-flight (Template Method's invariant step) -------

    def _build_v2_config(self, migrate_v1_to_v2: Any) -> Any:
        """Read the v1 routing dict, resolve ``media_server_id`` from
        the profile, fold both into a ``RoutingConfigV2``. ``migrate``
        is parameter-injected so the caller (which already imports it
        for its own use) can pass the function it's holding rather
        than the helper re-importing.

        ``media_server_id`` lookup is allowed to fail: the legacy
        chain wraps the same call in a defensive try/except because
        the profile path goes through ``# type: ignore[attr-defined]``
        on a semi-private attribute, and a missing media-server
        binding is a normal-startup state. ``None`` falls back to the
        migrator's default ("jellyfin").
        """
        v1 = config_svc.get_routing()
        ms_id: str | None = None
        try:
            ms_id = config_svc._profile.media_server_id()  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            ms_id = None
        return migrate_v1_to_v2(v1, media_server_id=ms_id)


__all__ = ["RoutingAdminGetRoutes"]
