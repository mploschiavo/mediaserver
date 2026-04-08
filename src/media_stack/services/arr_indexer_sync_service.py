"""Backward-compat shim — canonical location is services.apps.prowlarr.indexer_sync_service."""

from media_stack.services.apps.prowlarr.indexer_sync_service import (  # noqa: F401
    ArrIndexerSyncService,
    DetectApiBaseFn,
    HttpRequestFn,
    LogFn,
)

__all__ = [
    "ArrIndexerSyncService",
    "DetectApiBaseFn",
    "HttpRequestFn",
    "LogFn",
]
