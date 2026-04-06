"""Media hygiene operation facade extracted from controller."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .media_hygiene_ops import (
    run_filesystem_hygiene,
    run_qbit_duplicate_prune,
    run_qbit_ipfilter_refresh,
    run_qbit_queue_guardrails,
    walk_existing_files,
)

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ToIntFn = Callable[[Any, int | None], int | None]
ToFloatFn = Callable[[Any, float | None], float | None]
NormalizeTokenFn = Callable[[Any], str]
NormalizeUrlFn = Callable[[str], str]
QbitLoginFn = Callable[[str, str, str], Any]
QbitListCompletedFn = Callable[[Any, str], list[dict[str, Any]]]
QbitListTorrentsFn = Callable[[Any, str, str], list[dict[str, Any]]]
QbitDeleteFn = Callable[[Any, str, list[str], bool], None]
QbitSetPreferencesFn = Callable[[Any, str, dict[str, Any]], None]


@dataclass
class MediaHygieneOpsService:
    log: LogFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    to_int: ToIntFn
    to_float: ToFloatFn
    normalize_token: NormalizeTokenFn
    normalize_url: NormalizeUrlFn
    qbit_login: QbitLoginFn
    qbit_list_completed_torrents: QbitListCompletedFn
    qbit_list_torrents: QbitListTorrentsFn
    qbit_delete_torrents: QbitDeleteFn
    qbit_set_preferences: QbitSetPreferencesFn

    def _walk_existing_files(self, paths: list[Path]):
        yield from walk_existing_files(paths)

    def run_filesystem_hygiene(self, hygiene_cfg: dict[str, Any]) -> dict[str, int]:
        return run_filesystem_hygiene(self, hygiene_cfg)

    def run_qbit_duplicate_prune(
        self,
        hygiene_cfg: dict[str, Any],
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
    ) -> dict[str, Any]:
        return run_qbit_duplicate_prune(self, hygiene_cfg, qbit_cfg, qb_username, qb_password)

    def run_qbit_ipfilter_refresh(
        self,
        hygiene_cfg: dict[str, Any],
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
    ) -> dict[str, Any]:
        return run_qbit_ipfilter_refresh(self, hygiene_cfg, qbit_cfg, qb_username, qb_password)

    def run_qbit_queue_guardrails(
        self,
        qbit_cfg: dict[str, Any],
        qb_username: str,
        qb_password: str,
    ) -> dict[str, Any]:
        return run_qbit_queue_guardrails(self, qbit_cfg, qb_username, qb_password)
