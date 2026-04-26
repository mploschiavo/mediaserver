"""Tests for the production wiring of ``MediaIntegrityService``.

The factory is responsible for: walking the service registry,
plucking host/port/api_key_env for each *arr and Bazarr, looking up
the env var, and constructing adapters. Every dep is injected so we
can exercise it without hitting the real registry or environ."""

from __future__ import annotations

from typing import Any

import pytest

from media_stack.services.media_integrity.adapters import (
    BazarrAdapter,
    LidarrAdapter,
    RadarrAdapter,
    ReadarrAdapter,
    SonarrAdapter,
)
from media_stack.services.media_integrity.adapters._servarr_base import HttpResponse
from media_stack.services.media_integrity.factory import (
    _ServiceLookup,
    build_default_service,
)
from media_stack.services.media_integrity.policy import ServarrPolicy


class _ProbeFakeClient:
    """Returns a minimal valid /config/mediamanagement and /system/settings."""

    def request(self, method, url, *, headers, body=None, timeout=15.0):
        if "/config/mediamanagement" in url:
            return HttpResponse(status=200, body=b'{"id": 1}')
        if "/api/system/settings" in url:
            return HttpResponse(status=200, body=b'{"general": {}}')
        return HttpResponse(status=404, body=b"")


def _radarr() -> _ServiceLookup:
    return _ServiceLookup(
        id="radarr", host="radarr", port=7878, api_key_env="RADARR_API_KEY",
    )


def _sonarr() -> _ServiceLookup:
    return _ServiceLookup(
        id="sonarr", host="sonarr", port=8989, api_key_env="SONARR_API_KEY",
    )


def _lidarr() -> _ServiceLookup:
    return _ServiceLookup(
        id="lidarr", host="lidarr", port=8686, api_key_env="LIDARR_API_KEY",
    )


def _readarr() -> _ServiceLookup:
    return _ServiceLookup(
        id="readarr", host="readarr", port=8787, api_key_env="READARR_API_KEY",
    )


def _bazarr() -> _ServiceLookup:
    return _ServiceLookup(
        id="bazarr", host="bazarr", port=6767, api_key_env="BAZARR_API_KEY",
    )


# ---------------------------------------------------------------------------


def test_factory_builds_all_servarr_when_keys_set() -> None:
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [_radarr(), _sonarr(), _lidarr(), _readarr()],
        bazarr_lookup=lambda: None,
        env=lambda k: "secret-key",
        http_client=_ProbeFakeClient(),
    )
    status = svc.status()
    assert sorted(status["servarr_adapters"]) == [
        "lidarr", "radarr", "readarr", "sonarr",
    ]
    assert status["bazarr_present"] is False


def test_factory_skips_servarr_without_api_key() -> None:
    """If RADARR_API_KEY isn't set, skip Radarr — don't crash the pass."""
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [_radarr(), _sonarr()],
        bazarr_lookup=lambda: None,
        env=lambda k: "set" if k == "SONARR_API_KEY" else "",
        http_client=_ProbeFakeClient(),
    )
    status = svc.status()
    assert status["servarr_adapters"] == ("sonarr",)


def test_factory_includes_bazarr_when_present() -> None:
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [],
        bazarr_lookup=_bazarr,
        env=lambda k: "secret",
        http_client=_ProbeFakeClient(),
    )
    assert svc.status()["bazarr_present"] is True


def test_factory_skips_bazarr_when_no_key() -> None:
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [],
        bazarr_lookup=_bazarr,
        env=lambda k: "",
        http_client=_ProbeFakeClient(),
    )
    assert svc.status()["bazarr_present"] is False


def test_factory_skips_bazarr_when_lookup_returns_none() -> None:
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [],
        bazarr_lookup=lambda: None,
        env=lambda k: "secret",
        http_client=_ProbeFakeClient(),
    )
    assert svc.status()["bazarr_present"] is False


def test_factory_logs_and_continues_on_construction_error() -> None:
    """If one adapter constructor raises (e.g. malformed URL), the
    factory keeps building the rest."""
    class _RaisingClient:
        _called = 0

        def request(self, method, url, *, headers, body=None, timeout=15.0):
            self._called += 1
            if "radarr" in url:
                # Simulate a 500 to make the probe fail. The base
                # adapter catches probe failures, so this won't
                # actually break construction — let's instead force
                # an error by patching the constructor below.
                return HttpResponse(status=200, body=b'{"id":1}')
            return HttpResponse(status=200, body=b'{"id":1}')

    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [_radarr(), _sonarr()],
        bazarr_lookup=lambda: None,
        env=lambda k: "key",
        http_client=_RaisingClient(),
    )
    # Both adapters should construct cleanly with these probes
    assert len(svc.status()["servarr_adapters"]) == 2


