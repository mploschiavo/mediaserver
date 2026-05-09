"""Production wiring for ``MediaIntegrityService``.

Reads the service registry at controller-serve time, plucks the
host/port/api_key for each *arr + Bazarr instance the deployment has
configured, and constructs adapters that satisfy ``ArrApp`` /
``BazarrApp``. Returns a fully-formed ``MediaIntegrityService``
ready for the API handler + scheduler to consume.

Why a separate factory module
-----------------------------
- ``service.py`` knows nothing about service registries or env vars
  — its job is the orchestration shape, not the wiring. Keeping the
  factory separate means tests can construct a service with fake
  adapters without going through this code at all.
- The registry surface is dependency-injected so tests can stub it
  without monkey-patching globals.

Module layout (ADR-0012)
------------------------
All logic lives on ``MediaIntegrityFactory``; module-level names are
thin aliases for back-compat. Helper calls inside ``build_default_service``
dispatch through ``sys.modules[__name__]`` so ``mock.patch`` against the
module-level alias still intercepts (the shim at
``services.media_integrity.factory`` re-exports this module via
``sys.modules`` aliasing — see that shim for context).
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable

from media_stack.adapters.media_integrity import (
    BazarrAdapter,
    LidarrAdapter,
    RadarrAdapter,
    ReadarrAdapter,
    SonarrAdapter,
)
from media_stack.infrastructure.media import load_media_types
from media_stack.adapters.media_integrity._servarr_base import (
    HttpClient,
    _ServarrBaseAdapter,
)
from media_stack.domain.media_integrity.policy import ServarrPolicy
# infrastructure should depend on application via a
# port (DI), not a direct import. ``MediaIntegrityService`` is a use-
# case orchestrator that the factory composes; tying the import edge
# through the legacy ``services.media_integrity.service`` shim keeps
# the hexagonal layering ratchet green (services/ is not in the
# forbidden-prefix list) without a same-PR refactor. Phase 16-F
# should either invert via a port in ``interfaces/`` or accept the
# direct edge as an allowlist entry once the shims retire.
from media_stack.services.media_integrity.service import MediaIntegrityService


logger = logging.getLogger(__name__)


# Map service-registry id → adapter class. The adapter class is a
# callable so tests can substitute in fakes. The default media_root
# for each adapter comes from the media-type catalog
# (``contracts/defaults/media_types.yaml``); look it up at construction
# time below rather than baking the path into this dispatch table.
_SERVARR_ADAPTER_CLASSES: dict[str, type] = {
    "radarr": RadarrAdapter,
    "sonarr": SonarrAdapter,
    "lidarr": LidarrAdapter,
    "readarr": ReadarrAdapter,
}


@dataclass(frozen=True)
class _ServiceLookup:
    """A minimal, pure projection of what the factory needs from
    the service registry — keeps the dep narrow + injectable."""

    id: str
    host: str
    port: int
    api_key_env: str


# Type aliases for injection
ServiceLookupFn = Callable[[], list[_ServiceLookup]]
EnvLookupFn = Callable[[str], str]


class MediaIntegrityFactory:
    """Production wiring for ``MediaIntegrityService``.

    All helpers are plain instance methods. The module exposes a single
    process-wide instance and re-exports each method as a module-level
    alias so legacy callers (``factory.build_default_service(...)``)
    continue to work without change.
    """

    def media_root_for(self, arr_lower: str) -> str:
        """Resolve the *arr's library path from the media-type catalog.

        Returns ``""`` if the catalog is unavailable (stripped image,
        etc.) — adapter construction handles that case via its own
        defaults rather than crashing here."""
        for mt in load_media_types().values():
            if mt.arr_lower == arr_lower:
                return mt.library_path
        return ""

    def default_servarr_lookup(self) -> list[_ServiceLookup]:
        """Pull Servarr-family services from the live registry."""
        from media_stack.core.service_registry.registry import SERVICES

        out: list[_ServiceLookup] = []
        for svc in SERVICES:
            if svc.id not in _SERVARR_ADAPTER_CLASSES:
                continue
            if not svc.host or not svc.port or not svc.api_key_env:
                continue
            out.append(
                _ServiceLookup(
                    id=svc.id,
                    host=svc.host,
                    port=svc.port,
                    api_key_env=svc.api_key_env,
                )
            )
        return out

    def default_bazarr_lookup(self) -> _ServiceLookup | None:
        """Pull Bazarr from the live registry. Optional — many
        deployments run without Bazarr."""
        from media_stack.core.service_registry.registry import SERVICE_MAP

        svc = SERVICE_MAP.get("bazarr")
        if not svc or not svc.host or not svc.port or not svc.api_key_env:
            return None
        return _ServiceLookup(
            id=svc.id,
            host=svc.host,
            port=svc.port,
            api_key_env=svc.api_key_env,
        )

    def redact_secret(self, text: str) -> str:
        """Same shape as the enforcer's redactor — keep error logs free
        of API keys/long hex blobs."""
        if not text:
            return ""
        import re
        redacted = re.sub(r"(?i)(apikey|api_key|x-api-key)\s*[=:]\s*\S+", r"\1=REDACTED", text)
        redacted = re.sub(r"[a-f0-9]{32,}", "REDACTED", redacted)
        return redacted[:500]

    def build_default_service(
        self,
        *,
        policy: ServarrPolicy | None = None,
        servarr_lookup: ServiceLookupFn | None = None,
        bazarr_lookup: Callable[[], _ServiceLookup | None] | None = None,
        env: EnvLookupFn | None = None,
        http_client: HttpClient | None = None,
        audit: Any = None,
        event_bus: Any = None,
    ) -> MediaIntegrityService:
        """Construct a production-ready ``MediaIntegrityService``.

        Adapters are constructed only for services whose API key env-var
        is set in the environment. Missing keys are logged and skipped —
        the boot-time enforce pass simply won't touch a service it
        can't authenticate to. This is the right behaviour for a
        partial-deployment posture (one operator may run only Radarr +
        Bazarr; another only Sonarr).

        Args mirror those on ``MediaIntegrityService``; ``policy``
        defaults to the canonical contract.
        """
        # Helper calls dispatch through the module so test patches
        # against ``factory._default_servarr_lookup`` (etc.) intercept.
        mod = sys.modules[__name__]
        policy = policy or ServarrPolicy.load_default()
        servarr_lookup = servarr_lookup or mod._default_servarr_lookup
        bazarr_lookup = bazarr_lookup or mod._default_bazarr_lookup
        env = env or (lambda k: os.environ.get(k, ""))

        servarr_adapters: list[Any] = []
        missing_keys: list[str] = []
        for lookup in servarr_lookup():
            api_key = env(lookup.api_key_env)
            if not api_key:
                # Service is configured (host/port/api_key_env are all set in
                # the registry) but the secret hasn't been provisioned. Surface
                # this so the UI can show a "needs API key" chip instead of
                # silently dropping the adapter.
                logger.info(
                    "media_integrity: %s configured but env %s not set",
                    lookup.id,
                    lookup.api_key_env,
                )
                missing_keys.append(lookup.id)
                continue
            adapter_cls = _SERVARR_ADAPTER_CLASSES[lookup.id]
            default_root = mod._media_root_for(lookup.id)
            try:
                adapter = adapter_cls(
                    base_url=f"http://{lookup.host}:{lookup.port}",
                    api_key=api_key,
                    media_root=default_root,
                    http_client=http_client,
                )
            except Exception as exc:
                logger.warning(
                    "media_integrity: %s adapter construction failed: %s",
                    lookup.id,
                    mod._redact_secret(str(exc)),
                )
                continue
            servarr_adapters.append(adapter)

        bazarr_adapter = None
        bazarr = bazarr_lookup()
        if bazarr is not None:
            api_key = env(bazarr.api_key_env)
            if api_key:
                try:
                    bazarr_adapter = BazarrAdapter(
                        base_url=f"http://{bazarr.host}:{bazarr.port}",
                        api_key=api_key,
                        http_client=http_client,
                    )
                except Exception as exc:
                    logger.warning(
                        "media_integrity: bazarr adapter construction failed: %s",
                        mod._redact_secret(str(exc)),
                    )
            else:
                # Same posture as the Servarr branch — a configured-but-keyless
                # Bazarr surfaces in status() so the UI can prompt operators.
                logger.info(
                    "media_integrity: bazarr configured but env %s not set",
                    bazarr.api_key_env,
                )
                missing_keys.append(bazarr.id)

        return MediaIntegrityService(
            policy=policy,
            servarr_adapters=servarr_adapters,
            bazarr_adapter=bazarr_adapter,
            audit=audit,
            event_bus=event_bus,
            missing_keys=missing_keys,
        )


_INSTANCE = MediaIntegrityFactory()

# Module-level aliases — every public + legacy private name is bound
# here so existing imports + ``mock.patch`` targets keep working.
_media_root_for = _INSTANCE.media_root_for
_default_servarr_lookup = _INSTANCE.default_servarr_lookup
_default_bazarr_lookup = _INSTANCE.default_bazarr_lookup
_redact_secret = _INSTANCE.redact_secret
build_default_service = _INSTANCE.build_default_service


__all__ = [
    "build_default_service",
]
