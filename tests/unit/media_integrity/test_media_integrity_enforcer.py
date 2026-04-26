"""Tests for ``ServarrConfigEnforcer``.

Uses a fake adapter implementing the ``ArrApp`` protocol to avoid
HTTP. The fake records every GET/PUT call so we can assert the
enforcer:

- Reads current config via GET before mutating.
- Only PUTs when there's a delta (no-op when already compliant).
- Translates canonical keys via the adapter's field map.
- Skips fields the adapter says it doesn't support.
- Continues across adapters when one fails.
- Emits success + failure events + audit entries.
"""

from __future__ import annotations

from typing import Any

import pytest

from media_stack.core.auth.users.audit_actions import (
    MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
    MEDIA_INTEGRITY_CONFIG_ENFORCED,
)
from media_stack.core.events import (
    EventBus,
    MediaIntegrityConfigEnforced,
    MediaIntegrityConfigEnforceFailed,
)
from media_stack.services.media_integrity.arr_protocol import (
    AdapterCapabilities,
    MediaFile,
    MediaRelease,
)
from media_stack.services.media_integrity.enforcer import (
    EnforceResult,
    ServarrConfigEnforcer,
    _redact,
)
from media_stack.services.media_integrity.policy import ServarrPolicy


class _FakeAdapter:
    """Minimal ArrApp implementation backed by in-memory dicts."""

    def __init__(
        self,
        *,
        name: str = "radarr",
        media_management: dict[str, Any] | None = None,
        naming: dict[str, Any] | None = None,
        capabilities: AdapterCapabilities | None = None,
        field_map_mm: dict[str, str] | None = None,
        field_map_naming: dict[str, str] | None = None,
        get_mm_raises: Exception | None = None,
        put_mm_raises: Exception | None = None,
        get_naming_raises: Exception | None = None,
        put_naming_raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.api_version = "v3"
        self.media_root = "/media"
        self.capabilities = capabilities or AdapterCapabilities()
        self._mm = dict(media_management or {})
        self._naming = dict(naming or {})
        self._field_map_mm = dict(
            field_map_mm
            or {
                "auto_unmonitor_previously_downloaded": "autoUnmonitorPreviouslyDownloadedMovies",
                "use_hardlinks": "copyUsingHardlinks",
                "delete_empty_folders": "deleteEmptyFolders",
                "import_extra_files": "importExtraFiles",
                "extra_file_extensions": "extraFileExtensions",
                "skip_free_space_check": "skipFreeSpaceCheckWhenImporting",
                "minimum_free_space_mb": "minimumFreeSpaceWhenImporting",
                "create_empty_media_folders": "createEmptyMovieFolders",
                "unmonitor_deleted": "autoUnmonitorDeletedMovies",
            }
        )
        self._field_map_naming = dict(
            field_map_naming or {"rename_files": "renameMovies"}
        )
        self.get_calls: list[str] = []
        self.put_calls: list[tuple[str, dict[str, Any]]] = []
        self._get_mm_raises = get_mm_raises
        self._put_mm_raises = put_mm_raises
        self._get_naming_raises = get_naming_raises
        self._put_naming_raises = put_naming_raises

    def get_media_management(self) -> dict[str, Any]:
        self.get_calls.append("mm")
        if self._get_mm_raises:
            raise self._get_mm_raises
        return dict(self._mm)

    def put_media_management(self, cfg: dict[str, Any]) -> None:
        if self._put_mm_raises:
            raise self._put_mm_raises
        self.put_calls.append(("mm", dict(cfg)))
        self._mm = dict(cfg)

    def get_naming(self) -> dict[str, Any]:
        self.get_calls.append("naming")
        if self._get_naming_raises:
            raise self._get_naming_raises
        return dict(self._naming)

    def put_naming(self, cfg: dict[str, Any]) -> None:
        if self._put_naming_raises:
            raise self._put_naming_raises
        self.put_calls.append(("naming", dict(cfg)))
        self._naming = dict(cfg)

    def media_management_field_map(self) -> dict[str, str]:
        return dict(self._field_map_mm)

    def naming_field_map(self) -> dict[str, str]:
        return dict(self._field_map_naming)

    # protocol completeness — unused by enforcer
    def list_releases(self) -> list[MediaRelease]:
        return []

    def list_files_for(self, release_id: str) -> list[MediaFile]:
        return []

    def delete_file(self, file_id: str) -> None:
        pass

    def quality_profiles(self) -> list:
        return []

    def quality_score(self, file: MediaFile) -> int:
        return 0


class _AuditSpy:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> dict[str, Any]:
        self.entries.append(kwargs)
        return kwargs


class _BusSpy:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------


def test_enforcer_brings_drifted_field_back_to_compliance() -> None:
    adapter = _FakeAdapter(
        media_management={
            "autoUnmonitorPreviouslyDownloadedMovies": False,  # drifted
            "copyUsingHardlinks": True,  # compliant
            "deleteEmptyFolders": False,
            "importExtraFiles": True,
            "extraFileExtensions": "srt,ass,ssa,vtt,smi,sub",
            "skipFreeSpaceCheckWhenImporting": False,
            "minimumFreeSpaceWhenImporting": 500,
            "createEmptyMovieFolders": False,
            "autoUnmonitorDeletedMovies": False,
            "id": 1,
        },
        naming={"renameMovies": False, "id": 1},
    )
    audit = _AuditSpy()
    bus = _BusSpy()
    enforcer = ServarrConfigEnforcer(
        policy=ServarrPolicy(), audit=audit, event_bus=bus
    )
    report = enforcer.apply([adapter])

    assert len(report.results) == 1
    result = report.results[0]
    assert result.app == "radarr"
    assert "autoUnmonitorPreviouslyDownloadedMovies" in result.mediamanagement_changed_fields
    assert "deleteEmptyFolders" in result.mediamanagement_changed_fields
    assert "renameMovies" in result.naming_changed_fields
    assert result.failures == ()

    # PUT was issued for both sections
    sections_put = {c[0] for c in adapter.put_calls}
    assert sections_put == {"mm", "naming"}
    # MediaManagement PUT preserved the existing ``id`` field
    mm_put = next(c for c in adapter.put_calls if c[0] == "mm")[1]
    assert mm_put["id"] == 1
    assert mm_put["autoUnmonitorPreviouslyDownloadedMovies"] is True

    # Audit entries + events
    success_audits = [e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_CONFIG_ENFORCED]
    assert len(success_audits) == 1
    assert success_audits[0]["target"] == "radarr"

    success_events = [e for e in bus.events if isinstance(e, MediaIntegrityConfigEnforced)]
    assert len(success_events) == 1
    assert success_events[0].app == "radarr"
    assert "mediamanagement" in success_events[0].sections_applied
    assert "naming" in success_events[0].sections_applied


def test_enforcer_skips_put_when_already_compliant() -> None:
    """No-op pass: every field already matches policy → no PUT."""
    adapter = _FakeAdapter(
        media_management={
            "autoUnmonitorPreviouslyDownloadedMovies": True,
            "copyUsingHardlinks": True,
            "deleteEmptyFolders": True,
            "importExtraFiles": True,
            "extraFileExtensions": "srt,ass,ssa,vtt,smi,sub",
            "skipFreeSpaceCheckWhenImporting": False,
            "minimumFreeSpaceWhenImporting": 500,
            "createEmptyMovieFolders": False,
            "autoUnmonitorDeletedMovies": False,
            "id": 1,
        },
        naming={"renameMovies": True, "id": 1},
    )
    enforcer = ServarrConfigEnforcer(policy=ServarrPolicy())
    report = enforcer.apply([adapter])

    assert report.total_fields_changed == 0
    assert adapter.put_calls == []


def test_enforcer_continues_after_one_adapter_fails() -> None:
    """Transient Sonarr outage must not stop Radarr enforcement."""
    bad = _FakeAdapter(
        name="sonarr",
        get_mm_raises=RuntimeError("connection refused"),
    )
    good = _FakeAdapter(
        name="radarr",
        media_management={
            "autoUnmonitorPreviouslyDownloadedMovies": False,
            "copyUsingHardlinks": True,
            "id": 1,
        },
        naming={"renameMovies": True, "id": 1},
    )
    audit = _AuditSpy()
    bus = _BusSpy()
    enforcer = ServarrConfigEnforcer(
        policy=ServarrPolicy(), audit=audit, event_bus=bus
    )
    report = enforcer.apply([bad, good])

    assert report.total_failures >= 1
    assert any(r.failures for r in report.results if r.app == "sonarr")
    assert good.put_calls != []  # radarr still got mutated

    # Failure event present
    failures = [e for e in bus.events if isinstance(e, MediaIntegrityConfigEnforceFailed)]
    assert any(f.app == "sonarr" for f in failures)
    failure_audits = [
        e for e in audit.entries if e["action"] == MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED
    ]
    assert any(e["target"] == "sonarr" for e in failure_audits)


def test_enforcer_skips_unsupported_field_via_capabilities() -> None:
    adapter = _FakeAdapter(
        media_management={
            "autoUnmonitorPreviouslyDownloadedMovies": False,
            "copyUsingHardlinks": False,  # would normally flip to True
            "id": 1,
        },
        capabilities=AdapterCapabilities(supports_hardlinks=False),
    )
    enforcer = ServarrConfigEnforcer(policy=ServarrPolicy())
    enforcer.apply([adapter])
    mm_put = next(c for c in adapter.put_calls if c[0] == "mm")[1]
    # Hardlinks flag NOT touched; still false.
    assert mm_put["copyUsingHardlinks"] is False


def test_enforcer_skips_unsupported_auto_unmonitor() -> None:
    adapter = _FakeAdapter(
        media_management={
            "autoUnmonitorPreviouslyDownloadedMovies": True,
            "copyUsingHardlinks": True,
            "deleteEmptyFolders": True,
            "importExtraFiles": True,
            "extraFileExtensions": "srt,ass,ssa,vtt,smi,sub",
            "skipFreeSpaceCheckWhenImporting": False,
            "minimumFreeSpaceWhenImporting": 500,
            "createEmptyMovieFolders": False,
            # Note: NO autoUnmonitorDeletedMovies key
            "id": 1,
        },
        naming={"renameMovies": True, "id": 1},
        capabilities=AdapterCapabilities(supports_auto_unmonitor_deleted=False),
    )
    enforcer = ServarrConfigEnforcer(policy=ServarrPolicy())
    enforcer.apply([adapter])
    # No PUT because every other field is compliant and the unsupported
    # one was skipped.
    assert adapter.put_calls == []


def test_enforcer_handles_put_failure() -> None:
    adapter = _FakeAdapter(
        media_management={
            "autoUnmonitorPreviouslyDownloadedMovies": False,
            "id": 1,
        },
        put_mm_raises=RuntimeError("503 Service Unavailable"),
    )
    audit = _AuditSpy()
    bus = _BusSpy()
    enforcer = ServarrConfigEnforcer(
        policy=ServarrPolicy(), audit=audit, event_bus=bus
    )
    report = enforcer.apply([adapter])
    result = report.results[0]
    assert result.failures != ()
    assert "mediamanagement:" in result.failures[0]
    assert any(
        isinstance(e, MediaIntegrityConfigEnforceFailed) for e in bus.events
    )


def test_enforcer_handles_put_naming_failure() -> None:
    adapter = _FakeAdapter(
        naming={"renameMovies": False, "id": 1},
        put_naming_raises=RuntimeError("403 Forbidden"),
    )
    enforcer = ServarrConfigEnforcer(policy=ServarrPolicy())
    report = enforcer.apply([adapter])
    assert any("naming:" in f for f in report.results[0].failures)


def test_enforcer_works_without_audit_or_bus() -> None:
    """Bus + audit are optional — service-only construction must not
    crash if neither is wired."""
    adapter = _FakeAdapter(
        media_management={
            "autoUnmonitorPreviouslyDownloadedMovies": False,
            "id": 1,
        },
    )
    enforcer = ServarrConfigEnforcer(policy=ServarrPolicy())
    report = enforcer.apply([adapter])
    assert report.total_fields_changed >= 1


def test_redact_strips_apikey_strings() -> None:
    raw = "401 Unauthorized: apikey=abcdef0123456789abcdef0123456789"
    assert "abcdef" not in _redact(raw)


def test_redact_caps_runaway_errors() -> None:
    long = "x" * 2000
    assert len(_redact(long)) <= 500


def test_redact_handles_empty_input() -> None:
    assert _redact("") == ""


def test_redact_strips_long_hex_token() -> None:
    raw = "Authorization failed for d41d8cd98f00b204e9800998ecf8427e"
    assert "d41d8cd9" not in _redact(raw)


def test_enforcer_reports_aggregate_counts() -> None:
    a = _FakeAdapter(
        name="radarr",
        media_management={"autoUnmonitorPreviouslyDownloadedMovies": False, "id": 1},
    )
    b = _FakeAdapter(
        name="sonarr",
        media_management={"autoUnmonitorPreviouslyDownloadedMovies": False, "id": 1},
        field_map_mm={
            "auto_unmonitor_previously_downloaded": "autoUnmonitorPreviouslyDownloadedMovies",
        },
    )
    enforcer = ServarrConfigEnforcer(policy=ServarrPolicy())
    report = enforcer.apply([a, b])
    assert len(report.results) == 2
    assert report.total_fields_changed >= 2  # at least one per adapter
