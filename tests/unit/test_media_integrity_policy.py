"""Tests for ``media_integrity.policy`` — YAML loading, validation,
and per-adapter patch translation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from media_stack.services.media_integrity.adapters import RadarrAdapter, SonarrAdapter
from media_stack.services.media_integrity.arr_protocol import AdapterCapabilities
from media_stack.services.media_integrity.policy import (
    MediaManagementSection,
    NamingSection,
    QualitySection,
    ServarrPolicy,
)


CANONICAL_YAML = textwrap.dedent(
    """
    version: 1
    media_management:
      auto_unmonitor_previously_downloaded: true
      use_hardlinks: true
      delete_empty_folders: true
      import_extra_files: true
      extra_file_extensions: "srt,ass,ssa,vtt,smi,sub"
      skip_free_space_check: false
      minimum_free_space_mb: 500
      create_empty_media_folders: false
      unmonitor_deleted: false
    naming:
      rename_files: true
    quality:
      cutoff: "WEBDL-1080p"
      upgrade_allowed: true
    """
).strip()


def test_from_yaml_text_parses_canonical_contract() -> None:
    policy = ServarrPolicy.from_yaml_text(CANONICAL_YAML)
    assert policy.version == 1
    assert policy.media_management.auto_unmonitor_previously_downloaded is True
    assert policy.media_management.use_hardlinks is True
    assert policy.media_management.extra_file_extensions == "srt,ass,ssa,vtt,smi,sub"
    assert policy.media_management.minimum_free_space_mb == 500
    assert policy.media_management.unmonitor_deleted is False
    assert policy.naming.rename_files is True
    assert policy.quality.cutoff == "WEBDL-1080p"
    assert policy.quality.upgrade_allowed is True


def test_from_yaml_text_empty_uses_defaults() -> None:
    policy = ServarrPolicy.from_yaml_text("")
    assert policy == ServarrPolicy()


def test_from_yaml_text_rejects_non_mapping_root() -> None:
    with pytest.raises(ValueError, match="top-level must be a mapping"):
        ServarrPolicy.from_yaml_text("- just\n- a\n- list\n")


def test_from_yaml_text_rejects_non_mapping_section() -> None:
    with pytest.raises(ValueError, match="media_management must be a mapping"):
        ServarrPolicy.from_yaml_text("media_management: 42")
    with pytest.raises(ValueError, match="naming must be a mapping"):
        ServarrPolicy.from_yaml_text("naming: 42")
    with pytest.raises(ValueError, match="quality must be a mapping"):
        ServarrPolicy.from_yaml_text("quality: 42")


def test_minimum_free_space_mb_rejects_bool() -> None:
    """``minimum_free_space_mb: true`` would silently be 1 if we
    didn't explicitly reject — guard against a YAML footgun."""
    with pytest.raises(ValueError, match="got bool"):
        ServarrPolicy.from_yaml_text(
            "media_management:\n  minimum_free_space_mb: true\n"
        )


def test_bool_coercion_accepts_string_forms() -> None:
    policy = ServarrPolicy.from_yaml_text(
        'media_management:\n  use_hardlinks: "yes"\n  '
        'delete_empty_folders: "NO"\n'
    )
    assert policy.media_management.use_hardlinks is True
    assert policy.media_management.delete_empty_folders is False


def test_bool_coercion_rejects_bad_string() -> None:
    with pytest.raises(ValueError, match="expected bool"):
        ServarrPolicy.from_yaml_text(
            'media_management:\n  use_hardlinks: "maybe"\n'
        )


def test_int_coercion_accepts_digit_string() -> None:
    policy = ServarrPolicy.from_yaml_text(
        'media_management:\n  minimum_free_space_mb: "1024"\n'
    )
    assert policy.media_management.minimum_free_space_mb == 1024


def test_int_coercion_rejects_bad_value() -> None:
    with pytest.raises(ValueError, match="expected int"):
        ServarrPolicy.from_yaml_text(
            'media_management:\n  minimum_free_space_mb: "not a number"\n'
        )


def test_str_coercion_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="expected str"):
        ServarrPolicy.from_yaml_text(
            "media_management:\n  extra_file_extensions: 42\n"
        )


