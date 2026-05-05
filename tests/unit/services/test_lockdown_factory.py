"""Unit tests for ``LockdownFactory`` (ADR-0008 Phase 2).

Pins the env-var → adapter mapping so a deployment that's missing
any one client's creds doesn't break the auto-heal loop's startup.
"""

from __future__ import annotations

from typing import Any

import pytest

from media_stack.adapters._shared.download_client_lockdown import (
    ArrLockdownAdapter,
    QBittorrentLockdownAdapter,
    SabnzbdLockdownAdapter,
)
from media_stack.services.download_lockdown_service import (
    DownloadLockdownService,
)
from media_stack.services.lockdown_factory import LockdownFactory


def _env_factory(env: dict[str, str]) -> Any:
    """Return an ``env_getter`` callable backed by the supplied dict."""
    def _get(key: str, default: str = "") -> str:
        return env.get(key, default)
    return _get


class TestBuildAdapters:
    def test_full_env_builds_six_adapters(self) -> None:
        env = {
            "QBIT_URL": "http://qbit:8080",
            "QBIT_USERNAME": "admin",
            "QBIT_PASSWORD": "secret",
            "SABNZBD_URL": "http://sab:8080",
            "SABNZBD_API_KEY": "sab-key",
            "SONARR_URL": "http://sonarr:8989",
            "SONARR_API_KEY": "sonarr-key",
            "RADARR_URL": "http://radarr:7878",
            "RADARR_API_KEY": "radarr-key",
            "LIDARR_URL": "http://lidarr:8686",
            "LIDARR_API_KEY": "lidarr-key",
            "READARR_URL": "http://readarr:8787",
            "READARR_API_KEY": "readarr-key",
        }
        factory = LockdownFactory(env_getter=_env_factory(env))
        adapters = factory.build_adapters()
        ids = [a.client_id for a in adapters]
        assert ids == [
            "qbittorrent", "sabnzbd",
            "sonarr", "radarr", "lidarr", "readarr",
        ]

    def test_empty_env_builds_zero_adapters(self) -> None:
        factory = LockdownFactory(env_getter=_env_factory({}))
        assert factory.build_adapters() == []

    def test_qbit_skipped_when_url_missing(self) -> None:
        env = {
            "QBIT_USERNAME": "admin",
            "QBIT_PASSWORD": "secret",
            "SABNZBD_URL": "http://sab:8080",
            "SABNZBD_API_KEY": "sab-key",
        }
        adapters = LockdownFactory(env_getter=_env_factory(env)).build_adapters()
        ids = [a.client_id for a in adapters]
        assert ids == ["sabnzbd"]
        assert not any(isinstance(a, QBittorrentLockdownAdapter) for a in adapters)

    def test_sab_requires_both_url_and_apikey(self) -> None:
        env_url_only = {"SABNZBD_URL": "http://sab:8080"}
        adapters = LockdownFactory(env_getter=_env_factory(env_url_only)).build_adapters()
        assert adapters == []

        env_key_only = {"SABNZBD_API_KEY": "sab-key"}
        adapters = LockdownFactory(env_getter=_env_factory(env_key_only)).build_adapters()
        assert adapters == []

    def test_arr_skipped_individually_when_creds_missing(self) -> None:
        env = {
            "SONARR_URL": "http://sonarr:8989",
            "SONARR_API_KEY": "sonarr-key",
            # Radarr URL only — no key.
            "RADARR_URL": "http://radarr:7878",
            # Lidarr key only — no URL.
            "LIDARR_API_KEY": "lidarr-key",
        }
        adapters = LockdownFactory(env_getter=_env_factory(env)).build_adapters()
        ids = [a.client_id for a in adapters]
        assert ids == ["sonarr"]

    def test_qbit_and_sab_with_arrs(self) -> None:
        env = {
            "QBIT_URL": "http://qbit:8080",
            "QBIT_USERNAME": "admin",
            "QBIT_PASSWORD": "x",
            "RADARR_URL": "http://radarr:7878",
            "RADARR_API_KEY": "r-key",
        }
        adapters = LockdownFactory(env_getter=_env_factory(env)).build_adapters()
        ids = [a.client_id for a in adapters]
        # qbit + radarr (only the configured arr).
        assert ids == ["qbittorrent", "radarr"]


class TestBuild:
    def test_build_returns_a_service_with_adapters(self) -> None:
        env = {
            "QBIT_URL": "http://qbit:8080",
            "QBIT_USERNAME": "admin",
            "QBIT_PASSWORD": "x",
        }
        factory = LockdownFactory(env_getter=_env_factory(env))
        svc = factory.build()
        assert isinstance(svc, DownloadLockdownService)
        # State load works even with no state file.
        state = svc.get_state()
        assert state["engaged"] is False


class TestSingleton:
    def test_singleton_returns_same_instance(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        LockdownFactory.reset_for_tests()
        # Singleton uses os.environ; clear any qbit env so the
        # adapter list is empty + deterministic.
        for key in (
            "QBIT_URL", "QBIT_USERNAME", "QBIT_PASSWORD",
            "SABNZBD_URL", "SABNZBD_API_KEY",
            "SONARR_URL", "SONARR_API_KEY",
            "RADARR_URL", "RADARR_API_KEY",
            "LIDARR_URL", "LIDARR_API_KEY",
            "READARR_URL", "READARR_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        a = LockdownFactory.singleton()
        b = LockdownFactory.singleton()
        assert a is b
        LockdownFactory.reset_for_tests()

    def test_reset_for_tests_returns_fresh_instance(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        LockdownFactory.reset_for_tests()
        for key in (
            "QBIT_URL", "QBIT_USERNAME", "QBIT_PASSWORD",
            "SABNZBD_URL", "SABNZBD_API_KEY",
            "SONARR_URL", "SONARR_API_KEY",
            "RADARR_URL", "RADARR_API_KEY",
            "LIDARR_URL", "LIDARR_API_KEY",
            "READARR_URL", "READARR_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)
        first = LockdownFactory.singleton()
        LockdownFactory.reset_for_tests()
        second = LockdownFactory.singleton()
        assert first is not second
        LockdownFactory.reset_for_tests()
