"""Config-domain GET routes (ADR-0007 Phase 2 wave 4).

Six routes migrated off the ``handlers_get.handle()`` elif chain,
all sharing the ``Config`` OpenAPI tag:

* ``GET /api/env`` — runtime environment metadata (k8s namespace,
  profile name, node IPs, platform, runtime kind). NOT a raw env-var
  dump; the platform fingerprint that drives the dashboard's
  "you're on k8s/compose" header chip.
* ``GET /api/envvars`` — sanitised view of the controller's
  recognised env vars. **Sensitive** — secret-suffixed values
  (``*_PASSWORD`` / ``*_SECRET`` / ``*_TOKEN`` / ``*_KEY`` /
  ``*_API_KEY``) plus a small explicit set of credential names
  are masked to ``"***"`` by the service layer's ``_mask`` helper
  before they leave the process. The route handler intentionally
  does NOT touch the dict — calling
  ``config_svc.get_envvars()`` preserves the redaction contract,
  whereas a route-side reshape would risk leaking a value the
  service deliberately masked.
* ``GET /api/manifests`` — deployment manifest descriptor (k8s
  Deployments list / compose YAML / bootstrap-config summary,
  whichever applies for the current runtime).
* ``GET /api/config-drift`` — drift between the on-disk profile
  YAML and the live runtime config (routing fields, *arr API keys,
  container image vs pulled image). Cached for 60s via the shared
  ``api_cache`` because the live probe spawns Docker / k8s API
  calls per service.
* ``GET /api/config/libraries`` — the canonical library
  configuration the bootstrap pipeline owns. Read-only; the POST
  twin lives on the legacy chain until that domain migrates.
* ``GET /api/metadata-settings`` — metadata-preset config
  (language, agents, image preferences). Powers the Settings →
  Metadata card on the dashboard.

Implementation choice, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* Five of the six routes are one-line delegations to
  ``config_svc.<func>()`` — the legacy bodies are themselves
  one-liners over the same module-level helper, so a delegation
  preserves the contract without adding indirection.
* ``/api/config-drift`` lifts the legacy body's two-arg
  ``api_cache.get_or_compute()`` call verbatim. The 60s TTL is
  the contract — the dashboard polls every 30s and the cache
  smooths the per-poll Docker/k8s API cost.

The legacy bodies don't perform any direct ``os.environ.get(...)``
reads — every env-var read is encapsulated in
``DiagnosticsService`` / ``LibraryConfigService`` /
``MetadataConfigService``, so this migration neither adds new
``os.environ`` calls nor needs new entries in
``media_stack.core.defaults``. The redaction contract on
``/api/envvars`` is enforced inside ``DiagnosticsService.get_envvars``
(``_mask`` helper applied to every (key, value) before return); the
route-level delegation preserves that contract by passing the
already-masked dict straight back to the caller.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from media_stack.api.cache import api_cache
from media_stack.api.routing import RouteModule, get
from media_stack.api.services import config as config_svc


class ConfigGetRoutes(RouteModule):
    """Config-tag GET routes covering runtime env, envvars, deploy
    manifests, drift, libraries, and metadata settings. The Router
    auto-discovers + instantiates this class + walks its tagged
    methods at startup."""

    @get("/api/env")
    def handle_env(self, handler: Any) -> None:
        """Return the controller's runtime-environment fingerprint
        (k8s namespace, profile name, node IPs, platform string,
        Python version, ``runtime`` kind). NOT an env-var dump —
        operators who want raw env vars use ``/api/envvars``
        (which redacts secrets).
        """
        handler._json_response(HTTPStatus.OK, config_svc.get_env())

    @get("/api/envvars")
    def handle_envvars(self, handler: Any) -> None:
        """Return the sanitised view of the controller's recognised
        env vars (BOOTSTRAP_/STACK_/K8S_/CONTROLLER_/PUID/PGID/TZ
        prefixes plus per-service ``*_API_KEY`` shapes from the
        registry).

        Secret-suffixed values (``*_PASSWORD`` / ``*_SECRET`` /
        ``*_TOKEN`` / ``*_KEY``) and the explicit credential set
        (``STACK_ADMIN_PASSWORD``, ``AUTHELIA_JWT_SECRET``,
        ``AUTHELIA_SESSION_SECRET``,
        ``AUTHELIA_STORAGE_ENCRYPTION_KEY``) are masked to ``"***"``
        by ``DiagnosticsService.get_envvars`` BEFORE they leave the
        service layer. The route handler delegates straight to the
        already-masked dict so we cannot accidentally leak a value
        the service redacted.
        """
        handler._json_response(HTTPStatus.OK, config_svc.get_envvars())

    @get("/api/manifests")
    def handle_manifests(self, handler: Any) -> None:
        """Return the deployment-manifest descriptor for the current
        runtime: k8s Deployments list when ``K8S_NAMESPACE`` is
        set, the compose YAML body when running under compose, or
        the bootstrap-config summary as a fallback. Falls back to
        a running-containers snapshot if no manifest source is
        available — the response shape always carries a ``type``
        field so the UI can pick the right renderer.
        """
        handler._json_response(HTTPStatus.OK, config_svc.get_manifests())

    @get("/api/config-drift")
    def handle_config_drift(self, handler: Any) -> None:
        """Return the drift report comparing the on-disk profile
        YAML against the live runtime config (routing fields, *arr
        API keys, container image vs pulled image).

        Cached for 60s via the shared ``api_cache`` because the
        live probe spawns Docker / k8s API calls per service —
        without the cache the dashboard's 30s poll cadence would
        double-tax those APIs. Cache key + TTL lifted verbatim
        from the legacy chain so the contract stays identical.
        """
        handler._json_response(
            HTTPStatus.OK,
            api_cache.get_or_compute(
                "config_drift", config_svc.get_config_drift, ttl=60,
            ),
        )

    @get("/api/config/libraries")
    def handle_config_libraries(self, handler: Any) -> None:
        """Return the canonical library configuration — the
        bootstrap pipeline's source-of-truth for what libraries
        Jellyfin / Sonarr / Radarr should expose. Read-only; the
        POST twin (which writes the profile YAML) lives on the
        legacy chain until that domain migrates.
        """
        handler._json_response(
            HTTPStatus.OK, config_svc.get_libraries(),
        )

    @get("/api/metadata-settings")
    def handle_metadata_settings(self, handler: Any) -> None:
        """Return the metadata-preset configuration (language,
        agent selection, image preferences). Drives the Settings
        → Metadata card on the dashboard; the POST twin writes
        through ``MetadataConfigService.update_metadata_settings``.
        """
        handler._json_response(
            HTTPStatus.OK, config_svc.get_metadata_settings(),
        )


__all__ = ["ConfigGetRoutes"]
