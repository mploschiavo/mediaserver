"""Tests pinning that the homepage renderer skips services that
aren't enabled by the active deploy.

The bug pattern: ``DEFAULT_HOSTS`` in ``adapters.py`` is a
hardcoded list of every service the registry knows about,
including profile-gated ones (Authelia, Authentik, Plex). A
default deploy that doesn't enable those compose profiles would
get a homepage with broken tiles for services that never
existed in their stack.

The fix: ``service.ensure_services_config`` filters the
hostname list through ``registry.is_service_enabled`` before
passing it to the renderer. A service is enabled when either
its ``profiles`` list is empty (always-on) OR at least one of
its declared profiles is in the active ``COMPOSE_PROFILES``
env var.

These tests pin three properties:

1. The new ``is_service_enabled`` helper short-circuits to True
   for services with no profile gate.
2. Profile-gated services are skipped when the active set
   doesn't list any of their profiles, included when it does.
3. The end-to-end render in ``HomepageRenderService`` drops
   gated services in the rendered ``services.yaml``."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.registry import (  # noqa: E402
    ServiceDef, is_service_enabled,
)


def _svc(sid: str, *, profiles: list[str] | None = None) -> ServiceDef:
    return ServiceDef(
        id=sid, name=sid.title(),
        host=sid, port=8080,
        profiles=profiles or [],
    )


class IsServiceEnabledTests(unittest.TestCase):

    def test_always_on_service_is_enabled(self) -> None:
        self.assertTrue(is_service_enabled(
            _svc("jellyfin"), env={},
        ))

    def test_gated_service_skipped_when_profile_inactive(self) -> None:
        """Authelia is gated behind ``profiles: ['auth-authelia']``.
        On a default deploy where that profile isn't activated,
        the homepage renderer should not include an authelia tile."""
        self.assertFalse(is_service_enabled(
            _svc("authelia", profiles=["auth-authelia"]),
            env={},
        ))

    def test_gated_service_enabled_when_profile_active(self) -> None:
        self.assertTrue(is_service_enabled(
            _svc("authelia", profiles=["auth-authelia"]),
            env={"COMPOSE_PROFILES": "auth-authelia"},
        ))

    def test_compose_profiles_csv_parsed(self) -> None:
        """The compose env var is comma-separated. Pin the
        parser so a future refactor doesn't accidentally treat
        the raw string as a single profile name."""
        self.assertTrue(is_service_enabled(
            _svc("plex", profiles=["plex"]),
            env={"COMPOSE_PROFILES": "auth-authentik, plex, nvidia"},
        ))
        self.assertFalse(is_service_enabled(
            _svc("authelia", profiles=["auth-authelia"]),
            env={"COMPOSE_PROFILES": "auth-authentik, plex, nvidia"},
        ))


class HomepageEndToEndFilterTests(unittest.TestCase):
    """End-to-end: render a real services.yaml with the default
    hosts and assert profile-gated services don't appear."""

    def _build_service(self, log_capture=None):
        from media_stack.services.apps.homepage.service import (
            HomepageService,
        )
        from media_stack.services.apps.homepage.adapters import (
            DEFAULT_HOSTS, render_services_yaml,
        )

        def _coerce_list(v):
            if v is None:
                return []
            if isinstance(v, (list, tuple)):
                return list(v)
            return [v]

        def _bool_cfg(d, key, default=False):
            v = (d or {}).get(key, default)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.lower() in ("1", "true", "yes", "on")
            return bool(v)

        return HomepageService(
            log=(log_capture.append if log_capture is not None
                 else lambda _m: None),
            bool_cfg=_bool_cfg,
            coerce_list=_coerce_list,
            resolve_path=lambda root, rel: Path(root) / rel,
            render_services_yaml=render_services_yaml,
            default_hosts=list(DEFAULT_HOSTS),
        )

    def _render(self, *, profiles: str | None = None) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            patcher = (
                mock.patch.dict(os.environ,
                                {"COMPOSE_PROFILES": profiles},
                                clear=False)
                if profiles is not None
                else mock.patch.dict(os.environ, {}, clear=False)
            )
            with patcher:
                if profiles is None:
                    os.environ.pop("COMPOSE_PROFILES", None)
                svc = self._build_service()
                svc.ensure_services_config(
                    cfg={
                        "homepage": {"enabled": True, "scheme": "http"},
                        "routing": {
                            "gateway_host": "apps.media-stack.local",
                        },
                    },
                    config_root=tmp,
                )
                return (
                    Path(tmp) / "homepage" / "services.yaml"
                ).read_text(encoding="utf-8")

    def test_authelia_authentik_excluded_on_default_deploy(self) -> None:
        """Default compose run has no profiles active. Renderer
        emits per-service hrefs like ``/app/authelia``; assert
        the gated services don't get one."""
        rendered = self._render(profiles=None)
        self.assertNotIn(
            "/app/authelia", rendered,
            "Authelia tile rendered on a deploy that didn't "
            "enable the auth-authelia profile.",
        )
        self.assertNotIn(
            "/app/authentik", rendered,
            "Authentik tile rendered on a deploy that didn't "
            "enable the auth-authentik profile.",
        )

    def test_authelia_included_when_profile_active(self) -> None:
        rendered = self._render(profiles="auth-authelia")
        self.assertIn(
            "/app/authelia", rendered,
            "Authelia profile is active but its tile was filtered out.",
        )

    def test_always_on_services_always_render(self) -> None:
        """Jellyfin / Sonarr / Radarr have no profile gate.
        They must always render regardless of COMPOSE_PROFILES."""
        rendered = self._render(profiles=None)
        for href in ("/app/jellyfin", "/app/sonarr",
                     "/app/radarr", "/app/prowlarr"):
            self.assertIn(
                href, rendered,
                f"Always-on service {href} dropped from "
                "services.yaml — the filter is too aggressive.",
            )

    def test_unregistered_hosts_are_dropped(self) -> None:
        """The bug shape: ``recyclarr.local`` was in DEFAULT_HOSTS
        but had no ``contracts/services/recyclarr.yaml``. The first
        cut of the filter passed unknown services through, so the
        homepage rendered a tile for a stub container the user
        never opted into. The fix: drop any host whose service id
        isn't in the registry."""
        from media_stack.services.apps.homepage.service import (
            HomepageService,
        )
        from media_stack.services.apps.homepage.adapters import (
            render_services_yaml,
        )
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("COMPOSE_PROFILES", None)
            svc = HomepageService(
                log=lambda _m: None,
                bool_cfg=lambda d, k, v: bool((d or {}).get(k, v)),
                coerce_list=lambda v: list(v) if isinstance(v, list) else ([] if v is None else [v]),
                resolve_path=lambda root, rel: Path(root) / rel,
                render_services_yaml=render_services_yaml,
                # Inject an unknown host explicitly — we don't want
                # this assertion to depend on which services happen
                # to be missing from the registry today.
                default_hosts=["jellyfin.local", "made-up-service.local"],
            )
            svc.ensure_services_config(
                cfg={
                    "homepage": {"enabled": True, "scheme": "http"},
                    "routing": {"gateway_host": "apps.media-stack.local"},
                },
                config_root=tmp,
            )
            rendered = (
                Path(tmp) / "homepage" / "services.yaml"
            ).read_text(encoding="utf-8")
        self.assertIn(
            "/app/jellyfin", rendered,
            "Registered service dropped — filter too aggressive.",
        )
        self.assertNotIn(
            "/app/made-up-service", rendered,
            "Unregistered service rendered — filter passes "
            "unknown ids through. The 2026-04-21 recyclarr bug.",
        )


