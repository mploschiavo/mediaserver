"""Unit tests for ``api.services.sw_config``.

Covers:
  * ``_normalize_path`` (trailing slashes, leading slash, repeats)
  * ``_resolve_dashboard_basepath`` (env override / profile / default)
  * ``_list_sister_app_prefixes`` (registry walk + dashboard exclusion)
  * ``_build_denylist_patterns`` (regex shape under three basepath
    layouts)
  * ``get_sw_config`` (full-payload happy path + degraded paths
    with profile/registry unavailable)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from media_stack.api.services import sw_config as mod


# ---------------------------------------------------------------------------
# _normalize_path
# ---------------------------------------------------------------------------


class TestNormalizePath:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("/app/media-stack-ui", "/app/media-stack-ui"),
            ("/app/media-stack-ui/", "/app/media-stack-ui"),
            ("/app/media-stack-ui///", "/app/media-stack-ui"),
            ("//app//media-stack-ui//", "/app/media-stack-ui"),
            ("app/media-stack-ui", "/app/media-stack-ui"),
            ("/app", "/app"),
            # ``/`` and ``//`` collapse to a single slash — that's the
            # ``no-segments`` case the helper preserves rather than
            # returning empty (callers concat segments after it).
            ("/", "/"),
            ("//", "/"),
        ],
    )
    def test_canonicalizes(self, given: str, expected: str) -> None:
        assert mod._normalize_path(given) == expected

    def test_empty_input(self) -> None:
        assert mod._normalize_path("") == ""


# ---------------------------------------------------------------------------
# _resolve_dashboard_basepath
# ---------------------------------------------------------------------------


class TestResolveDashboardBasepath:
    def test_env_override_wins(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "DASHBOARD_BASEPATH_OVERRIDE", "/custom/dashboard/",
        )
        assert mod._resolve_dashboard_basepath() == "/custom/dashboard"

    def test_env_override_blank_falls_through(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("DASHBOARD_BASEPATH_OVERRIDE", "   ")
        with patch.object(
            mod, "_read_app_path_prefix_from_profile", return_value="/app",
        ):
            assert (
                mod._resolve_dashboard_basepath() == "/app/media-stack-ui"
            )

    def test_default_when_profile_returns_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DASHBOARD_BASEPATH_OVERRIDE", raising=False)
        with patch.object(
            mod, "_read_app_path_prefix_from_profile", return_value="/app",
        ):
            assert (
                mod._resolve_dashboard_basepath() == "/app/media-stack-ui"
            )

    def test_custom_app_prefix_from_profile(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DASHBOARD_BASEPATH_OVERRIDE", raising=False)
        with patch.object(
            mod, "_read_app_path_prefix_from_profile",
            return_value="/dashboards",
        ):
            assert (
                mod._resolve_dashboard_basepath()
                == "/dashboards/media-stack-ui"
            )


# ---------------------------------------------------------------------------
# _read_app_path_prefix_from_profile
# ---------------------------------------------------------------------------


class TestReadAppPathPrefixFromProfile:
    def test_returns_profile_value(self) -> None:
        with patch.dict(
            "sys.modules",
            {
                "media_stack.api.services.config._routing": _FakeRouting(
                    {"app_path_prefix": "/dashboards"},
                ),
            },
        ):
            # Re-import via the lazy import inside the helper.
            assert (
                mod._read_app_path_prefix_from_profile() == "/dashboards"
            )

    def test_returns_default_on_missing(self) -> None:
        with patch.dict(
            "sys.modules",
            {
                "media_stack.api.services.config._routing": _FakeRouting(
                    {},
                ),
            },
        ):
            assert mod._read_app_path_prefix_from_profile() == "/app"

    def test_returns_default_on_exception(self) -> None:
        # Stub a module whose ``get_routing`` raises; helper must
        # swallow and fall back without propagating.
        bad = type(
            "BadMod", (),
            {"get_routing": staticmethod(_raise_runtime)},
        )()
        with patch.dict(
            "sys.modules",
            {"media_stack.api.services.config._routing": bad},
        ):
            assert mod._read_app_path_prefix_from_profile() == "/app"


def _raise_runtime() -> dict[str, Any]:
    raise RuntimeError("profile load failed")


class _FakeRouting:
    """Stand-in for the real ``_routing`` module — exposes the
    single ``get_routing`` callable the helper imports."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def get_routing(self) -> dict[str, Any]:
        return self._payload


# ---------------------------------------------------------------------------
# _list_sister_app_prefixes
# ---------------------------------------------------------------------------


class _FakeService:
    def __init__(self, sid: str) -> None:
        self.id = sid


