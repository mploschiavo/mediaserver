#!/usr/bin/env python3
"""Media hygiene and disk guardrail runtime operations."""

from __future__ import annotations

from media_stack.services.disk_guardrails_service import DiskGuardrailsService
from media_stack.services.media_hygiene_ops_service import MediaHygieneOpsService
from media_stack.services.media_hygiene_service import MediaHygieneService
from media_stack.services.runtime_helpers import (
    disk_usage_percent as _disk_usage_percent,
)
from media_stack.services.runtime_helpers import (
    fmt_bytes as _fmt_bytes,
)
from media_stack.services.runtime_helpers import (
    to_float as _to_float,
)
from media_stack.services.runtime_platform import (
    bool_cfg,
    coerce_list,
    log,
    normalize_token,
    normalize_url,
    resolve_app_service_class,
    to_int,
)

from .arr_ops import detect_arr_api_base
from .factory import _arr_queue_cleanup_service
from .qbit_ops import (
    qbit_delete_torrents,
    qbit_list_completed_torrents,
    qbit_list_torrents,
    qbit_login,
    qbit_set_preferences,
)


class ServarrHygieneOps:

    @staticmethod
    def _media_hygiene_ops_service(cfg=None) -> MediaHygieneOpsService:
        service_cls = resolve_app_service_class(
            "media_hygiene_ops_service",
            MediaHygieneOpsService,
        )
        return service_cls(
            log=log,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            to_int=to_int,
            to_float=_to_float,
            normalize_token=normalize_token,
            normalize_url=normalize_url,
            qbit_login=qbit_login,
            qbit_list_completed_torrents=qbit_list_completed_torrents,
            qbit_list_torrents=qbit_list_torrents,
            qbit_delete_torrents=qbit_delete_torrents,
            qbit_set_preferences=qbit_set_preferences,
        )

    @staticmethod
    def _disk_guardrails_service(cfg=None) -> DiskGuardrailsService:
        service_cls = resolve_app_service_class("disk_guardrails_service", DiskGuardrailsService)
        return service_cls(
            log=log,
            bool_cfg=bool_cfg,
            coerce_list=coerce_list,
            to_int=to_int,
            to_float=_to_float,
            normalize_url=normalize_url,
            disk_usage_percent=_disk_usage_percent,
            fmt_bytes=_fmt_bytes,
            qbit_login=qbit_login,
            qbit_list_completed_torrents=qbit_list_completed_torrents,
            qbit_delete_torrents=qbit_delete_torrents,
        )

    @staticmethod
    def _media_hygiene_service(cfg=None) -> MediaHygieneService:
        service_cls = resolve_app_service_class("media_hygiene_service", MediaHygieneService)
        return service_cls(
            log=log,
            bool_cfg=bool_cfg,
            normalize_url=normalize_url,
            detect_arr_api_base=detect_arr_api_base,
            ensure_arr_failed_queue_cleanup=ensure_arr_failed_queue_cleanup,
            run_filesystem_hygiene=run_filesystem_hygiene,
            run_qbit_ipfilter_refresh=run_qbit_ipfilter_refresh,
            run_qbit_queue_guardrails=run_qbit_queue_guardrails,
            run_qbit_duplicate_prune=run_qbit_duplicate_prune,
        )

    def run_qbit_queue_guardrails(self, qbit_cfg, qb_username, qb_password):
        return _media_hygiene_ops_service().run_qbit_queue_guardrails(
            qbit_cfg=qbit_cfg,
            qb_username=qb_username,
            qb_password=qb_password,
        )

    def arr_queue_records(self, payload):
        return _arr_queue_cleanup_service().arr_queue_records(payload)

    def queue_item_is_failed(self, item, failed_tokens):
        return _arr_queue_cleanup_service().queue_item_is_failed(item, failed_tokens)

    def delete_queue_item(self, app_name, app_url, api_base, api_key, item_id, remove_from_client, blocklist):
        return _arr_queue_cleanup_service().delete_queue_item(
            app_name=app_name,
            app_url=app_url,
            api_base=api_base,
            api_key=api_key,
            item_id=item_id,
            remove_from_client=remove_from_client,
            blocklist=blocklist,
        )

    def ensure_arr_failed_queue_cleanup(self, app_cfg, app_url, api_base, api_key, hygiene_cfg):
        return _arr_queue_cleanup_service().ensure_arr_failed_queue_cleanup(
            app_cfg=app_cfg,
            app_url=app_url,
            api_base=api_base,
            api_key=api_key,
            hygiene_cfg=hygiene_cfg,
        )

    @staticmethod
    def _walk_existing_files(paths):
        yield from _media_hygiene_ops_service()._walk_existing_files(paths)

    def run_filesystem_hygiene(self, hygiene_cfg):
        return _media_hygiene_ops_service().run_filesystem_hygiene(hygiene_cfg)

    def run_qbit_duplicate_prune(self, hygiene_cfg, qbit_cfg, qb_username, qb_password):
        return _media_hygiene_ops_service().run_qbit_duplicate_prune(
            hygiene_cfg=hygiene_cfg,
            qbit_cfg=qbit_cfg,
            qb_username=qb_username,
            qb_password=qb_password,
        )

    def run_qbit_ipfilter_refresh(self, hygiene_cfg, qbit_cfg, qb_username, qb_password):
        return _media_hygiene_ops_service().run_qbit_ipfilter_refresh(
            hygiene_cfg=hygiene_cfg,
            qbit_cfg=qbit_cfg,
            qb_username=qb_username,
            qb_password=qb_password,
        )

    def run_media_hygiene(self, 
        cfg,
        config_root,
        arr_apps,
        app_keys,
        qbit_cfg=None,
        qb_username="",
        qb_password="",
    ):
        del config_root  # kept for backward-compatible signature
        return _media_hygiene_service().run(
            cfg=cfg,
            arr_apps=arr_apps,
            app_keys=app_keys,
            qbit_cfg=qbit_cfg,
            qb_username=qb_username,
            qb_password=qb_password,
        )

    def enforce_disk_guardrails(self, cfg, config_root, qbit_cfg, qb_username, qb_password):
        return _disk_guardrails_service().enforce(
            cfg=cfg,
            config_root=config_root,
            qbit_cfg=qbit_cfg,
            qb_username=qb_username,
            qb_password=qb_password,
        )


_instance = ServarrHygieneOps()
run_qbit_queue_guardrails = _instance.run_qbit_queue_guardrails
arr_queue_records = _instance.arr_queue_records
queue_item_is_failed = _instance.queue_item_is_failed
delete_queue_item = _instance.delete_queue_item
ensure_arr_failed_queue_cleanup = _instance.ensure_arr_failed_queue_cleanup
run_filesystem_hygiene = _instance.run_filesystem_hygiene
run_qbit_duplicate_prune = _instance.run_qbit_duplicate_prune
run_qbit_ipfilter_refresh = _instance.run_qbit_ipfilter_refresh
run_media_hygiene = _instance.run_media_hygiene
enforce_disk_guardrails = _instance.enforce_disk_guardrails
