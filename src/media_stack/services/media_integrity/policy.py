"""Canonical Servarr policy — YAML loader + enforcement helpers.

The policy file (``contracts/servarr-policy.yaml``) is the single
source of truth for media-management behavior across Radarr, Sonarr,
Lidarr, and Readarr. This module loads it into frozen dataclasses
and provides the translation helpers the enforcer uses to build
per-app PUT payloads.

Design invariants
-----------------

- **Canonical keys are adapter-agnostic.** The policy says
  ``auto_unmonitor_previously_downloaded: true``, not
  ``autoUnmonitorPreviouslyDownloadedMovies: true``. Per-app field
  name translation lives on the adapter.
- **Unknown canonical keys are ignored, not errors.** This lets us
  add new knobs without breaking older adapters that don't know
  about them. Adapters that DO know about a key but can't apply it
  (older firmware) report via ``AdapterCapabilities`` and the
  enforcer skips cleanly.
- **Values are validated at load time, not at apply time.** A
  malformed YAML never reaches an adapter's PUT call.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from .arr_protocol import ArrApp


# Resolve policy contract path: repo-relative first, then container
# mount, then the install-root path the wheel-based image uses.
# The factory bails with FileNotFoundError if none exist, leaving the
# whole media-integrity subsystem disabled at boot — so this list is
# load-bearing.
_CONTRACT_PATH_REPO = (
    Path(__file__).resolve().parents[4] / "contracts" / "servarr-policy.yaml"
)
# Install-root used by ``deploy/compose/controller.Dockerfile`` (the
# wheel-based image). v1.0.230 cluster has the file here, but the
# loader was hard-coded to ``/contracts/...`` and silently disabled
# the whole subsystem ("media-integrity service not configured" /
# "No adapters configured" in the dashboard) — fixed by adding this
# candidate above the legacy bind-mount path.
_CONTRACT_PATH_INSTALL = Path("/opt/media-stack/contracts/servarr-policy.yaml")
_CONTRACT_PATH_CONTAINER = Path("/contracts/servarr-policy.yaml")

_CONTRACT_PATH_CANDIDATES = (
    _CONTRACT_PATH_REPO,
    _CONTRACT_PATH_INSTALL,
    _CONTRACT_PATH_CONTAINER,
)


def _default_contract_path() -> Path:
    """Pick the policy path at call time so tests can monkeypatch."""
    for candidate in _CONTRACT_PATH_CANDIDATES:
        if candidate.exists():
            return candidate
    # No file on disk — return the last (legacy) candidate so the
    # caller's FileNotFoundError surfaces with a familiar path string.
    return _CONTRACT_PATH_CONTAINER


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MediaManagementSection:
    """``/config/mediamanagement`` knobs (canonical form)."""

    auto_unmonitor_previously_downloaded: bool = True
    use_hardlinks: bool = True
    delete_empty_folders: bool = True
    import_extra_files: bool = True
    extra_file_extensions: str = "srt,ass,ssa,vtt,smi,sub"
    skip_free_space_check: bool = False
    minimum_free_space_mb: int = 500
    create_empty_media_folders: bool = False
    unmonitor_deleted: bool = False

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "auto_unmonitor_previously_downloaded": self.auto_unmonitor_previously_downloaded,
            "use_hardlinks": self.use_hardlinks,
            "delete_empty_folders": self.delete_empty_folders,
            "import_extra_files": self.import_extra_files,
            "extra_file_extensions": self.extra_file_extensions,
            "skip_free_space_check": self.skip_free_space_check,
            "minimum_free_space_mb": self.minimum_free_space_mb,
            "create_empty_media_folders": self.create_empty_media_folders,
            "unmonitor_deleted": self.unmonitor_deleted,
        }


@dataclass(frozen=True)
class NamingSection:
    """``/config/naming`` knobs (canonical form)."""

    rename_files: bool = True

    def as_canonical_dict(self) -> dict[str, Any]:
        return {"rename_files": self.rename_files}


@dataclass(frozen=True)
class QualitySection:
    """Quality-profile policy. Applied per-profile by the enforcer."""

    cutoff: str = "WEBDL-1080p"
    upgrade_allowed: bool = True

    def as_canonical_dict(self) -> dict[str, Any]:
        return {"cutoff": self.cutoff, "upgrade_allowed": self.upgrade_allowed}


@dataclass(frozen=True)
class BazarrSection:
    """Bazarr (subtitle) policy — mirrors media_management in spirit.

    Canonical keys (see ``contracts/servarr-policy.yaml`` comments
    for the prod incident each key prevents):
    - ``rename_files``     — subs follow canonical video name
    - ``auto_sync``        — sync sub download to video import
    - ``upgrade_allowed``  — cap re-downloads once a good sub exists
    - ``ignore_deleted``   — don't re-download user-removed subs
    """

    rename_files: bool = True
    auto_sync: bool = True
    upgrade_allowed: bool = True
    ignore_deleted: bool = True

    def as_canonical_dict(self) -> dict[str, Any]:
        return {
            "rename_files": self.rename_files,
            "auto_sync": self.auto_sync,
            "upgrade_allowed": self.upgrade_allowed,
            "ignore_deleted": self.ignore_deleted,
        }


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServarrPolicy:
    """Full canonical policy loaded from YAML."""

    version: int = 1
    media_management: MediaManagementSection = field(default_factory=MediaManagementSection)
    naming: NamingSection = field(default_factory=NamingSection)
    quality: QualitySection = field(default_factory=QualitySection)
    bazarr: BazarrSection = field(default_factory=BazarrSection)

    # -- loaders --------------------------------------------------------

    @classmethod
    def from_yaml_text(cls, text: str) -> "ServarrPolicy":
        """Parse policy from YAML text. Unknown keys are ignored."""
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict):
            raise ValueError("servarr-policy: top-level must be a mapping")
        version = int(raw.get("version", 1))
        mm_raw = raw.get("media_management") or {}
        naming_raw = raw.get("naming") or {}
        quality_raw = raw.get("quality") or {}
        bazarr_raw = raw.get("bazarr") or {}
        if not isinstance(mm_raw, dict):
            raise ValueError("servarr-policy: media_management must be a mapping")
        if not isinstance(naming_raw, dict):
            raise ValueError("servarr-policy: naming must be a mapping")
        if not isinstance(quality_raw, dict):
            raise ValueError("servarr-policy: quality must be a mapping")
        if not isinstance(bazarr_raw, dict):
            raise ValueError("servarr-policy: bazarr must be a mapping")
        return cls(
            version=version,
            media_management=_media_management_from_dict(mm_raw),
            naming=_naming_from_dict(naming_raw),
            quality=_quality_from_dict(quality_raw),
            bazarr=_bazarr_from_dict(bazarr_raw),
        )

    @classmethod
    def from_path(cls, path: Path) -> "ServarrPolicy":
        return cls.from_yaml_text(path.read_text(encoding="utf-8"))

    @classmethod
    def load_default(cls) -> "ServarrPolicy":
        """Load the policy from the canonical contract path.

        Raises if the file is missing — the stack refuses to boot
        without a policy (fail-closed on config invariants).
        """
        path = _default_contract_path()
        if not path.exists():
            raise FileNotFoundError(
                f"servarr-policy contract not found at {path}; "
                "the media-integrity subsystem refuses to boot without one"
            )
        return cls.from_path(path)

    # -- patch builders (consumed by enforcer in turn 2) ----------------

    def build_media_management_patch(self, adapter: ArrApp) -> dict[str, Any]:
        """Translate canonical media-management keys → adapter's fields.

        Canonical keys the adapter does not expose (missing from
        ``adapter.media_management_field_map()`` OR disabled via
        ``adapter.capabilities``) are dropped from the patch. The
        enforcer then merges this patch into the current config and
        PUTs — full-document replacement is how Servarr config
        endpoints work.
        """
        field_map = adapter.media_management_field_map()
        caps = adapter.capabilities
        canonical = self.media_management.as_canonical_dict()
        patch: dict[str, Any] = {}
        for canonical_key, value in canonical.items():
            app_key = field_map.get(canonical_key)
            if not app_key:
                continue
            if canonical_key == "unmonitor_deleted" and not caps.supports_auto_unmonitor_deleted:
                continue
            if canonical_key == "use_hardlinks" and not caps.supports_hardlinks:
                continue
            patch[app_key] = value
        return patch

    def build_naming_patch(self, adapter: ArrApp) -> dict[str, Any]:
        """Translate canonical naming keys → adapter's fields."""
        field_map = adapter.naming_field_map()
        caps = adapter.capabilities
        if not caps.supports_rename:
            return {}
        canonical = self.naming.as_canonical_dict()
        patch: dict[str, Any] = {}
        for canonical_key, value in canonical.items():
            app_key = field_map.get(canonical_key)
            if not app_key:
                continue
            patch[app_key] = value
        return patch

    def build_bazarr_settings_patch(self, adapter: Any) -> dict[str, Any]:
        """Translate canonical Bazarr keys → adapter's nested settings
        paths. Returns a dotted-path → value mapping; the enforcer
        walks the paths to merge into the current settings blob.

        The adapter is a ``BazarrApp`` (duck-typed for cycle reasons;
        importing the protocol here would create an import cycle
        with adapters importing policy)."""
        caps = adapter.capabilities
        field_map = adapter.settings_field_map()
        canonical = self.bazarr.as_canonical_dict()
        patch: dict[str, Any] = {}
        for canonical_key, value in canonical.items():
            app_path = field_map.get(canonical_key)
            if not app_path:
                continue
            if canonical_key == "rename_files" and not caps.supports_rename:
                continue
            if canonical_key == "auto_sync" and not caps.supports_auto_sync:
                continue
            if canonical_key == "upgrade_allowed" and not caps.supports_upgrade:
                continue
            if canonical_key == "ignore_deleted" and not caps.supports_ignore_deleted:
                continue
            patch[app_path] = value
        return patch

    def with_overrides(
        self,
        *,
        media_management: MediaManagementSection | None = None,
        naming: NamingSection | None = None,
        quality: QualitySection | None = None,
        bazarr: BazarrSection | None = None,
    ) -> "ServarrPolicy":
        """Return a new policy with the given sections replaced. Used
        by tests and by operators who want to deviate on a single
        knob without editing the YAML."""
        return replace(
            self,
            media_management=media_management or self.media_management,
            naming=naming or self.naming,
            quality=quality or self.quality,
            bazarr=bazarr or self.bazarr,
        )


