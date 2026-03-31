"""Base contract for download-client bootstrap adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..enums import RunnerOperation

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
InvokeOperationFn = Callable[[RunnerOperation | str, Any], Any]


@dataclass
class DownloadClientAdapterContext:
    key: str
    display_name: str
    cfg: dict[str, Any]
    wait_timeout: int
    config_root: str
    arr_apps_raw: list[dict[str, Any]]
    fully_preconfigured: bool
    configure_arr_clients: bool = False
    set_categories: bool = False
    login_required: bool = False
    username: str = ""
    password: str = ""
    url: str = ""
    status: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DownloadClientAdapterDependencies:
    log: LogFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    bool_cfg: BoolCfgFn
    invoke_operation: InvokeOperationFn


@dataclass
class DownloadClientAdapterBase:
    context: DownloadClientAdapterContext
    deps: DownloadClientAdapterDependencies

    def is_enabled(self) -> bool:
        return bool(self.context.configure_arr_clients)

    def load(self) -> None:
        if not self.is_enabled():
            return
        self.context.url = self.deps.normalize_url(str(self.context.cfg.get("url", "")))

    def precheck(self) -> None:
        return

    def prepare(self) -> None:
        return

    def configure(self) -> None:
        return

    def ensure(self) -> None:
        return

    def status_check(self) -> dict[str, Any]:
        return dict(self.context.status)
