"""Orchestrate torrent and usenet client bootstrap via per-client adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .download_client_adapters import (
    DownloadClientAdapterContext,
    DownloadClientAdapterDependencies,
    DownloadClientAdapterFactory,
)
from .enums import RunnerOperation

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
InvokeOperationFn = Callable[[RunnerOperation | str, Any], Any]


@dataclass(frozen=True)
class DownloadClientPipelineInputs:
    config_root: str
    arr_apps_raw: list[dict[str, Any]]
    qbit_cfg: dict[str, Any]
    qbit_username: str
    qbit_password: str
    qbit_login_required: bool
    configure_qbit_arr_clients: bool
    set_qbit_categories: bool
    sab_cfg: dict[str, Any]
    configure_sab_arr_clients: bool
    fully_preconfigured: bool
    wait_timeout: int
    adapter_hooks_cfg: dict[str, Any]
    torrent_client_key: str = ""
    usenet_client_key: str = ""


@dataclass(frozen=True)
class DownloadClientPipelineResult:
    qbit_login_ok: bool
    sab_api_key: str


@dataclass
class DownloadClientPipelineService:
    log: LogFn
    normalize_url: NormalizeUrlFn
    wait_for_service: WaitForServiceFn
    bool_cfg: BoolCfgFn
    invoke_operation: InvokeOperationFn

    def _dependencies(self) -> DownloadClientAdapterDependencies:
        return DownloadClientAdapterDependencies(
            log=self.log,
            normalize_url=self.normalize_url,
            wait_for_service=self.wait_for_service,
            bool_cfg=self.bool_cfg,
            invoke_operation=self.invoke_operation,
        )

    def run_prepare(self, inputs: DownloadClientPipelineInputs) -> DownloadClientPipelineResult:
        adapter_factory = DownloadClientAdapterFactory(
            deps=self._dependencies(),
            adapter_class_specs=(inputs.adapter_hooks_cfg or {}).get(
                "download_client_adapter_classes"
            ),
        )

        torrent_key = str(inputs.torrent_client_key or "").strip().lower()
        usenet_key = str(inputs.usenet_client_key or "").strip().lower()
        torrent_status: dict[str, Any] = {}
        if torrent_key:
            torrent_context = DownloadClientAdapterContext(
                key=torrent_key,
                display_name=str(inputs.qbit_cfg.get("name") or torrent_key.title()),
                cfg=inputs.qbit_cfg,
                wait_timeout=inputs.wait_timeout,
                config_root=inputs.config_root,
                arr_apps_raw=inputs.arr_apps_raw,
                fully_preconfigured=inputs.fully_preconfigured,
                configure_arr_clients=inputs.configure_qbit_arr_clients,
                set_categories=inputs.set_qbit_categories,
                login_required=inputs.qbit_login_required,
                username=inputs.qbit_username,
                password=inputs.qbit_password,
            )
            torrent_adapter = adapter_factory.create(torrent_key, torrent_context)
            torrent_adapter.load()
            torrent_adapter.precheck()
            torrent_adapter.prepare()
            torrent_adapter.configure()
            torrent_adapter.ensure()
            torrent_status = torrent_adapter.status_check()

        sab_status: dict[str, Any] = {}
        if usenet_key:
            sab_context = DownloadClientAdapterContext(
                key=usenet_key,
                display_name=str(inputs.sab_cfg.get("name") or usenet_key.title()),
                cfg=inputs.sab_cfg,
                wait_timeout=inputs.wait_timeout,
                config_root=inputs.config_root,
                arr_apps_raw=inputs.arr_apps_raw,
                fully_preconfigured=inputs.fully_preconfigured,
                configure_arr_clients=inputs.configure_sab_arr_clients,
            )
            sab_adapter = adapter_factory.create(usenet_key, sab_context)
            sab_adapter.load()
            sab_adapter.precheck()
            sab_adapter.prepare()
            sab_adapter.configure()
            sab_adapter.ensure()
            sab_status = sab_adapter.status_check()

        return DownloadClientPipelineResult(
            qbit_login_ok=bool(torrent_status.get("login_ok", False)),
            sab_api_key=str(sab_status.get("api_key") or ""),
        )