class DefaultHostsRegistryAlignmentRatchet(unittest.TestCase):
    """Ratchet: every host in ``DEFAULT_HOSTS`` must correspond to
    a registered ``ServiceDef``. If someone adds a host without a
    contract entry, the filter drops it silently — easier to miss
    than the inverse, so pin it here."""

    def test_every_default_host_is_in_registry(self) -> None:
        from media_stack.services.apps.homepage.adapters import (
            DEFAULT_HOSTS,
        )
        from media_stack.api.services.registry import SERVICE_MAP
        # The host ``<svc-id>.local`` maps to service id ``svc-id``.
        unknown: list[str] = []
        for host in DEFAULT_HOSTS:
            svc_id = host.split(".", 1)[0]
            if svc_id not in SERVICE_MAP:
                unknown.append(f"{host} (svc id {svc_id!r})")
        self.assertFalse(
            unknown,
            "DEFAULT_HOSTS lists hosts with no matching contract "
            "in contracts/services/:\n  - "
            + "\n  - ".join(unknown)
            + "\n\nFix: either remove the host from DEFAULT_HOSTS "
              "in adapters.py, or add a contracts/services/<id>.yaml "
              "for it. Without a contract entry the filter drops the "
              "host silently.",
        )


if __name__ == "__main__":
    unittest.main()
