"""Tests for the ``GET /api/services`` filter behaviour.

Phase A polish — the AppsPage was rendering one card per registry
entry, including services the deploy hadn't enabled (``COMPOSE_PROFILES``
gate didn't fire) and "service" entries that exist purely to anchor
jobs in the bootstrap DAG (``core``, ``media_integrity`` — both have
``web_ui: false``).

Contract pinned here:
  * Default response excludes ``web_ui: false`` entries.
  * Default response excludes profile-gated services that aren't in
    the active ``COMPOSE_PROFILES``.
  * ``include_all=True`` overrides the filter and returns every
    registry entry — for tooling and the registry inspector.
  * Each entry carries ``web_ui``, ``profiles``, ``enabled`` and
    ``icon_url`` so the dashboard can debug "why is X missing?".

Tests target the pure ``build_apps_listing`` helper rather than the
HTTP handler so the import chain (which transitively pulls argon2
and other optional native deps via the auth modules) doesn't get in
the way.
"""

from __future__ import annotations

from typing import Any

import pytest

from media_stack.api.services.registry import (
    ServiceDef,
    build_apps_listing,
)


def _make_services() -> list[ServiceDef]:
    return [
        ServiceDef(id="sonarr", name="Sonarr", category="automation"),
        ServiceDef(id="radarr", name="Radarr", category="automation"),
        # web_ui:false — must be filtered on the default path.
        ServiceDef(
            id="core", name="Core Operations",
            category="infrastructure", web_ui=False,
        ),
        ServiceDef(
            id="media_integrity", name="Media Integrity",
            category="infrastructure", web_ui=False,
        ),
        # Profile-gated; not active by default.
        ServiceDef(
            id="plex", name="Plex",
            category="media", profiles=["plex"],
        ),
        # Profile-gated; enabled by default profile (active).
        ServiceDef(
            id="jellyfin", name="Jellyfin",
            category="media", profiles=["default"],
        ),
        # Icon override.
        ServiceDef(
            id="custom-svc", name="Custom",
            category="management",
            icon_url="https://example.test/custom.png",
        ),
    ]


def _ids(body: list[dict[str, Any]]) -> set[str]:
    return {entry["id"] for entry in body}


_ENV = {"COMPOSE_PROFILES": "default"}


class TestDefaultFiltering:
    def test_drops_web_ui_false_services(self) -> None:
        body = build_apps_listing(_make_services(), env=_ENV)
        ids = _ids(body)
        assert "core" not in ids
        assert "media_integrity" not in ids

    def test_drops_inactive_profile_gated_services(self) -> None:
        body = build_apps_listing(_make_services(), env=_ENV)
        ids = _ids(body)
        # plex requires "plex" profile; only "default" is active.
        assert "plex" not in ids

    def test_keeps_active_profile_gated_services(self) -> None:
        body = build_apps_listing(_make_services(), env=_ENV)
        ids = _ids(body)
        assert "jellyfin" in ids

    def test_keeps_unprofiled_services(self) -> None:
        body = build_apps_listing(_make_services(), env=_ENV)
        ids = _ids(body)
        assert "sonarr" in ids
        assert "radarr" in ids

    def test_always_appends_controller_entry(self) -> None:
        body = build_apps_listing(_make_services(), env=_ENV)
        ids = _ids(body)
        assert "controller" in ids


class TestIncludeAllOverride:
    def test_returns_every_registry_entry_with_include_all(self) -> None:
        body = build_apps_listing(
            _make_services(), include_all=True, env=_ENV,
        )
        ids = _ids(body)
        for expected in (
            "sonarr", "radarr", "core", "media_integrity",
            "plex", "jellyfin", "custom-svc",
        ):
            assert expected in ids, f"{expected} missing from include=all"


class TestEntryShape:
    def test_emits_filter_debug_fields(self) -> None:
        body = build_apps_listing(_make_services(), env=_ENV)
        sonarr = next(e for e in body if e["id"] == "sonarr")
        for key in ("web_ui", "profiles", "enabled", "icon_url"):
            assert key in sonarr, f"{key} missing from response entry"
        assert sonarr["web_ui"] is True
        assert sonarr["enabled"] is True
        assert sonarr["profiles"] == []

    def test_propagates_explicit_icon_url(self) -> None:
        body = build_apps_listing(_make_services(), env=_ENV)
        custom = next(e for e in body if e["id"] == "custom-svc")
        assert custom["icon_url"] == "https://example.test/custom.png"

    def test_default_icon_url_is_empty_string(self) -> None:
        body = build_apps_listing(_make_services(), env=_ENV)
        sonarr = next(e for e in body if e["id"] == "sonarr")
        assert sonarr["icon_url"] == ""

    def test_profile_gated_entry_includes_profiles_list(self) -> None:
        body = build_apps_listing(
            _make_services(), include_all=True, env=_ENV,
        )
        plex = next(e for e in body if e["id"] == "plex")
        assert plex["profiles"] == ["plex"]
        # Backend reports enabled state regardless of whether the
        # entry was filtered, so the UI can render "disabled" hint.
        assert plex["enabled"] is False


class TestEmptyRegistry:
    def test_handles_empty_registry_gracefully(self) -> None:
        body = build_apps_listing([], env=_ENV)
        # Only the synthetic controller entry should remain.
        assert len(body) == 1
        assert body[0]["id"] == "controller"


class TestControllerPortPropagation:
    def test_controller_port_threaded_into_response(self) -> None:
        body = build_apps_listing(
            _make_services(), env=_ENV, controller_port=8765,
        )
        ctrl = next(e for e in body if e["id"] == "controller")
        assert ctrl["port"] == 8765
        assert ctrl["published_port"] == 8765
