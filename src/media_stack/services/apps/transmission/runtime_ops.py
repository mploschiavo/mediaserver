#!/usr/bin/env python3
"""Transmission runtime operations bound to generic torrent-client wrappers."""

from __future__ import annotations

from media_stack.services.apps.servarr.runtime.qbit_ops import (
    qbit_create_category,
    qbit_delete_torrents,
    qbit_list_completed_torrents,
    qbit_list_torrents,
    qbit_login,
    qbit_set_preferences,
    setup_qbit_categories,
    setup_qbit_storage_defaults,
    setup_torrent_categories,
    setup_torrent_storage_defaults,
    torrent_client_login,
)

__all__ = [
    "torrent_client_login",
    "qbit_login",
    "qbit_create_category",
    "qbit_set_preferences",
    "qbit_list_torrents",
    "qbit_list_completed_torrents",
    "qbit_delete_torrents",
    "setup_torrent_storage_defaults",
    "setup_qbit_storage_defaults",
    "setup_torrent_categories",
    "setup_qbit_categories",
]
