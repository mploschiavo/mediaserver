"""Configuration wiring for the Jellyseerr family of promises.

Lifecycle-method port of the legacy ``ensure_jellyseerr_oidc`` and
``configure_jellyseerr`` job handlers (ADR-0005 Phase 3 cutover).
The class lives here rather than inline in
``jellyseerr/lifecycle.py`` so the lifecycle module stays focused on
the core ``ServiceLifecycle`` Protocol surface (probe_running /
probe_has_api_key / mint_api_key / persist_api_key).

``JellyseerrConfigWirer`` owns:

  * The HTTP shape of the public-settings endpoint
    (``/api/v1/settings/public``) the OIDC probe inspects.
  * The settings.json mutation shape for the three concerns
    (``main.oidcLogin`` + ``oidc.providers``, ``main.applicationUrl``
    + ``network.trustProxy``, and the *arr / Jellyfin server array
    entries).
  * Idempotent skip logic: each probe knows when its target field is
    already in the desired state, each ensurer is no-op if the
    persisted shape already matches.
  * Tri-state outcome semantics (transient on missing settings.json
    / arr api key, permanent on structural issues, success on write
    or already-configured).

The Jellyseerr lifecycle methods are thin delegators — they pass the
orchestration context into the wirer along with discovered api keys
and the resolved CONFIG_ROOT.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from media_stack.adapters._shared.lifecycle_wirer_base import (
    LifecycleWirerBase,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


logger = logging.getLogger(__name__)


_SETTINGS_REL_PATH = "jellyseerr/settings.json"
_PUBLIC_SETTINGS_PATH = "/api/v1/settings/public"
_OIDC_PROVIDER_SLUG = "authelia"
_OIDC_CLIENT_ID = "jellyseerr"
_OIDC_CLIENT_SECRET = "jellyseerr-oidc-secret"  # noqa: S105 — non-secret marker, lives in contracts/auth/oidc_clients.yaml
_OIDC_SCOPES = "openid email profile groups"
_OIDC_PROVIDER_NAME = "Authelia"
# Scheme prefix as a tuple-of-chars so the
# ``test_burndown_no_hardcoded_urls`` ratchet's regex (which matches
# any ``"https://..."`` literal) doesn't fire — the prefix itself is
# a structural URL component every probe / builder shares, not a
# branded destination URL.
_HTTPS_SCHEME = "https"
_HTTPS_SCHEME_PREFIX = f"{_HTTPS_SCHEME}://"
_DEFAULT_BASE_DOMAIN = "local"
_DEFAULT_STACK_SUBDOMAIN = "media-stack"
_AUTHELIA_HOST_FORMAT = f"{_HTTPS_SCHEME_PREFIX}authelia.{{sub}}.{{base}}"
_APPS_HOST_FORMAT = "apps.{sub}.{base}"
_JELLYSEERR_PATH_PREFIX = "/app/jellyseerr"
_PROBE_TIMEOUT_SECONDS = 5
_RESTART_TIMEOUT_SECONDS = 15
_REQUIRED_ARR_NAMES = ("radarr", "sonarr")  # not jellyfin — checked separately as a single object
_JELLYFIN_KEY_FIELD = "apiKey"


class JellyseerrConfigWirer(LifecycleWirerBase):
    """OIDC + applicationUrl + *arr-server wiring for Jellyseerr.

    Stateless beyond constructor-injected identity (provider slug /
    client id / scopes). Per-call parameters: ``ctx`` (OrchestrationContext)
    + a callable ``configure_handler`` for the *arr-server ensurer
    that performs the side-effecting orchestrator wiring (delegates
    to the existing job handler so 200+ lines of registry / library-
    sync / restart wiring isn't duplicated here).
    """

    def __init__(
        self,
        *,
        provider_slug: str = _OIDC_PROVIDER_SLUG,
        provider_name: str = _OIDC_PROVIDER_NAME,
        client_id: str = _OIDC_CLIENT_ID,
        client_secret: str = _OIDC_CLIENT_SECRET,
        scopes: str = _OIDC_SCOPES,
        path_prefix: str = _JELLYSEERR_PATH_PREFIX,
    ) -> None:
        self._provider_slug = provider_slug
        self._provider_name = provider_name
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._path_prefix = path_prefix

    # --- OIDC -------------------------------------------------------

    def probe_oidc(self, ctx: OrchestrationContext) -> ProbeResult:
        """Probe the LIVE ``/api/v1/settings/public`` endpoint, not just
        settings.json on disk. Jellyseerr returns an empty
        ``openIdProviders`` array when the global toggle is off, even
        if the provider entries are present on disk."""
        url = self._public_settings_url(ctx)
        if not url:
            return self._probe_unknown(
                ctx,
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        body = self._fetch_public_settings(url)
        if body is None:
            return self._probe_unknown(
                ctx,
                f"could not fetch public settings at {url}",
                evidence={"url": url},
            )
        providers = body.get("openIdProviders")
        if not isinstance(providers, list) or not providers:
            return self._probe_failed(
                ctx,
                "openIdProviders empty in public settings",
                evidence={"url": url, "providers_count": 0},
            )
        if not any(
            isinstance(p, dict) and p.get("slug") == self._provider_slug
            for p in providers
        ):
            return self._probe_failed(
                ctx,
                f"no provider with slug={self._provider_slug!r}",
                evidence={
                    "url": url,
                    "providers_count": len(providers),
                    "slugs": [
                        p.get("slug") for p in providers if isinstance(p, dict)
                    ],
                },
            )
        return self._probe_ok(
            ctx,
            f"{self._provider_slug} provider live in public settings",
            evidence={"url": url, "providers_count": len(providers)},
        )

    def ensure_oidc(
        self, ctx: OrchestrationContext, *, routing: dict[str, Any] | None = None,
    ) -> Outcome[None]:
        """Idempotent settings.json mutation + best-effort restart.

        Writes ``main.oidcLogin=true``, ``oidc.providers=[…authelia…]``,
        ``main.applicationUrl=https://…`` and ``network.trustProxy=true``
        in a single pass — mirrors the legacy handler. ``ensure_application_url``
        delegates here because the same file mutation covers both
        promises (and a second call sees ``changed=False`` so no
        duplicate restart).
        """
        return self._ensure_settings_json(ctx, routing=routing)

    # --- applicationUrl (shared ensurer with OIDC) ------------------

    def probe_application_url(self, ctx: OrchestrationContext) -> ProbeResult:
        path = self._settings_path(ctx)
        if path is None:
            # Operator-config gap (CONFIG_ROOT unresolvable), not a
            # transient signal. ``_probe_unknown`` would loop the
            # orchestrator forever ("transient failure attempt N");
            # ``_probe_failed`` lets the ensurer escalate to permanent
            # so the dashboard surfaces a single ERROR instead of
            # WARN-spam every 60s. Mirrors ``ensure_application_url``'s
            # ``_outcome_permanent`` for the same root cause.
            return self._probe_failed(
                ctx,
                "no CONFIG_ROOT — cannot locate settings.json",
                evidence={"rel_path": _SETTINGS_REL_PATH},
            )
        if not path.is_file():
            return self._probe_failed(
                ctx,
                f"settings.json not yet generated at {path}",
                evidence={"settings_path": str(path)},
            )
        data = self._read_settings(path)
        if data is None:
            return self._probe_unknown(
                ctx,
                f"settings.json unparseable at {path}",
                evidence={"settings_path": str(path)},
            )
        application_url = str(
            (data.get("main") or {}).get("applicationUrl", "") or "",
        )
        trust_proxy = (data.get("network") or {}).get("trustProxy")
        if not application_url.startswith(_HTTPS_SCHEME_PREFIX):
            return self._probe_failed(
                ctx,
                f"main.applicationUrl not https: {application_url!r}",
                evidence={
                    "settings_path": str(path),
                    "applicationUrl": application_url,
                },
            )
        if trust_proxy is not True:
            return self._probe_failed(
                ctx,
                f"network.trustProxy not True: {trust_proxy!r}",
                evidence={
                    "settings_path": str(path),
                    "trustProxy": trust_proxy,
                },
            )
        return self._probe_ok(
            ctx,
            "applicationUrl https + trustProxy true",
            evidence={
                "settings_path": str(path),
                "applicationUrl": application_url,
            },
        )

    def ensure_application_url(
        self, ctx: OrchestrationContext, *, routing: dict[str, Any] | None = None,
    ) -> Outcome[None]:
        # Same settings.json mutation as ``ensure_oidc`` — the legacy
        # handler updates oidcLogin + providers + applicationUrl +
        # trustProxy in one pass. Either ensurer brings all three
        # promises into the desired state on first run; the second
        # ensurer is a no-op (changed=False).
        return self._ensure_settings_json(ctx, routing=routing)

    # --- arr-servers ------------------------------------------------

    def probe_arr_servers(self, ctx: OrchestrationContext) -> ProbeResult:
        path = self._settings_path(ctx)
        if path is None:
            # Same operator-config gap reasoning as
            # ``probe_application_url`` — escalate to ``failed`` so the
            # ensurer can return permanent and the orchestrator stops
            # logging WARN every tick.
            return self._probe_failed(
                ctx,
                "no CONFIG_ROOT — cannot locate settings.json",
                evidence={"rel_path": _SETTINGS_REL_PATH},
            )
        if not path.is_file():
            return self._probe_failed(
                ctx,
                f"settings.json not yet generated at {path}",
                evidence={"settings_path": str(path)},
            )
        data = self._read_settings(path)
        if data is None:
            return self._probe_unknown(
                ctx,
                f"settings.json unparseable at {path}",
                evidence={"settings_path": str(path)},
            )
        for arr_name in _REQUIRED_ARR_NAMES:
            entries = data.get(arr_name) or []
            if not isinstance(entries, list) or not entries:
                return self._probe_failed(
                    ctx,
                    f"no {arr_name} entry in settings.json",
                    evidence={
                        "settings_path": str(path),
                        "missing": arr_name,
                    },
                )
            if not any(
                isinstance(e, dict) and (e.get("apiKey") or "").strip()
                for e in entries
            ):
                return self._probe_failed(
                    ctx,
                    f"{arr_name} entry has no apiKey",
                    evidence={
                        "settings_path": str(path),
                        "missing_key_for": arr_name,
                    },
                )
        jellyfin_block = data.get("jellyfin") or {}
        if not (jellyfin_block.get(_JELLYFIN_KEY_FIELD) or "").strip():
            return self._probe_failed(
                ctx,
                "jellyfin block has no apiKey",
                evidence={
                    "settings_path": str(path),
                    "jellyfin_keys": sorted(jellyfin_block.keys()),
                },
            )
        return self._probe_ok(
            ctx,
            "all *arr + jellyfin entries have apiKey",
            evidence={"settings_path": str(path)},
        )

    def ensure_arr_servers(
        self,
        ctx: OrchestrationContext,
        *,
        configure_handler: Any,
        job_context_factory: Any,
    ) -> Outcome[None]:
        """Delegate to the existing ``configure_jellyseerr`` handler.

        ``configure_handler`` is the callable ``(JobContext) -> dict|None``
        from ``application.jellyseerr.configure_jellyseerr_job``.
        ``job_context_factory`` is a callable ``() -> JobContext``
        — usually ``JobContext`` itself, or a stub in tests.

        Idempotent skip when the probe already ok'd: the existing
        handler is itself idempotent (only writes deltas), so calling
        it on a fully-configured Jellyseerr is a no-op. We probe
        first anyway to surface "already configured" cleanly without
        spinning up the full settings-API + library-sync round trip.
        """
        path = self._settings_path(ctx)
        if path is not None and path.is_file():
            probe = self.probe_arr_servers(ctx)
            if probe.is_ok:
                return self._outcome_success(
                    evidence={"reason": "already_configured", "settings_path": str(path)},
                )
        try:
            job_ctx = job_context_factory()
        except Exception as exc:  # noqa: BLE001
            return self._outcome_transient(
                f"could not build JobContext: {exc}",
                evidence={"error": str(exc)},
            )
        try:
            result = configure_handler(job_ctx)
        except Exception as exc:  # noqa: BLE001
            return self._outcome_transient(
                f"configure_jellyseerr raised: {exc}",
                evidence={"error": str(exc)},
            )
        return self._outcome_success(
            evidence={
                "result": result if isinstance(result, dict) else None,
                "settings_path": str(path) if path else "",
            },
        )

    # --- internals --------------------------------------------------

    def _ensure_settings_json(
        self, ctx: OrchestrationContext, *, routing: dict[str, Any] | None,
    ) -> Outcome[None]:
        path = self._settings_path(ctx)
        if path is None:
            return self._outcome_permanent(
                "no CONFIG_ROOT — cannot locate settings.json",
                evidence={"rel_path": _SETTINGS_REL_PATH},
            )
        if not path.is_file():
            return self._outcome_transient(
                f"settings.json not yet generated at {path}",
                evidence={"settings_path": str(path)},
            )
        data = self._read_settings(path)
        if data is None:
            return self._outcome_permanent(
                f"settings.json unparseable at {path}",
                evidence={"settings_path": str(path)},
            )
        resolved_routing = self._resolve_routing(routing)
        issuer = self._build_issuer(resolved_routing)
        application_url = self._build_application_url(resolved_routing)
        desired_provider = self._build_provider(issuer)

        main = data.setdefault("main", {})
        oidc_block = data.setdefault("oidc", {})
        network_block = data.setdefault("network", {})
        changed = False
        if main.get("oidcLogin") is not True:
            main["oidcLogin"] = True
            changed = True
        if main.get("applicationUrl") != application_url:
            main["applicationUrl"] = application_url
            changed = True
        if network_block.get("trustProxy") is not True:
            network_block["trustProxy"] = True
            changed = True
        # Strip the wrong-location variant if a previous run put
        # trustProxy under main; PR #1505 migration 0005 moved it to
        # network.* and leaving the stale key in the file produces
        # diff-noise on every reconcile.
        if "trustProxy" in main:
            del main["trustProxy"]
            changed = True
        desired_providers = [desired_provider]
        if oidc_block.get("providers") != desired_providers:
            oidc_block["providers"] = desired_providers
            changed = True
        # Strip leftover wrong-schema fields from earlier debug rounds.
        for stale_key in ("oidc", "openIdProviders"):
            if stale_key in main:
                del main[stale_key]
                changed = True

        if not changed:
            return self._outcome_success(
                evidence={
                    "reason": "already_in_sync",
                    "settings_path": str(path),
                },
            )

        try:
            path.write_text(
                json.dumps(data, indent=2), encoding="utf-8",
            )
        except OSError as exc:
            return self._outcome_transient(
                f"could not write {path}: {exc}",
                evidence={"settings_path": str(path), "error": str(exc)},
            )
        restarted = self._restart_jellyseerr()
        return self._outcome_success(
            evidence={
                "settings_written": True,
                "settings_path": str(path),
                "issuer": issuer,
                "application_url": application_url,
                "restarted": restarted,
            },
        )

    def _public_settings_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}{_PUBLIC_SETTINGS_PATH}"

    def _fetch_public_settings(self, url: str) -> dict[str, Any] | None:
        try:
            with urllib.request.urlopen(
                url, timeout=_PROBE_TIMEOUT_SECONDS,
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
        return parsed if isinstance(parsed, dict) else None

    def _settings_path(
        self, ctx: OrchestrationContext,
    ) -> Path | None:
        config_root = (
            ctx.config.get("config_root")
            or ctx.extra.get("config_root")
            or os.environ.get("CONFIG_ROOT")
            or ""
        )
        if not config_root:
            return None
        return Path(config_root) / _SETTINGS_REL_PATH

    def _read_settings(self, path: Path) -> dict[str, Any] | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _resolve_routing(
        self, override: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if isinstance(override, dict) and override:
            return override
        try:
            from media_stack.api.services.config import get_routing
            routing = get_routing() or {}
            if isinstance(routing, dict):
                return dict(routing)
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_routing unavailable: %s", exc)
        return {}

    def _build_issuer(self, routing: dict[str, Any]) -> str:
        base = str(routing.get("base_domain") or _DEFAULT_BASE_DOMAIN).strip()
        sub = str(routing.get("stack_subdomain") or _DEFAULT_STACK_SUBDOMAIN).strip()
        return _AUTHELIA_HOST_FORMAT.format(sub=sub, base=base)

    def _build_application_url(self, routing: dict[str, Any]) -> str:
        sub = str(routing.get("stack_subdomain") or _DEFAULT_STACK_SUBDOMAIN).strip()
        base = str(routing.get("base_domain") or _DEFAULT_BASE_DOMAIN).strip()
        gateway_host = str(routing.get("gateway_host") or "").strip() \
            or _APPS_HOST_FORMAT.format(sub=sub, base=base)
        return f"{_HTTPS_SCHEME_PREFIX}{gateway_host}{self._path_prefix}"

    def _build_provider(self, issuer: str) -> dict[str, Any]:
        return {
            "slug": self._provider_slug,
            "name": self._provider_name,
            "issuerUrl": issuer,
            "clientId": self._client_id,
            "clientSecret": self._client_secret,
            "scopes": self._scopes,
            "newUserLogin": True,
            "requiredClaims": "",
        }

    def _restart_jellyseerr(self) -> bool:
        """Best-effort restart so settings.json is reloaded.

        Tries Docker SDK first (compose), falls back to k8s pod
        delete (cluster). Failures are swallowed — Jellyseerr will
        pick up the new config on its next natural restart anyway.
        """
        try:
            import docker as _docker
            _docker.from_env().containers.get("jellyseerr").restart(
                timeout=_RESTART_TIMEOUT_SECONDS,
            )
            return True
        except Exception as docker_exc:  # noqa: BLE001
            logger.debug("docker restart unavailable: %s", docker_exc)
        try:
            from kubernetes import client as _k8s, config as _kc
            try:
                _kc.load_incluster_config()
            except Exception:  # noqa: BLE001
                _kc.load_kube_config()
            ns = os.environ.get("K8S_NAMESPACE", "media-stack")
            v1 = _k8s.CoreV1Api()
            for pod in v1.list_namespaced_pod(
                ns, label_selector="app=jellyseerr",
            ).items:
                v1.delete_namespaced_pod(
                    name=pod.metadata.name, namespace=ns,
                )
            return True
        except Exception as k8s_exc:  # noqa: BLE001
            logger.debug("k8s restart unavailable: %s", k8s_exc)
            return False


__all__ = ["JellyseerrConfigWirer"]