def test_from_path_reads_file(tmp_path: Path) -> None:
    file = tmp_path / "policy.yaml"
    file.write_text(CANONICAL_YAML, encoding="utf-8")
    policy = ServarrPolicy.from_path(file)
    assert policy.quality.cutoff == "WEBDL-1080p"


def test_load_default_raises_if_missing(monkeypatch, tmp_path: Path) -> None:
    """The stack refuses to boot without a policy — fail-closed."""
    missing = tmp_path / "does-not-exist.yaml"
    monkeypatch.setattr(
        "media_stack.services.media_integrity.policy._default_contract_path",
        lambda: missing,
    )
    with pytest.raises(FileNotFoundError, match="refuses to boot"):
        ServarrPolicy.load_default()


def test_load_default_reads_contract(monkeypatch, tmp_path: Path) -> None:
    file = tmp_path / "policy.yaml"
    file.write_text(CANONICAL_YAML, encoding="utf-8")
    monkeypatch.setattr(
        "media_stack.services.media_integrity.policy._default_contract_path",
        lambda: file,
    )
    policy = ServarrPolicy.load_default()
    assert policy.version == 1


def test_with_overrides_replaces_sections() -> None:
    base = ServarrPolicy()
    overridden = base.with_overrides(
        media_management=MediaManagementSection(use_hardlinks=False)
    )
    assert overridden.media_management.use_hardlinks is False
    # Untouched sections keep their defaults
    assert overridden.naming.rename_files is True
    # Original is unchanged (frozen)
    assert base.media_management.use_hardlinks is True


def test_with_overrides_naming_and_quality() -> None:
    base = ServarrPolicy()
    overridden = base.with_overrides(
        naming=NamingSection(rename_files=False),
        quality=QualitySection(cutoff="Bluray-1080p", upgrade_allowed=False),
    )
    assert overridden.naming.rename_files is False
    assert overridden.quality.cutoff == "Bluray-1080p"
    assert overridden.quality.upgrade_allowed is False


# ---------------------------------------------------------------------------
# Patch-builder tests — canonical → per-adapter field translation
# ---------------------------------------------------------------------------


