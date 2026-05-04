"""Disk- and Keys-domain GET routes (ADR-0007 Phase 2).

Three small read-only routes lifted off the legacy
``handlers_get.GetRequestHandler.handle()`` ``elif`` chain. The two
domains are bundled into a single route module because each is too
small to warrant its own file (``/api/keys`` is one route,
``/api/disk`` and ``/api/cleanup-preview`` share the
``DiskService``) and they read from completely independent
collaborators (``HealthService.discover_api_keys`` for keys,
``DiskService`` for disk). Co-locating them keeps the
``api/routes/`` directory from accumulating one-route modules
without losing the per-domain seams — each route's handler talks
to a single service.

Spec parity:

* ``/api/keys``           -> ``getKeys``           (Security tag)
* ``/api/disk``           -> ``getDisk``           (Disk tag)
* ``/api/cleanup-preview``-> ``getCleanupPreview`` (Disk tag)

Bodies for ``/api/disk`` and ``/api/cleanup-preview`` are lifted
verbatim from the legacy chain — both are one-line delegations to
``disk_svc.get_disk()`` / ``disk_svc.preview_cleanup()``. The
``/api/keys`` body is also lifted, NOT delegated to the legacy
``GetRequestHandler._handle_keys`` static method, because the
legacy helper lives in a closed class and pulling in
``handlers_get`` solely to reach the staticmethod re-introduces
the cyclic-import risk Phase 2 is trying to avoid. The lifted body
imports the redaction helper + ``HealthService`` directly.
"""

from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import disk as disk_svc
from media_stack.api.services import health as health_svc
from media_stack.domain.auth.secret_redaction import redact_api_key_map


class DiskKeysGetRoutes(RouteModule):
    """All ``/api/keys``, ``/api/disk``, and ``/api/cleanup-preview``
    GET routes. Auto-discovered + instantiated by the Router at
    startup; the Router walks tagged methods for registration."""

    @get("/api/keys")
    def handle_keys(self, handler: Any) -> None:
        """Return the REDACTED per-service API-key inventory plus
        the admin username + password-set flag.

        **Security**: this endpoint returns key METADATA only — the
        raw secrets never cross the wire. Each entry is shaped
        ``{"has_key": bool, "fingerprint": "abcd...wxyz",
        "source": "discovered"}`` — enough for the admin UI to say
        "Sonarr's key starts ``abcd`` and ends ``wxyz``" without
        ever handing the key to the browser.

        Background (security audit 2026-04-24): a previous version
        of this endpoint returned every discovered provider's RAW
        key to any authenticated caller. A single compromised
        read-scope token == full stack compromise. Revealing a raw
        key now requires an explicit reveal endpoint that is
        separately audited, rate-limited, and admin-only.

        See ``media_stack.domain.auth.secret_redaction
        .redact_api_key_map`` for the central redaction helper.
        """
        raw_keys = health_svc.discover_api_keys()
        keys = redact_api_key_map(raw_keys, source="discovered")
        admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "")
        handler._json_response(HTTPStatus.OK, {
            "keys": keys,
            "admin": {
                "username": admin_user,
                "password_set": bool(admin_pass),
            },
            "count": len(keys),
        })

    @get("/api/disk")
    def handle_disk(self, handler: Any) -> None:
        """Return per-volume disk usage + the active guardrail
        config. The dashboard's storage card + the
        ``/storage`` page both consume this. Body shape is the
        ``DiskInfo`` schema in ``contracts/api/openapi.yaml``.
        """
        handler._json_response(HTTPStatus.OK, disk_svc.get_disk())

    @get("/api/cleanup-preview")
    def handle_cleanup_preview(self, handler: Any) -> None:
        """Dry-run preview of which completed torrents the disk
        guardrail cleanup policy would delete. Used by the
        Storage page's "preview cleanup" affordance to show the
        operator exactly what an actual cleanup would touch
        BEFORE they run it.
        """
        handler._json_response(
            HTTPStatus.OK, disk_svc.preview_cleanup(),
        )


__all__ = ["DiskKeysGetRoutes"]