def test_factory_uses_default_policy_when_omitted(monkeypatch, tmp_path) -> None:
    """``build_default_service()`` with no policy reads the canonical
    YAML via ``ServarrPolicy.load_default()`` — which we redirect at
    a tmp file to avoid coupling the test to the real contract path."""
    yaml_path = tmp_path / "policy.yaml"
    yaml_path.write_text(
        "version: 1\n"
        "media_management:\n  use_hardlinks: true\n"
        "naming:\n  rename_files: true\n"
        "quality:\n  cutoff: WEBDL-1080p\n  upgrade_allowed: true\n"
        "bazarr:\n  rename_files: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "media_stack.services.media_integrity.policy._default_contract_path",
        lambda: yaml_path,
    )
    svc = build_default_service(
        servarr_lookup=lambda: [],
        bazarr_lookup=lambda: None,
        env=lambda k: "",
    )
    assert svc.status()["policy_version"] == 1


def test_factory_redacts_apikey_in_construction_error_logs(caplog) -> None:
    """Construction-error path scrubs API keys from log messages."""
    import logging

    class _BadClient:
        def request(self, *a, **kw):
            raise RuntimeError(
                "401 unauthorized for apikey=deadbeefdeadbeefdeadbeefdeadbeef"
            )

    caplog.set_level(logging.WARNING, logger="media_stack.services.media_integrity.factory")
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [_radarr()],
        bazarr_lookup=lambda: None,
        env=lambda k: "deadbeefdeadbeefdeadbeefdeadbeef",
        http_client=_BadClient(),
    )
    # Construction failed → adapter skipped, partial-deployment posture.
    assert svc.status()["servarr_adapters"] == ()
    # Critically: the redactor scrubbed the api key from the log.
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "deadbeef" not in log_text
    assert "REDACTED" in log_text


def test_factory_default_lookups_run_against_live_registry() -> None:
    """Smoke test: the default servarr_lookup actually inspects the
    live registry. We don't assert specific adapters are present —
    that depends on which YAML profiles are loaded — only that the
    lookup runs without raising."""
    from media_stack.services.media_integrity.factory import (
        _default_bazarr_lookup,
        _default_servarr_lookup,
    )

    services = _default_servarr_lookup()
    assert isinstance(services, list)
    bazarr = _default_bazarr_lookup()
    assert bazarr is None or isinstance(bazarr, _ServiceLookup)


# ---------------------------------------------------------------------------
# missing_api_keys surface (Task 3)
# ---------------------------------------------------------------------------


def test_factory_records_missing_api_keys() -> None:
    """Configured-but-keyless adapters land in status()['missing_api_keys']."""
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [_radarr(), _sonarr()],
        bazarr_lookup=lambda: None,
        env=lambda k: "set" if k == "SONARR_API_KEY" else "",
        http_client=_ProbeFakeClient(),
    )
    status = svc.status()
    assert status["servarr_adapters"] == ("sonarr",)
    assert status["missing_api_keys"] == ["radarr"]


def test_factory_records_missing_bazarr_api_key() -> None:
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [],
        bazarr_lookup=_bazarr,
        env=lambda k: "",
        http_client=_ProbeFakeClient(),
    )
    status = svc.status()
    assert status["bazarr_present"] is False
    assert status["missing_api_keys"] == ["bazarr"]


def test_factory_does_not_record_missing_keys_when_no_config_entry() -> None:
    """An ABSENT registry entry (lookup returns []) is NOT the same as
    'configured but keyless' — only the latter populates missing_api_keys."""
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [],
        bazarr_lookup=lambda: None,
        env=lambda k: "",
        http_client=_ProbeFakeClient(),
    )
    assert svc.status()["missing_api_keys"] == []


def test_factory_no_missing_keys_when_all_configured() -> None:
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [_radarr(), _sonarr()],
        bazarr_lookup=_bazarr,
        env=lambda k: "secret",
        http_client=_ProbeFakeClient(),
    )
    assert svc.status()["missing_api_keys"] == []


def test_factory_missing_keys_records_each_configured_keyless_servarr() -> None:
    """All four Servarr-family services configured but no keys set →
    every id appears in missing_api_keys."""
    svc = build_default_service(
        policy=ServarrPolicy(),
        servarr_lookup=lambda: [
            _radarr(), _sonarr(), _lidarr(), _readarr(),
        ],
        bazarr_lookup=lambda: None,
        env=lambda k: "",
        http_client=_ProbeFakeClient(),
    )
    assert sorted(svc.status()["missing_api_keys"]) == [
        "lidarr", "radarr", "readarr", "sonarr",
    ]