# ---------------------------------------------------------------------------
# Section parsers (typed coercion — YAML gives us Any, dataclass wants bool/int/str)
# ---------------------------------------------------------------------------


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    raise ValueError(f"expected bool, got {value!r}")


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        # bool is a subclass of int — explicit reject so
        # ``minimum_free_space_mb: true`` isn't silently 1.
        raise ValueError(f"expected int, got bool {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise ValueError(f"expected int, got {value!r}")


def _as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    raise ValueError(f"expected str, got {value!r}")


def _media_management_from_dict(raw: dict[str, Any]) -> MediaManagementSection:
    defaults = MediaManagementSection()
    return MediaManagementSection(
        auto_unmonitor_previously_downloaded=_as_bool(
            raw.get("auto_unmonitor_previously_downloaded"),
            defaults.auto_unmonitor_previously_downloaded,
        ),
        use_hardlinks=_as_bool(raw.get("use_hardlinks"), defaults.use_hardlinks),
        delete_empty_folders=_as_bool(
            raw.get("delete_empty_folders"), defaults.delete_empty_folders
        ),
        import_extra_files=_as_bool(
            raw.get("import_extra_files"), defaults.import_extra_files
        ),
        extra_file_extensions=_as_str(
            raw.get("extra_file_extensions"), defaults.extra_file_extensions
        ),
        skip_free_space_check=_as_bool(
            raw.get("skip_free_space_check"), defaults.skip_free_space_check
        ),
        minimum_free_space_mb=_as_int(
            raw.get("minimum_free_space_mb"), defaults.minimum_free_space_mb
        ),
        create_empty_media_folders=_as_bool(
            raw.get("create_empty_media_folders"), defaults.create_empty_media_folders
        ),
        unmonitor_deleted=_as_bool(
            raw.get("unmonitor_deleted"), defaults.unmonitor_deleted
        ),
    )


def _naming_from_dict(raw: dict[str, Any]) -> NamingSection:
    defaults = NamingSection()
    return NamingSection(
        rename_files=_as_bool(raw.get("rename_files"), defaults.rename_files),
    )


def _quality_from_dict(raw: dict[str, Any]) -> QualitySection:
    defaults = QualitySection()
    return QualitySection(
        cutoff=_as_str(raw.get("cutoff"), defaults.cutoff),
        upgrade_allowed=_as_bool(raw.get("upgrade_allowed"), defaults.upgrade_allowed),
    )


def _bazarr_from_dict(raw: dict[str, Any]) -> BazarrSection:
    defaults = BazarrSection()
    return BazarrSection(
        rename_files=_as_bool(raw.get("rename_files"), defaults.rename_files),
        auto_sync=_as_bool(raw.get("auto_sync"), defaults.auto_sync),
        upgrade_allowed=_as_bool(raw.get("upgrade_allowed"), defaults.upgrade_allowed),
        ignore_deleted=_as_bool(raw.get("ignore_deleted"), defaults.ignore_deleted),
    )


__all__ = [
    "BazarrSection",
    "MediaManagementSection",
    "NamingSection",
    "QualitySection",
    "ServarrPolicy",
]