class TestListSisterAppPrefixes:
    def test_excludes_the_dashboard_itself(self) -> None:
        services = [
            _FakeService("media-stack-ui"),
            _FakeService("sonarr"),
            _FakeService("radarr"),
            _FakeService("jellyfin"),
        ]
        with (
            patch.object(
                mod,
                "_read_app_path_prefix_from_profile",
                return_value="/app",
            ),
            patch.dict(
                "sys.modules",
                {
                    "media_stack.api.services.registry": type(
                        "M", (), {"SERVICES": services},
                    )(),
                },
            ),
        ):
            out = mod._list_sister_app_prefixes(
                basepath="/app/media-stack-ui",
            )
            assert "/app/media-stack-ui" not in out
            assert "/app/sonarr" in out
            assert "/app/radarr" in out
            assert "/app/jellyfin" in out

    def test_skips_services_without_id(self) -> None:
        services = [_FakeService(""), _FakeService("sonarr")]
        with (
            patch.object(
                mod,
                "_read_app_path_prefix_from_profile",
                return_value="/app",
            ),
            patch.dict(
                "sys.modules",
                {
                    "media_stack.api.services.registry": type(
                        "M", (), {"SERVICES": services},
                    )(),
                },
            ),
        ):
            assert mod._list_sister_app_prefixes(
                basepath="/app/media-stack-ui",
            ) == ["/app/sonarr"]

    def test_returns_empty_when_registry_fails(self) -> None:
        bad = type("M", (), {})()
        # Simulate import error — module exists but lacks SERVICES.
        with patch.dict(
            "sys.modules",
            {"media_stack.api.services.registry": bad},
        ):
            out = mod._list_sister_app_prefixes(
                basepath="/app/media-stack-ui",
            )
            assert out == []

    def test_results_are_sorted(self) -> None:
        services = [
            _FakeService("zonar"),
            _FakeService("alpha"),
            _FakeService("media-stack-ui"),
            _FakeService("middle"),
        ]
        with (
            patch.object(
                mod,
                "_read_app_path_prefix_from_profile",
                return_value="/app",
            ),
            patch.dict(
                "sys.modules",
                {
                    "media_stack.api.services.registry": type(
                        "M", (), {"SERVICES": services},
                    )(),
                },
            ),
        ):
            out = mod._list_sister_app_prefixes(
                basepath="/app/media-stack-ui",
            )
            assert out == ["/app/alpha", "/app/middle", "/app/zonar"]


# ---------------------------------------------------------------------------
# _build_denylist_patterns
# ---------------------------------------------------------------------------


class TestBuildDenylistPatterns:
    def test_default_layout(self) -> None:
        patterns = mod._build_denylist_patterns(
            basepath="/app/media-stack-ui",
            sister_prefixes=["/app/sonarr"],
        )
        assert patterns[0] == r"^/api/"
        # The dashboard segment is run through ``re.escape`` so
        # dashes (and any future special chars) survive as literal
        # matches. Check for the escaped form.
        assert any("media\\-stack\\-ui" in p for p in patterns)

    def test_custom_dashboard_segment(self) -> None:
        patterns = mod._build_denylist_patterns(
            basepath="/app/operator-dashboard",
            sister_prefixes=[],
        )
        assert patterns[0] == r"^/api/"
        assert any("operator\\-dashboard" in p for p in patterns)

    def test_root_basepath_keeps_only_api_pattern(self) -> None:
        patterns = mod._build_denylist_patterns(
            basepath="/dashboard",
            sister_prefixes=[],
        )
        # Single-segment basepath isn't ``/app/<x>`` — only the API
        # rule survives.
        assert patterns == [r"^/api/"]

    def test_special_chars_in_segment_are_escaped(self) -> None:
        # A dot would otherwise match any char in regex.
        patterns = mod._build_denylist_patterns(
            basepath="/app/media.stack",
            sister_prefixes=[],
        )
        # Find the /app/ rule
        app_rule = next(p for p in patterns if "/app/" in p)
        assert "media\\.stack" in app_rule


# ---------------------------------------------------------------------------
# get_sw_config — end-to-end shape
# ---------------------------------------------------------------------------


class TestGetSwConfig:
    def test_full_payload_shape(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DASHBOARD_BASEPATH_OVERRIDE", raising=False)
        services = [
            _FakeService("media-stack-ui"),
            _FakeService("sonarr"),
            _FakeService("jellyfin"),
        ]
        with (
            patch.object(
                mod,
                "_read_app_path_prefix_from_profile",
                return_value="/app",
            ),
            patch.dict(
                "sys.modules",
                {
                    "media_stack.api.services.registry": type(
                        "M", (), {"SERVICES": services},
                    )(),
                },
            ),
        ):
            payload = mod.get_sw_config()
        assert payload["version"] == 1
        assert payload["basepath"] == "/app/media-stack-ui"
        assert payload["allowed_app_prefixes"] == ["/app/media-stack-ui"]
        assert sorted(payload["sister_app_prefixes"]) == [
            "/app/jellyfin",
            "/app/sonarr",
        ]
        assert payload["denylist_patterns"][0] == r"^/api/"
        # ``re.escape`` adds a backslash before each dash; verify the
        # escaped form so we don't accidentally green-light a regex
        # that lost its escapes.
        assert any(
            "media\\-stack\\-ui" in p for p in payload["denylist_patterns"]
        )

    def test_env_override_propagates_to_payload(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(
            "DASHBOARD_BASEPATH_OVERRIDE", "/dash/main",
        )
        with patch.dict(
            "sys.modules",
            {
                "media_stack.api.services.registry": type(
                    "M", (), {"SERVICES": []},
                )(),
            },
        ):
            payload = mod.get_sw_config()
        assert payload["basepath"] == "/dash/main"
        # Single-segment-after-/dash basepath emits only the /api/
        # rule (no /app/<x> filter).
        assert payload["denylist_patterns"] == [r"^/api/"]

    def test_degraded_mode_when_registry_unavailable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("DASHBOARD_BASEPATH_OVERRIDE", raising=False)
        with (
            patch.object(
                mod,
                "_read_app_path_prefix_from_profile",
                return_value="/app",
            ),
            patch.dict(
                "sys.modules",
                {
                    "media_stack.api.services.registry": type(
                        "M", (), {},
                    )(),
                },
            ),
        ):
            payload = mod.get_sw_config()
        # Sister apps unavailable but the dashboard's own basepath +
        # API rule still emit cleanly.
        assert payload["basepath"] == "/app/media-stack-ui"
        assert payload["sister_app_prefixes"] == []
        assert payload["denylist_patterns"][0] == r"^/api/"
