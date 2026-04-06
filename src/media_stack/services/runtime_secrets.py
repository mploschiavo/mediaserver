#!/usr/bin/env python3
"""Shared secret and config-file adapters for runtime operations."""

from __future__ import annotations

from media_stack.services.api_keys_service import ApiKeysService

from .runtime_platform import bool_cfg, coerce_list, log, resolve_path, to_int


def api_keys_service() -> ApiKeysService:
    return ApiKeysService(
        log=log,
        to_int=to_int,
        bool_cfg=bool_cfg,
        coerce_list=coerce_list,
        resolve_path=resolve_path,
    )


def candidate_config_roots(config_root):
    return api_keys_service().candidate_config_roots(config_root)


def read_api_key(config_root, app_name):
    return api_keys_service().read_api_key(config_root, app_name)


def read_json_file(path):
    return api_keys_service().read_json_file(path)
