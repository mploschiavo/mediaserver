"""Per-technology Servarr adapter strategies for bootstrap extension points."""

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
class AppBootstrapContext:
    cfg: dict[str, Any]
    app_cfg: dict[str, Any]
    app_url: str
    api_base: str
    api_key: str


AdapterHook = Callable[[AdapterDependencies, AppBootstrapContext], None]


def _noop_before_common_steps(_deps: AdapterDependencies, _ctx: AppBootstrapContext) -> None:
    return None


def _readarr_before_common_steps(deps: AdapterDependencies, ctx: AppBootstrapContext) -> None:
    readarr_cfg = ctx.cfg.get("readarr") or {}
    try:
        deps.ensure_readarr_metadata_source(
            ctx.cfg,
            ctx.app_cfg,
            ctx.app_url,
            ctx.api_base,
            ctx.api_key,
        )
    except Exception as exc:
        if deps.bool_cfg(readarr_cfg, "metadata_source_required", False):
            raise
        deps.log(
            f"[WARN] Readarr metadata source: bootstrap skipped ({exc}). "
            "Set readarr.metadata_source_required=true to fail the bootstrap instead."
        )


@dataclass(frozen=True)
class ServarrAdapter:
    implementation: str
    before_common_steps: AdapterHook = _noop_before_common_steps


_ADAPTERS: dict[str, ServarrAdapter] = {
    "sonarr": ServarrAdapter(implementation="sonarr"),
    "radarr": ServarrAdapter(implementation="radarr"),
    "lidarr": ServarrAdapter(implementation="lidarr"),
    "readarr": ServarrAdapter(
        implementation="readarr",
        before_common_steps=_readarr_before_common_steps,
    ),
}


def adapter_for_implementation(implementation: str) -> ServarrAdapter:
    impl = str(implementation or "").strip().lower()
    return _ADAPTERS.get(impl, ServarrAdapter(implementation=impl or "unknown"))