class _FakeHttpClient:
    """Minimal fake: returns pre-seeded responses by (method, url) key."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, bytes]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, bytes | None]] = []

    def request(self, method, url, *, headers, body=None, timeout):
        from media_stack.services.media_integrity.adapters._servarr_base import (
            HttpResponse,
        )
        self.calls.append((method, url, body))
        status, body_out = self._responses.get((method, url), (404, b""))
        return HttpResponse(status=status, body=body_out)


def _radarr_probe_body(include_auto_unmon: bool = True) -> bytes:
    import json
    cfg = {
        "autoUnmonitorPreviouslyDownloadedMovies": False,
        "copyUsingHardlinks": False,
        "deleteEmptyFolders": False,
        "importExtraFiles": False,
        "extraFileExtensions": "",
        "skipFreeSpaceCheckWhenImporting": True,
        "minimumFreeSpaceWhenImporting": 100,
        "createEmptyMovieFolders": True,
        "renameMovies": False,
        "id": 1,
    }
    if include_auto_unmon:
        cfg["autoUnmonitorDeletedMovies"] = False
    return json.dumps(cfg).encode()


def _radarr_adapter(include_auto_unmon: bool = True) -> RadarrAdapter:
    client = _FakeHttpClient(
        {
            ("GET", "http://radarr:7878/api/v3/config/mediamanagement"): (
                200,
                _radarr_probe_body(include_auto_unmon),
            ),
        }
    )
    return RadarrAdapter(
        base_url="http://radarr:7878",
        api_key="test-key",
        media_root="/media/movies",
        http_client=client,
    )


def test_build_media_management_patch_translates_canonical_to_radarr() -> None:
    policy = ServarrPolicy.from_yaml_text(CANONICAL_YAML)
    adapter = _radarr_adapter()
    patch = policy.build_media_management_patch(adapter)

    assert patch["autoUnmonitorPreviouslyDownloadedMovies"] is True
    assert patch["copyUsingHardlinks"] is True
    assert patch["deleteEmptyFolders"] is True
    assert patch["importExtraFiles"] is True
    assert patch["extraFileExtensions"] == "srt,ass,ssa,vtt,smi,sub"
    assert patch["skipFreeSpaceCheckWhenImporting"] is False
    assert patch["minimumFreeSpaceWhenImporting"] == 500
    assert patch["createEmptyMovieFolders"] is False
    assert patch["autoUnmonitorDeletedMovies"] is False


def test_build_media_management_patch_skips_unsupported_auto_unmonitor() -> None:
    """Radarr 4.x doesn't expose autoUnmonitorDeletedMovies — adapter
    probe reports no support, enforcer must not PUT it."""
    policy = ServarrPolicy.from_yaml_text(CANONICAL_YAML)
    adapter = _radarr_adapter(include_auto_unmon=False)
    patch = policy.build_media_management_patch(adapter)

    assert "autoUnmonitorDeletedMovies" not in patch
    # Other fields still translated:
    assert patch["copyUsingHardlinks"] is True


def test_build_media_management_patch_skips_hardlinks_when_unsupported() -> None:
    policy = ServarrPolicy.from_yaml_text(CANONICAL_YAML)
    adapter = _radarr_adapter()
    # Manually override capabilities to simulate adapter reporting
    # that the mount layout rejects hardlinks.
    object.__setattr__(
        adapter,
        "capabilities",
        AdapterCapabilities(
            supports_auto_unmonitor_deleted=True,
            supports_hardlinks=False,
            probed_field_names=adapter.capabilities.probed_field_names,
        ),
    )
    patch = policy.build_media_management_patch(adapter)
    assert "copyUsingHardlinks" not in patch


def test_build_naming_patch_skips_when_unsupported() -> None:
    policy = ServarrPolicy.from_yaml_text(CANONICAL_YAML)
    adapter = _radarr_adapter()
    object.__setattr__(
        adapter,
        "capabilities",
        AdapterCapabilities(
            supports_auto_unmonitor_deleted=True,
            supports_rename=False,
            probed_field_names=adapter.capabilities.probed_field_names,
        ),
    )
    assert policy.build_naming_patch(adapter) == {}


def test_build_naming_patch_translates_to_radarr() -> None:
    policy = ServarrPolicy.from_yaml_text(CANONICAL_YAML)
    adapter = _radarr_adapter()
    patch = policy.build_naming_patch(adapter)
    assert patch == {"renameMovies": True}


def test_sonarr_field_map_uses_episodes_suffix() -> None:
    """Verify the media-type suffix pattern across *arrs."""
    client = _FakeHttpClient(
        {
            ("GET", "http://sonarr:8989/api/v3/config/mediamanagement"): (
                200,
                b'{"autoUnmonitorPreviouslyDownloadedEpisodes": false, '
                b'"copyUsingHardlinks": false, '
                b'"autoUnmonitorDeletedEpisodes": false, "id": 1}',
            ),
        }
    )
    adapter = SonarrAdapter(
        base_url="http://sonarr:8989",
        api_key="test",
        media_root="/media/tv",
        http_client=client,
    )
    field_map = adapter.media_management_field_map()
    assert (
        field_map["auto_unmonitor_previously_downloaded"]
        == "autoUnmonitorPreviouslyDownloadedEpisodes"
    )
    assert field_map["unmonitor_deleted"] == "autoUnmonitorDeletedEpisodes"
    assert field_map["create_empty_media_folders"] == "createEmptySeriesFolders"
    assert adapter.naming_field_map() == {"rename_files": "renameEpisodes"}


def test_canonical_dict_round_trip() -> None:
    section = MediaManagementSection()
    d = section.as_canonical_dict()
    # Every field on the section appears in the canonical dict
    assert set(d) == {
        "auto_unmonitor_previously_downloaded",
        "use_hardlinks",
        "delete_empty_folders",
        "import_extra_files",
        "extra_file_extensions",
        "skip_free_space_check",
        "minimum_free_space_mb",
        "create_empty_media_folders",
        "unmonitor_deleted",
    }


def test_naming_and_quality_canonical_dicts() -> None:
    assert NamingSection().as_canonical_dict() == {"rename_files": True}
    assert QualitySection().as_canonical_dict() == {
        "cutoff": "WEBDL-1080p",
        "upgrade_allowed": True,
    }
