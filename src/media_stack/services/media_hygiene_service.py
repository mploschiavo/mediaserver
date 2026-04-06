"""Media hygiene orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
NormalizeUrlFn = Callable[[str], str]
DetectArrApiBaseFn = Callable[[str, str, str], str]
CleanupArrQueueFn = Callable[[dict[str, Any], str, str, str, dict[str, Any]], int]
FilesystemHygieneFn = Callable[[dict[str, Any]], dict[str, Any]]
QbitIpFilterRefreshFn = Callable[[dict[str, Any], dict[str, Any], str, str], dict[str, Any]]
QbitQueueGuardrailsFn = Callable[[dict[str, Any], str, str], dict[str, Any]]
QbitDuplicatePruneFn = Callable[[dict[str, Any], dict[str, Any], str, str], dict[str, Any]]


@dataclass
class MediaHygieneService:
    log: LogFn
    bool_cfg: BoolCfgFn
    normalize_url: NormalizeUrlFn
    detect_arr_api_base: DetectArrApiBaseFn
    ensure_arr_failed_queue_cleanup: CleanupArrQueueFn
    run_filesystem_hygiene: FilesystemHygieneFn
    run_qbit_ipfilter_refresh: QbitIpFilterRefreshFn
    run_qbit_queue_guardrails: QbitQueueGuardrailsFn
    run_qbit_duplicate_prune: QbitDuplicatePruneFn

    def run(
        self,
        cfg: dict[str, Any],
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        qbit_cfg: dict[str, Any] | None = None,
        qb_username: str = "",
        qb_password: str = "",
    ) -> None:
        hygiene_cfg = cfg.get("media_hygiene") or {}
        if not self.bool_cfg(hygiene_cfg, "enabled", False):
            return

        deleted_queue = 0
        app_errors = 0
        if self.bool_cfg(hygiene_cfg, "cleanup_arr_failed_queue", True):
            for app in arr_apps:
                impl = str(app.get("implementation") or "")
                app_url = self.normalize_url(app.get("url") or "")
                if not impl or not app_url:
                    continue
                app_key = app_keys.get(impl)
                if not app_key:
                    continue
                try:
                    api_base = self.detect_arr_api_base(app.get("name") or impl, app_url, app_key)
                    deleted_queue += self.ensure_arr_failed_queue_cleanup(
                        app,
                        app_url,
                        api_base,
                        app_key,
                        hygiene_cfg,
                    )
                except Exception as exc:
                    app_errors += 1
                    self.log(
                        f"[WARN] Media hygiene: queue cleanup skipped for {app.get('name') or impl} "
                        f"({exc})"
                    )

        fs_summary = self.run_filesystem_hygiene(hygiene_cfg)
        qbit_queue_summary: dict[str, Any] = {
            "enabled": False,
            "dry_run": False,
            "total": 0,
            "over_limit_candidates": 0,
            "stale_candidates": 0,
            "over_limit_deleted": 0,
            "stale_deleted": 0,
            "by_category": {},
        }
        qbit_ipfilter_summary: dict[str, Any] = {
            "enabled": False,
            "downloaded": False,
            "applied": False,
            "skipped_reason": "",
            "source_url": "",
            "target_path": "",
            "bytes": 0,
        }
        qbit_summary: dict[str, Any] = {
            "enabled": False,
            "dry_run": False,
            "groups": 0,
            "candidates": 0,
            "deleted": 0,
        }
        qbit_errors = 0

        if self.bool_cfg(hygiene_cfg.get("qbit_ipfilter") or {}, "enabled", False):
            try:
                qbit_ipfilter_summary = self.run_qbit_ipfilter_refresh(
                    hygiene_cfg,
                    qbit_cfg or {},
                    qb_username,
                    qb_password,
                )
            except Exception as exc:
                qbit_errors += 1
                self.log(f"[WARN] Media hygiene: qB IP filter refresh skipped ({exc})")

        if self.bool_cfg((qbit_cfg or {}).get("queue_guardrails") or {}, "enabled", False):
            try:
                qbit_queue_summary = self.run_qbit_queue_guardrails(
                    qbit_cfg or {},
                    qb_username,
                    qb_password,
                )
            except Exception as exc:
                qbit_errors += 1
                self.log(f"[WARN] Media hygiene: qB queue guardrails skipped ({exc})")

        if self.bool_cfg(hygiene_cfg.get("qbit_duplicate_prune") or {}, "enabled", False):
            try:
                qbit_summary = self.run_qbit_duplicate_prune(
                    hygiene_cfg,
                    qbit_cfg or {},
                    qb_username,
                    qb_password,
                )
            except Exception as exc:
                qbit_errors += 1
                self.log(f"[WARN] Media hygiene: qB duplicate prune skipped ({exc})")

        self.log(
            "[OK] Media hygiene: reconcile complete "
            f"(queue_deleted={deleted_queue}, queue_errors={app_errors}, "
            f"qbit_ipfilter={qbit_ipfilter_summary}, "
            f"qbit_queue={qbit_queue_summary}, qbit_dupes={qbit_summary}, "
            f"qbit_errors={qbit_errors}, fs={fs_summary})"
        )
