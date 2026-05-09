"""Shared helpers for ``ServiceLifecycle`` adapters that discover
API keys from env + on-disk config.

Every adapter in the bazarr/jellyseerr/sabnzbd/*arr family converged
on the same three helpers (``_api_key_env``, ``_config_path``,
``_classify_source``) — six near-identical loose-function definitions
across six lifecycle modules. ``LifecycleApiKeyHelpers`` collapses
them into one parameterised class. Each adapter holds a single
``ClassVar`` instance, configured with its service-specific defaults
(env-var name + default api-key file format), and the call sites
become ``self._API_KEY_HELPERS.api_key_env(ctx)``.

Lives in the domain layer (no I/O, no platform deps) because it
reads only from the ``OrchestrationContext`` value object — itself a
pure dataclass — plus ``os.environ``. The latter is unavoidable
since an "env or secret or file" key resolution is the contract;
domain owns the contract, adapters own the I/O around it.

Why a class instead of staticmethods on each lifecycle class?
The defaults differ per service (``BAZARR_API_KEY`` vs
``SONARR_API_KEY``), so the helpers cannot be pure staticmethods
without per-call lookup of a service-specific constant. Wrapping
the defaults in a per-service instance keeps the call sites short
and lets a future Sonarr-only override land cleanly via subclass.
"""

from __future__ import annotations

import os
from pathlib import Path

from media_stack.domain.services.lifecycle import OrchestrationContext


class LifecycleApiKeyHelpers:
    """Per-adapter configured helper for env/file API-key resolution.

    Construct one ``ClassVar`` instance per lifecycle class with the
    service-specific defaults; call its methods from the lifecycle's
    probe/discover/persist sites.
    """

    def __init__(
        self,
        *,
        default_api_key_env: str,
    ) -> None:
        self._default_api_key_env = default_api_key_env

    def api_key_env(self, ctx: OrchestrationContext) -> str:
        """Resolve the env-var name the adapter uses for its api key.

        Contract YAML's ``api_key_env`` overrides the per-service
        default (so deployments can rename without touching code).
        """
        return (
            ctx.config.get("api_key_env") or self._default_api_key_env
        ).strip()

    def config_path(
        self, ctx: OrchestrationContext,
    ) -> Path | None:
        """Resolve the absolute path to the on-disk api-key file.

        Returns ``None`` when the contract YAML has no
        ``api_key_config`` set (services without a file-based key
        source). When ``CONFIG_ROOT`` is missing, falls back to
        treating ``api_key_config`` as already-absolute.
        """
        rel = ctx.config.get("api_key_config")
        if not rel:
            return None
        config_root = (
            ctx.config.get("config_root")
            or ctx.extra.get("config_root")
            or os.environ.get("CONFIG_ROOT")
            or ""
        )
        return Path(config_root) / rel if config_root else Path(rel)

    def classify_source(
        self, ctx: OrchestrationContext, key: str,
    ) -> str:
        """Classify where ``key`` was discovered (for evidence trails).

        Returns one of ``"secrets"`` / ``"env"`` / ``"config_file"``
        — the orchestrator surfaces this in probe evidence so
        operators can tell at a glance which source the credential
        came from when debugging a misconfiguration.
        """
        env_var = self.api_key_env(ctx)
        if (ctx.secrets.get(env_var) or "").strip() == key:
            return "secrets"
        if os.environ.get(env_var, "").strip() == key:
            return "env"
        return "config_file"


__all__ = ["LifecycleApiKeyHelpers"]
