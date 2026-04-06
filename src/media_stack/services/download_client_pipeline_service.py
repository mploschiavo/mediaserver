"""Orchestrate torrent and usenet client bootstrap via per-client adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .download_client_adapters import (
    DownloadClientAdapterContext,
    DownloadClientAdapterDependencies,
    DownloadClientAdapterFactory,
)

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
WaitForServiceFn = Callable[[str, str, str, int], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
InvokeHandlerFn = Callable[..., Any]


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
    invoke_handler: InvokeHandlerFn | None = None
    invoke_operation: InvokeHandlerFn | None = None

    def _dispatch_invoke(self, event: str, handler: str, *args: Any) -> Any:
        if callable(self.invoke_handler):
            return self.invoke_handler(event, handler, *args)
        if callable(self.invoke_operation):
            return self.invoke_operation(handler, *args)
        raise ValueError(
            "DownloadClientPipelineService requires invoke_handler " "(or legacy invoke_operation)."
        )

    def _dependencies(self) -> DownloadClientAdapterDependencies:
        return DownloadClientAdapterDependencies(
            log=self.log,
            normalize_url=self.normalize_url,
            wait_for_service=self.wait_for_service,
            bool_cfg=self.bool_cfg,
            invoke_handler=self._dispatch_invoke,
        )

    def _run_adapter_pipeline(
        self, adapter_factory: DownloadClientAdapterFactory, key: str, context: DownloadClientAdapterContext,
    ) -> dict[str, Any]:
        """Run the full adapter lifecycle: load → precheck → prepare → configure → ensure → status."""
        adapter = adapter_factory.create(key, context)
        adapter.load()
        adapter.precheck()
        adapter.prepare()
        adapter.configure()
        adapter.ensure()
        return adapter.status_check()

    def run_prepare(self, inputs: DownloadClientPipelineInputs) -> DownloadClientPipelineResult:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        adapter_factory = DownloadClientAdapterFactory(
            deps=self._dependencies(),
            adapter_class_specs=(inputs.adapter_hooks_cfg or {}).get(
                "download_client_adapter_classes"
            ),
        )

        torrent_key = str(inputs.torrent_client_key or "").strip().lower()
        usenet_key = str(inputs.usenet_client_key or "").strip().lower()

        futures: dict[Any, str] = {}
        clients_to_run = []

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
            clients_to_run.append(("torrent", torrent_key, torrent_context))

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
            clients_to_run.append(("usenet", usenet_key, sab_context))

        results: dict[str, dict[str, Any]] = {}

        if len(clients_to_run) > 1:
            self.log("[INFO] Preparing download clients in parallel...")
            with ThreadPoolExecutor(max_workers=len(clients_to_run)) as pool:
                for label, key, ctx in clients_to_run:
                    future = pool.submit(self._run_adapter_pipeline, adapter_factory, key, ctx)
                    futures[future] = label
                for future in as_completed(futures):
                    label = futures[future]
                    results[label] = future.result()
        else:
            for label, key, ctx in clients_to_run:
                results[label] = self._run_adapter_pipeline(adapter_factory, key, ctx)

        torrent_status = results.get("torrent", {})
        sab_status = results.get("usenet", {})

        return DownloadClientPipelineResult(
            qbit_login_ok=bool(torrent_status.get("login_ok", False)),
            sab_api_key=str(sab_status.get("api_key") or ""),
        )
