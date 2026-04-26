"""Arr failed queue cleanup operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

HttpRequestFn = Callable[..., tuple[int, Any, str]]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ToIntFn = Callable[[Any, int | None], int | None]
NormalizeTokenFn = Callable[[Any], str]
ResolveArrOverridesFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
LogFn = Callable[[str], None]


@dataclass
class ArrQueueCleanupService:
    http_request: HttpRequestFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    to_int: ToIntFn
    normalize_token: NormalizeTokenFn
    resolve_arr_overrides_by_app: ResolveArrOverridesFn
    log: LogFn

    def arr_queue_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in ("records", "Records", "items", "Items"):
            items = payload.get(key)
            if isinstance(items, list):
                return items
        return []

    def queue_item_is_failed(self, item: Any, failed_tokens: list[str]) -> bool:
        if not isinstance(item, dict):
            return False

        hay: list[str] = []
        for key in (
            "status",
            "statusText",
            "trackedDownloadState",
            "trackedDownloadStatus",
            "errorMessage",
            "trackedDownloadError",
            "outputPath",
        ):
            value = item.get(key)
            if value is not None:
                hay.append(str(value))

        messages = item.get("statusMessages")
        if isinstance(messages, list):
            for entry in messages:
                if isinstance(entry, dict):
                    for mk in ("title", "messages", "message"):
                        mv = entry.get(mk)
                        if mv is None:
                            continue
                        if isinstance(mv, list):
                            hay.extend(str(x) for x in mv if x is not None)
                        else:
                            hay.append(str(mv))
                elif entry is not None:
                    hay.append(str(entry))

        text = self.normalize_token(" ".join(hay))
        if not text:
            return False
        return any(token and token in text for token in failed_tokens)

    def delete_queue_item(
        self,
        app_name: str,
        app_url: str,
        api_base: str,
        api_key: str,
        item_id: int,
        remove_from_client: bool,
        blocklist: bool,
    ) -> None:
        query_paths = [
            (
                f"{api_base}/queue/{item_id}?"
                f"removeFromClient={'true' if remove_from_client else 'false'}&"
                f"blocklist={'true' if blocklist else 'false'}&skipRedownload=false"
            ),
            (
                f"{api_base}/queue/{item_id}?"
                f"removeFromClient={'true' if remove_from_client else 'false'}&"
                f"blacklist={'true' if blocklist else 'false'}"
            ),
            f"{api_base}/queue/{item_id}",
        ]
        last_status: int | None = None
        last_body = ""
        for path in query_paths:
            status, _, body = self.http_request(app_url, path, api_key=api_key, method="DELETE")
            last_status = status
            last_body = body
            if status in (200, 202, 204):
                return
            if status == 404:
                return
        raise RuntimeError(
            f"{app_name}: failed deleting queue item id={item_id} (HTTP {last_status}): {last_body}"
        )

    def ensure_arr_failed_queue_cleanup(
        self,
        app_cfg: dict[str, Any],
        app_url: str,
        api_base: str,
        api_key: str,
        hygiene_cfg: dict[str, Any],
    ) -> int:
        app_name = str(app_cfg.get("name") or app_cfg.get("implementation") or "Arr")
        queue_cfg = hygiene_cfg.get("arr_failed_queue_cleanup") or {}
        if not self.bool_cfg(queue_cfg, "enabled", True):
            return 0

        app_overrides = self.resolve_arr_overrides_by_app(queue_cfg, app_cfg)
        if "enabled" in app_overrides and not bool(app_overrides.get("enabled")):
            return 0

        failed_tokens = [
            self.normalize_token(x)
            for x in self.coerce_list(
                app_overrides.get("failed_status_tokens")
                or queue_cfg.get("failed_status_tokens")
                or ["failed", "error", "importfailed", "warning"]
            )
            if self.normalize_token(x)
        ]
        page_size = self.to_int(
            app_overrides.get("page_size"), self.to_int(queue_cfg.get("page_size"), 250)
        )
        if page_size is None or page_size <= 0:
            page_size = 250
        max_delete = self.to_int(
            app_overrides.get("max_delete_per_run"),
            self.to_int(queue_cfg.get("max_delete_per_run"), 50),
        )
        if max_delete is None or max_delete <= 0:
            max_delete = 50

        queue_paths = [
            f"{api_base}/queue?page=1&pageSize={page_size}&sortKey=timeleft&sortDirection=ascending",
            f"{api_base}/queue?page=1&pageSize={page_size}",
            f"{api_base}/queue",
        ]
        payload: Any = None
        last_status: int | None = None
        last_body = ""
        for path in queue_paths:
            status, data, body = self.http_request(app_url, path, api_key=api_key)
            last_status = status
            last_body = body
            if status == 200:
                payload = data
                break
            if status in (404, 405):
                continue
            raise RuntimeError(f"{app_name}: failed reading queue (HTTP {status}): {body}")
        if payload is None:
            self.log(
                f"[WARN] {app_name}: queue endpoint unavailable; skipping failed queue cleanup "
                f"(last_status={last_status}, last_body={last_body})"
            )
            return 0

        records = self.arr_queue_records(payload)
        if not records:
            self.log(f"[OK] {app_name}: queue cleanup found no records.")
            return 0

        to_delete: list[int] = []
        for record in records:
            if self.queue_item_is_failed(record, failed_tokens):
                item_id = self.to_int(record.get("id"), None)
                if item_id is not None:
                    to_delete.append(int(item_id))
        if not to_delete:
            self.log(f"[OK] {app_name}: queue cleanup found no failed records.")
            return 0

        remove_from_client = self.bool_cfg(queue_cfg, "remove_from_client", True)
        blocklist = self.bool_cfg(queue_cfg, "blocklist", True)
        deleted = 0
        for item_id in to_delete[:max_delete]:
            self.delete_queue_item(
                app_name,
                app_url,
                api_base,
                api_key,
                item_id,
                remove_from_client=remove_from_client,
                blocklist=blocklist,
            )
            deleted += 1

        self.log(
            f"[OK] {app_name}: cleaned failed queue items "
            f"(deleted={deleted}, matched={len(to_delete)}, remove_from_client={remove_from_client}, blocklist={blocklist})"
        )
        return deleted
