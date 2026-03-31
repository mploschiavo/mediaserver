"""Reconcile Arr indexers against Prowlarr indexer set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

HttpRequestFn = Callable[..., tuple[int, Any, str]]
DetectApiBaseFn = Callable[[str, str, str], str]
LogFn = Callable[[str], None]


@dataclass
class ArrIndexerSyncService:
    http_request: HttpRequestFn
    detect_arr_api_base: DetectApiBaseFn
    log: LogFn

    @staticmethod
    def _prowlarr_indexer_name(entry: dict[str, Any]) -> str:
        return str(entry.get("name") or "").strip()

    @staticmethod
    def _arr_indexer_name(entry: dict[str, Any]) -> str:
        name = str(entry.get("name") or "").strip()
        if name:
            return name
        fields = entry.get("fields") or []
        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                field_name = str(field.get("name") or "").strip().lower()
                if field_name in {"indexername", "name"}:
                    return str(field.get("value") or "").strip()
        return ""

    @staticmethod
    def _is_prowlarr_managed(entry: dict[str, Any], prowlarr_host: str) -> bool:
        impl = str(entry.get("implementation") or "").strip().lower()
        impl_name = str(entry.get("implementationName") or "").strip().lower()
        if "prowlarr" in impl or "prowlarr" in impl_name:
            return True
        fields = entry.get("fields") or []
        if isinstance(fields, list):
            for field in fields:
                if not isinstance(field, dict):
                    continue
                if str(field.get("name") or "").strip().lower() in {"host", "baseurl", "url"}:
                    value = str(field.get("value") or "").strip().lower()
                    if prowlarr_host and prowlarr_host in value:
                        return True
        return False

    def reconcile(
        self,
        *,
        prowlarr_url: str,
        prowlarr_key: str,
        arr_apps: list[dict[str, Any]],
        app_keys: dict[str, str],
        prune_stale: bool = True,
    ) -> dict[str, int]:
        status, payload, body = self.http_request(
            prowlarr_url,
            "/api/v1/indexer",
            api_key=prowlarr_key,
        )
        if status != 200 or not isinstance(payload, list):
            raise RuntimeError(f"Prowlarr: failed reading indexers for sync (HTTP {status}): {body}")

        expected_names = {
            self._prowlarr_indexer_name(item)
            for item in payload
            if isinstance(item, dict) and bool(item.get("enable", True))
        }
        expected_names = {name for name in expected_names if name}

        prowlarr_host = str(prowlarr_url or "").split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        summary = {
            "apps": 0,
            "stale_found": 0,
            "stale_removed": 0,
            "stale_kept": 0,
        }

        for app in arr_apps:
            app_name = str(app.get("name") or app.get("implementation") or "").strip()
            app_url = str(app.get("url") or "").strip().rstrip("/")
            impl = str(app.get("implementation") or "").strip()
            if not app_name or not app_url:
                continue
            api_key = str(
                app_keys.get(app_name)
                or app_keys.get(impl)
                or app_keys.get(impl.lower())
                or ""
            ).strip()
            if not api_key:
                self.log(f"[WARN] {app_name}: missing API key for stale indexer reconciliation")
                continue

            api_base = self.detect_arr_api_base(app_name, app_url, api_key)
            status, arr_indexers, body = self.http_request(
                app_url,
                f"{api_base}/indexer",
                api_key=api_key,
            )
            if status != 200 or not isinstance(arr_indexers, list):
                self.log(
                    f"[WARN] {app_name}: unable to list indexers for reconciliation (HTTP {status}): {body}"
                )
                continue

            summary["apps"] += 1
            for item in arr_indexers:
                if not isinstance(item, dict):
                    continue
                if not self._is_prowlarr_managed(item, prowlarr_host):
                    continue
                name = self._arr_indexer_name(item)
                if not name or name in expected_names:
                    continue
                summary["stale_found"] += 1
                idx_id = item.get("id")
                if not prune_stale:
                    summary["stale_kept"] += 1
                    self.log(f"[WARN] {app_name}: stale Prowlarr-linked indexer kept (dry-run): {name}")
                    continue
                if idx_id in (None, ""):
                    summary["stale_kept"] += 1
                    self.log(f"[WARN] {app_name}: stale indexer '{name}' missing id; cannot delete")
                    continue
                delete_status, _, delete_body = self.http_request(
                    app_url,
                    f"{api_base}/indexer/{idx_id}",
                    api_key=api_key,
                    method="DELETE",
                )
                if delete_status in (200, 202):
                    summary["stale_removed"] += 1
                    self.log(f"[OK] {app_name}: removed stale Prowlarr-linked indexer '{name}'")
                else:
                    summary["stale_kept"] += 1
                    self.log(
                        f"[WARN] {app_name}: failed removing stale indexer '{name}' "
                        f"(HTTP {delete_status}): {delete_body}"
                    )

        self.log(
            "[OK] Arr indexer sync summary: "
            f"apps={summary['apps']}, stale_found={summary['stale_found']}, "
            f"stale_removed={summary['stale_removed']}, stale_kept={summary['stale_kept']}"
        )
        return summary

