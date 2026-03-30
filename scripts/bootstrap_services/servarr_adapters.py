"""Per-technology Servarr adapters for bootstrap extension points."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
LogFn = Callable[[str], None]
EnsureReadarrMetadataFn = Callable[[dict[str, Any], dict[str, Any], str, str, str], None]


@dataclass(frozen=True)
class AdapterDependencies:
    bool_cfg: BoolCfgFn
    log: LogFn
    ensure_readarr_metadata_source: EnsureReadarrMetadataFn


@dataclass(frozen=True)
class ServarrAdapter:
    """Default adapter with no app-specific hook behavior."""

    implementation: str

    def before_common_steps(
        self,
        deps: AdapterDependencies,
        cfg: dict[str, Any],
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
    ) -> None:
        del deps, cfg, app_cfg, app_url, api_base, api_key


@dataclass(frozen=True)
class SonarrAdapter(ServarrAdapter):
    implementation: str = "sonarr"


@dataclass(frozen=True)
class RadarrAdapter(ServarrAdapter):
    implementation: str = "radarr"


@dataclass(frozen=True)
class LidarrAdapter(ServarrAdapter):
    implementation: str = "lidarr"


@dataclass(frozen=True)
class ReadarrAdapter(ServarrAdapter):
    implementation: str = "readarr"

    def before_common_steps(
        self,
        deps: AdapterDependencies,
        cfg: dict[str, Any],
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
    ) -> None:
        readarr_cfg = cfg.get("readarr") or {}
        try:
            deps.ensure_readarr_metadata_source(
                cfg,
                app_cfg,
                app_url,
                api_base,
                api_key,
            )
        except Exception as exc:
            if deps.bool_cfg(readarr_cfg, "metadata_source_required", False):
                raise
            deps.log(
                f"[WARN] Readarr metadata source: bootstrap skipped ({exc}). "
                "Set readarr.metadata_source_required=true to fail the bootstrap instead."
            )


_ADAPTERS: dict[str, ServarrAdapter] = {
    "sonarr": SonarrAdapter(),
    "radarr": RadarrAdapter(),
    "lidarr": LidarrAdapter(),
    "readarr": ReadarrAdapter(),
}


def adapter_for_implementation(implementation: str) -> ServarrAdapter:
    impl = str(implementation or "").strip().lower()
    return _ADAPTERS.get(impl, ServarrAdapter(implementation=impl or "unknown"))
