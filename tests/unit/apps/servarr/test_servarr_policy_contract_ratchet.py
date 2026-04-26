"""Ratchet: the canonical Servarr-policy contract is tamper-evident.

Why a ratchet
-------------
``contracts/servarr-policy.yaml`` is the single source of truth for
anti-dupe behavior across Radarr/Sonarr/Lidarr/Readarr. Every flag
in it corresponds to a specific failure mode we've seen in
production:

- ``auto_unmonitor_previously_downloaded: true``
      prevents re-grab loops (14 duplicate movies on the k8s cluster,
      2026-04-24).
- ``use_hardlinks: true``
      prevents orphan files when qBittorrent deletes its side.
- ``rename_files: true``
      prevents the ``movieFileDeleted`` desync that caused the
      Spider-Man incident, 2026-04-24.
- ``unmonitor_deleted: false``
      prevents "delete bad file → *arr grabs another copy → also
      bad" cycles.
- ``create_empty_media_folders: false``
      keeps the reconciler's "zero releases" check honest.
- ``delete_empty_folders: true``
      lets reconciler distinguish "empty release" from "partial
      release."

If any of these values flip without a policy-review checkbox
getting ticked, an operator has introduced a regression that will
take days to notice but minutes to reopen as a prod incident.

This ratchet pins the full canonical value set. Changing any value
requires editing this test WITH a commit message explaining which
failure mode the change addresses. That's the tamper-evidence:
the review attention follows the value change.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path

import yaml

from media_stack.services.media_integrity.policy import ServarrPolicy


CONTRACT_PATH = (
    Path(__file__).resolve().parents[4] / "contracts" / "servarr-policy.yaml"
)


# Pin every canonical value. If you change ANY line below, include
# in the commit message which prod failure mode the change addresses.
EXPECTED_POLICY = {
    "version": 1,
    "media_management": {
        "auto_unmonitor_previously_downloaded": True,
        "use_hardlinks": True,
        "delete_empty_folders": True,
        "import_extra_files": True,
        "extra_file_extensions": "srt,ass,ssa,vtt,smi,sub",
        "skip_free_space_check": False,
        "minimum_free_space_mb": 500,
        "create_empty_media_folders": False,
        "unmonitor_deleted": False,
    },
    "naming": {"rename_files": True},
    "quality": {"cutoff": "WEBDL-1080p", "upgrade_allowed": True},
    "bazarr": {
        "rename_files": True,
        "auto_sync": True,
        "upgrade_allowed": True,
        "ignore_deleted": True,
    },
}


class ServarrPolicyContractRatchet(unittest.TestCase):

    def test_contract_file_exists(self) -> None:
        self.assertTrue(
            CONTRACT_PATH.is_file(),
            f"servarr-policy contract missing at {CONTRACT_PATH}; the "
            "media-integrity subsystem refuses to boot without it",
        )

    def test_contract_yaml_parses(self) -> None:
        raw = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertIsInstance(raw, dict)

    def test_contract_values_are_pinned(self) -> None:
        """Every canonical key pinned to its expected value.

        If a test change here is unrelated to a prod incident or a
        deliberate policy shift, revert it — the ratchet exists to
        make accidental flips visible in review.
        """
        raw = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            raw,
            EXPECTED_POLICY,
            "\n\n---\nThe Servarr policy contract has changed. This is a "
            "tamper-evident file — every value corresponds to a specific "
            "duplicate-download failure mode. Before updating this test:\n"
            "  1. Name the failure mode you're addressing.\n"
            "  2. Add a comment in servarr-policy.yaml explaining the "
            "change.\n"
            "  3. Update docs/roadmap/session-visibility-followups.md if "
            "the change affects the duplicate-download narrative.\n"
            "---\n",
        )

    def test_contract_loads_into_policy_dataclass(self) -> None:
        """The dataclass loader can round-trip the contract without
        raising — guards against schema/loader drift."""
        policy = ServarrPolicy.from_path(CONTRACT_PATH)
        self.assertEqual(policy.version, 1)
        self.assertTrue(policy.media_management.auto_unmonitor_previously_downloaded)
        self.assertTrue(policy.media_management.use_hardlinks)
        self.assertFalse(policy.media_management.unmonitor_deleted)
        self.assertTrue(policy.naming.rename_files)
        self.assertEqual(policy.quality.cutoff, "WEBDL-1080p")

    def test_contract_has_required_top_level_sections(self) -> None:
        raw = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertIn("version", raw)
        self.assertIn("media_management", raw)
        self.assertIn("naming", raw)
        self.assertIn("quality", raw)
        self.assertIn("bazarr", raw)

    def test_bazarr_section_loads(self) -> None:
        """Bazarr section must round-trip through the dataclass
        loader — same tamper-evidence contract as Servarr."""
        policy = ServarrPolicy.from_path(CONTRACT_PATH)
        self.assertTrue(policy.bazarr.rename_files)
        self.assertTrue(policy.bazarr.auto_sync)
        self.assertTrue(policy.bazarr.upgrade_allowed)
        self.assertTrue(policy.bazarr.ignore_deleted)


class ServarrAdapterFieldMapRatchet(unittest.TestCase):
    """Ratchet: the per-adapter field maps must cover every canonical
    key the contract exposes.

    Why: the enforcer SILENTLY drops canonical keys a particular
    adapter doesn't know about. If a new canonical key is added to
    the contract but not wired into, say, Lidarr's field map, the
    Lidarr cluster will keep duplicating and nobody will notice
    until an operator audits live config.

    This catches that at CI time instead of at 3am.
    """

    CANONICAL_MEDIA_MGMT_KEYS = {
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

    CANONICAL_NAMING_KEYS = {"rename_files"}

    def test_every_adapter_maps_every_media_management_key(self) -> None:
        from media_stack.services.media_integrity.adapters import (
            LidarrAdapter,
            RadarrAdapter,
            ReadarrAdapter,
            SonarrAdapter,
        )
        for adapter_cls in (RadarrAdapter, SonarrAdapter, LidarrAdapter, ReadarrAdapter):
            missing = self.CANONICAL_MEDIA_MGMT_KEYS - set(
                adapter_cls._MEDIA_MANAGEMENT_FIELDS
            )
            self.assertFalse(
                missing,
                f"{adapter_cls.__name__} missing field-map entries for "
                f"canonical keys: {missing}. Add the per-app field "
                "name (verified against a running instance) before "
                "merging.",
            )

    def test_every_adapter_maps_every_naming_key(self) -> None:
        from media_stack.services.media_integrity.adapters import (
            LidarrAdapter,
            RadarrAdapter,
            ReadarrAdapter,
            SonarrAdapter,
        )
        for adapter_cls in (RadarrAdapter, SonarrAdapter, LidarrAdapter, ReadarrAdapter):
            missing = self.CANONICAL_NAMING_KEYS - set(adapter_cls._NAMING_FIELDS)
            self.assertFalse(
                missing,
                f"{adapter_cls.__name__} missing naming-field-map entries "
                f"for: {missing}",
            )

    def test_media_type_suffix_pattern(self) -> None:
        """Pattern ratchet: Servarr apps use the media-type suffix
        convention (Movies/Episodes/Tracks/Books) for the
        ``autoUnmonitor*`` + ``create*Folders`` + ``rename*`` fields.
        Locking this here means an accidental rename (e.g., a copy-
        paste fumble from radarr_adapter to sonarr_adapter) fails CI."""
        from media_stack.services.media_integrity.adapters import (
            LidarrAdapter,
            RadarrAdapter,
            ReadarrAdapter,
            SonarrAdapter,
        )
        cases = [
            (RadarrAdapter, "Movies", "Movie"),
            (SonarrAdapter, "Episodes", "Series"),
            (LidarrAdapter, "Tracks", "Artist"),
            (ReadarrAdapter, "Books", "Author"),
        ]
        for adapter_cls, file_suffix, folder_suffix in cases:
            self.assertTrue(
                adapter_cls._MEDIA_MANAGEMENT_FIELDS[
                    "auto_unmonitor_previously_downloaded"
                ].endswith(file_suffix),
                f"{adapter_cls.__name__}: auto_unmonitor_previously_"
                f"downloaded should end with {file_suffix}",
            )
            self.assertTrue(
                adapter_cls._MEDIA_MANAGEMENT_FIELDS["unmonitor_deleted"].endswith(
                    file_suffix
                ),
                f"{adapter_cls.__name__}: unmonitor_deleted should end "
                f"with {file_suffix}",
            )
            self.assertTrue(
                adapter_cls._MEDIA_MANAGEMENT_FIELDS[
                    "create_empty_media_folders"
                ].endswith(f"{folder_suffix}Folders"),
                f"{adapter_cls.__name__}: create_empty_media_folders "
                f"should end with {folder_suffix}Folders",
            )
            self.assertTrue(
                adapter_cls._NAMING_FIELDS["rename_files"].endswith(file_suffix),
                f"{adapter_cls.__name__}: rename_files should end with "
                f"{file_suffix}",
            )


if __name__ == "__main__":
    unittest.main()
