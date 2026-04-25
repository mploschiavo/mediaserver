"""Tests for the ``arr_protocol`` dataclasses + Protocol contract.

The Protocol is runtime-checkable, so we verify:
- Concrete adapters register as instances (via adapter suite, but
  we assert the shape here too).
- Dataclasses are frozen (reconciler assumes thread-safety).
- Field defaults match the documented invariants.
"""

from __future__ import annotations

import dataclasses

import pytest

from media_stack.services.media_integrity.arr_protocol import (
    AdapterCapabilities,
    ArrApp,
    MediaFile,
    MediaRelease,
    QualityProfile,
)


def test_media_release_is_frozen() -> None:
    release = MediaRelease(id="1", title="X", path="/media/x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        release.title = "Y"  # type: ignore[misc]


def test_media_file_is_frozen() -> None:
    mf = MediaFile(
        id="1",
        release_id="2",
        relative_path="x.mkv",
        absolute_path="/media/x.mkv",
        size=1,
        quality_name="WEBDL-1080p",
        quality_score=5,
        added_at="2026-04-20",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        mf.size = 2  # type: ignore[misc]


def test_quality_profile_is_frozen() -> None:
    qp = QualityProfile(id=1, name="HD", cutoff_id=5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        qp.name = "Other"  # type: ignore[misc]


def test_adapter_capabilities_defaults_allow_enforcement() -> None:
    """Default capabilities are optimistic so a newly-probed adapter
    can still apply policy; only probing can turn features off."""
    caps = AdapterCapabilities()
    assert caps.supports_auto_unmonitor_deleted is True
    assert caps.supports_rename is True
    assert caps.supports_hardlinks is True
    assert caps.supports_quality_profile_cutoff is True
    assert caps.supports_file_delete is True
    assert caps.supports_release_listing is True
    assert caps.probed_field_names == ()


def test_media_release_has_sensible_defaults() -> None:
    release = MediaRelease(id="1", title="T", path="/p")
    assert release.year is None
    assert release.quality_profile_id is None
    assert release.monitored is True


def test_media_file_source_torrent_hash_optional() -> None:
    """Empty source_torrent_hash means 'we don't know'; the
    reconciler uses this to skip torrent-client pause."""
    mf = MediaFile(
        id="1",
        release_id="2",
        relative_path="x",
        absolute_path="/x",
        size=0,
        quality_name="",
        quality_score=0,
        added_at="",
    )
    assert mf.source_torrent_hash == ""


def test_quality_profile_items_is_tuple_not_list() -> None:
    """Items is a tuple so the frozen dataclass stays hashable and
    passes across threads without aliasing risk."""
    qp = QualityProfile(id=1, name="x", cutoff_id=1, items=({"a": 1},))
    assert isinstance(qp.items, tuple)


def test_protocol_is_runtime_checkable() -> None:
    """Smoke test that ``isinstance(obj, ArrApp)`` works — critical
    for the service-dispatch pattern in turn-2 enforcer."""

    class _Minimal:
        name = "x"
        api_version = "v3"
        media_root = "/"
        capabilities = AdapterCapabilities()

        def get_media_management(self):  # noqa: D401 — stub
            return {}

        def put_media_management(self, cfg):
            return None

        def get_naming(self):
            return {}

        def put_naming(self, cfg):
            return None

        def list_releases(self):
            return []

        def list_files_for(self, release_id):
            return []

        def delete_file(self, file_id):
            return None

        def quality_profiles(self):
            return []

        def quality_score(self, file):
            return 0

        def media_management_field_map(self):
            return {}

        def naming_field_map(self):
            return {}

        def list_releases_for_file(self, file_id):
            return []

    assert isinstance(_Minimal(), ArrApp)


def test_protocol_rejects_incomplete_impl() -> None:
    class _Incomplete:
        name = "x"
        # Missing api_version, media_root, methods …

    assert not isinstance(_Incomplete(), ArrApp)
