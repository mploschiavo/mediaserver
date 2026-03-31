"""Prowlarr indexer pipeline orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
EnsureFlareSolverrProxyFn = Callable[[dict[str, Any], str, str, int], None]
EnsureIndexerFn = Callable[[str, str, dict[str, Any]], None]
AutoAddTestedIndexersFn = Callable[[str, str, list[Any], dict[str, Any]], None]
TriggerSyncFn = Callable[[str, str], None]
SyncArrIndexersFn = Callable[[str, str, list[dict[str, Any]], dict[str, str], bool], None]


@dataclass
class ProwlarrIndexerPipelineService:
    log: LogFn
    bool_cfg: BoolCfgFn
    ensure_flaresolverr_proxy: EnsureFlareSolverrProxyFn
    ensure_indexer: EnsureIndexerFn
    auto_add_tested_indexers: AutoAddTestedIndexersFn
    trigger_sync: TriggerSyncFn
    sync_arr_indexers_from_prowlarr: SyncArrIndexersFn

    def _run_optional(
        self,
        *,
        enabled: bool,
        required: bool,
        warning_message: str,
        action: Callable[[], None],
    ) -> None:
        if not enabled:
            return
        try:
            action()
        except Exception as exc:
            if required:
                raise
            self.log(f"{warning_message} ({exc})")

    def run(
        self,
        *,
        cfg: dict[str, Any],
        prowlarr_url: str,
        prowlarr_key: str,
        wait_timeout: int,
        prowlarr_indexers: list[dict[str, Any]],
        auto_indexers: bool,
        trigger_sync: bool,
        arr_apps_raw: list[dict[str, Any]],
        app_keys: dict[str, str],
    ) -> None:
        flaresolverr_cfg = cfg.get("flaresolverr") or {}
        if not isinstance(flaresolverr_cfg, dict):
            flaresolverr_cfg = {}
        self._run_optional(
            enabled=self.bool_cfg(flaresolverr_cfg, "enabled", False),
            required=self.bool_cfg(flaresolverr_cfg, "required", False),
            action=lambda: self.ensure_flaresolverr_proxy(
                cfg,
                prowlarr_url,
                prowlarr_key,
                wait_timeout,
            ),
            warning_message=(
                "[WARN] Prowlarr FlareSolverr proxy: automation skipped. "
                "Set flaresolverr.required=true to fail the bootstrap instead."
            ),
        )

        indexer_failures = 0
        for indexer in prowlarr_indexers:
            idx_name = indexer.get("name") or indexer.get("implementation") or "unnamed-indexer"
            try:
                self.ensure_indexer(prowlarr_url, prowlarr_key, indexer)
            except Exception as exc:
                indexer_failures += 1
                self.log(f"[WARN] Prowlarr: failed indexer '{idx_name}': {exc}")

        if indexer_failures:
            if bool(cfg.get("fail_on_indexer_error", False)):
                raise RuntimeError(
                    f"Prowlarr: {indexer_failures} configured indexer(s) failed and "
                    "fail_on_indexer_error=true."
                )
            self.log(
                f"[WARN] Prowlarr: {indexer_failures} configured indexer(s) failed; "
                "continuing because fail_on_indexer_error is false."
            )

        if auto_indexers:
            self.auto_add_tested_indexers(
                prowlarr_url,
                prowlarr_key,
                cfg.get("prowlarr_auto_indexer_exclude_name_tokens", []),
                cfg.get("prowlarr_indexer_reputation", {}),
            )

        if trigger_sync:
            self.trigger_sync(prowlarr_url, prowlarr_key)
            prune_stale = bool((cfg.get("arr_indexer_sync") or {}).get("prune_stale_indexers", True))
            self.sync_arr_indexers_from_prowlarr(
                prowlarr_url,
                prowlarr_key,
                arr_apps_raw,
                app_keys,
                prune_stale,
            )

