"""Base Servarr adapter contract and shared lifecycle behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..servarr_adapters import AdapterDependencies, AppBootstrapContext, HookFn
from ..config_models import (
    AppCapabilities,
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)
from ..types import ClientAuth, ServarrRunConfig

LogFn = Callable[[str], None]
NormalizeUrlFn = Callable[[str], str]
DetectArrApiBaseFn = Callable[[str, str, str], str]
EnsureAppAuthFn = Callable[[str, str, str, str, str, dict[str, Any]], None]
EnsureMediaMgmtFn = Callable[
    [ServarrAppConfig | dict[str, Any], str, str, str, ArrMediaManagementPolicy],
    None,
]
EnsureRootFolderFn = Callable[[str, str, str, str, str], None]
EnsureDownloadHandlingFn = Callable[
    [ServarrAppConfig | dict[str, Any] | str, str, str, str, ArrDownloadHandlingPolicy],
    None,
]
EnsureQualityUpgradeFn = Callable[
    [
        dict[str, Any],
        ServarrAppConfig | dict[str, Any],
        str,
        str,
        str,
        ArrQualityUpgradePolicy,
    ],
    None,
]
EnsureProwlarrAppFn = Callable[[str, str, str, str, str, str], None]
EnsureDownloadClientFn = Callable[
    [dict[str, Any], str, str, str, dict[str, Any], dict[str, Any]],
    None,
]
EnsureRemoteMappingsFn = Callable[[dict[str, Any], str, str, str, list[dict[str, Any]]], None]
EnsureDiscoveryListsFn = Callable[[dict[str, Any], dict[str, Any], str, str, str], None]
TriggerDiscoveryFn = Callable[[dict[str, Any], dict[str, Any], str, str, str], None]
TriggerHealthCheckFn = Callable[[str, str, str, str], None]


@dataclass(frozen=True)
class ServarrAdapterDependencies:
    log: LogFn
    normalize_url: NormalizeUrlFn
    detect_arr_api_base: DetectArrApiBaseFn
    ensure_app_auth_settings: EnsureAppAuthFn
    ensure_arr_media_management: EnsureMediaMgmtFn
    ensure_root_folder: EnsureRootFolderFn
    ensure_arr_download_handling: EnsureDownloadHandlingFn
    ensure_arr_quality_upgrade_policy: EnsureQualityUpgradeFn
    ensure_prowlarr_application: EnsureProwlarrAppFn
    ensure_arr_download_client: EnsureDownloadClientFn
    ensure_arr_remote_path_mappings: EnsureRemoteMappingsFn
    ensure_arr_discovery_lists_for_app: EnsureDiscoveryListsFn
    trigger_arr_discovery_kickoff: TriggerDiscoveryFn
    trigger_health_check: TriggerHealthCheckFn


@dataclass
class ServarrAdapterContext:
    cfg: dict[str, Any]
    app_model: ServarrAppConfig
    app_payload: dict[str, Any]
    app_key: str
    app_auth_cfg: dict[str, Any]
    arr_media_management_cfg: ArrMediaManagementPolicy
    arr_download_handling_cfg: ArrDownloadHandlingPolicy
    arr_quality_upgrade_cfg: ArrQualityUpgradePolicy
    qbit_cfg: dict[str, Any]
    qbit_auth: ClientAuth
    sab_cfg: dict[str, Any]
    sab_auth: ClientAuth
    sab_remote_path_mappings: list[dict[str, Any]]
    indexer_manager_url: str
    indexer_manager_key: str
    run_cfg: ServarrRunConfig
    app_url: str = ""
    api_base: str = ""

    @property
    def app_name(self) -> str:
        return str(self.app_model.name or self.app_model.implementation)

    @property
    def app_impl(self) -> str:
        return str(self.app_model.implementation or "")

    @property
    def app_caps(self) -> AppCapabilities:
        return self.app_model.capabilities


@dataclass
class ServarrAdapterBase:
    context: ServarrAdapterContext
    deps: ServarrAdapterDependencies
    adapter_deps: AdapterDependencies
    before_common_hook: HookFn

    def load(self) -> None:
        self.context.app_url = self.deps.normalize_url(self.context.app_model.url or "")
        self.deps.log(f"[STEP] Processing {self.context.app_name} ({self.context.app_impl})")

    @staticmethod
    def _normalize_url_base(value: Any) -> str:
        token = str(value or "").strip()
        if not token or token == "/":
            return ""
        if not token.startswith("/"):
            token = f"/{token}"
        return token.rstrip("/")

    @staticmethod
    def _join_url_base(base_url: str, url_base: str) -> str:
        root = str(base_url or "").rstrip("/")
        base = ServarrAdapterBase._normalize_url_base(url_base)
        if not base:
            return root
        return f"{root}{base}"

    @staticmethod
    def _mapping_lookup(mapping: dict[str, Any], keys: list[str]) -> str:
        if not isinstance(mapping, dict):
            return ""
        key_map = {
            str(raw_key or "").strip().lower(): raw_value for raw_key, raw_value in mapping.items()
        }
        for key in keys:
            token = str(key or "").strip().lower()
            if not token:
                continue
            raw_value = key_map.get(token)
            if raw_value is None:
                continue
            value = ServarrAdapterBase._normalize_url_base(raw_value)
            if value:
                return value
        return ""

    def _configured_url_base_for_keys(self, keys: list[str]) -> str:
        cfg = self.context.cfg if isinstance(self.context.cfg, dict) else {}
        app_auth = cfg.get("app_auth") if isinstance(cfg.get("app_auth"), dict) else {}
        return self._mapping_lookup(
            app_auth.get("path_prefix_url_base_by_app") or {},
            keys,
        ) or self._mapping_lookup(
            app_auth.get("url_base_by_app") or {},
            keys,
        )

    def _configured_url_base(self) -> str:
        return self._configured_url_base_for_keys(
            [
                str(self.context.app_impl or "").strip().lower(),
                str(self.context.app_name or "").strip().lower(),
                str(self.context.app_payload.get("implementation") or "").strip().lower(),
                str(self.context.app_payload.get("name") or "").strip().lower(),
                str(self.context.app_key or "").strip().lower(),
            ]
        )

    def _candidate_path_aware_url(self) -> str:
        configured_base = self._configured_url_base()
        if not configured_base:
            return ""
        root_url = self.deps.normalize_url(self.context.app_model.url or "")
        return self._join_url_base(root_url, configured_base)

    def _promote_path_aware_url_if_ready(self) -> None:
        candidate_url = self._candidate_path_aware_url()
        if not candidate_url or candidate_url == self.context.app_url:
            return
        try:
            candidate_api_base = self.deps.detect_arr_api_base(
                self.context.app_name,
                candidate_url,
                self.context.app_key,
            )
        except Exception:
            return
        self.context.app_url = candidate_url
        self.context.api_base = candidate_api_base
        self.deps.log(
            f"[OK] {self.context.app_name}: using path-aware app URL "
            f"{self.context.app_url} for bootstrap operations"
        )

    def _apply_auth_settings(self) -> None:
        try:
            self.deps.ensure_app_auth_settings(
                self.context.app_name,
                self.context.app_impl,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.app_auth_cfg,
            )
        except Exception as exc:
            if "http 307" in str(exc).lower():
                candidate_url = self._candidate_path_aware_url()
                if candidate_url and candidate_url != self.context.app_url:
                    try:
                        self.deps.ensure_app_auth_settings(
                            self.context.app_name,
                            self.context.app_impl,
                            candidate_url,
                            self.context.api_base,
                            self.context.app_key,
                            self.context.app_auth_cfg,
                        )
                        self.context.app_url = candidate_url
                        self.deps.log(
                            f"[OK] {self.context.app_name}: retried auth bootstrap with "
                            f"path-aware URL {candidate_url}"
                        )
                        return
                    except Exception as retry_exc:
                        exc = retry_exc
            if bool((self.context.app_auth_cfg or {}).get("fail_on_error", False)):
                raise
            self.deps.log(f"[WARN] {self.context.app_name}: auth bootstrap skipped ({exc})")

    def precheck(self) -> None:
        self.context.api_base = self.deps.detect_arr_api_base(
            self.context.app_name,
            self.context.app_url,
            self.context.app_key,
        )

        if self.context.app_caps.supports_auth:
            self._apply_auth_settings()
            self._promote_path_aware_url_if_ready()

        self.before_common_hook(
            self.adapter_deps,
            AppBootstrapContext(
                cfg=self.context.cfg,
                app_cfg=self.context.app_payload,
                app_url=self.context.app_url,
                api_base=self.context.api_base,
                api_key=self.context.app_key,
            ),
        )

    def prepare(self) -> None:
        return

    def _register_prowlarr_application(self) -> None:
        """Register this app with Prowlarr, retrying with path-aware URL on HTTP 307."""
        effective_prowlarr_url = self.context.indexer_manager_url
        try:
            self.deps.ensure_prowlarr_application(
                effective_prowlarr_url,
                self.context.indexer_manager_key,
                self.context.app_name,
                self.context.app_impl,
                self.context.app_url,
                self.context.app_key,
            )
        except Exception as exc:
            if "http 307" not in str(exc).lower():
                raise
            path_aware_prowlarr_url = self._join_url_base(
                self.context.indexer_manager_url,
                self._configured_url_base_for_keys(["prowlarr"]),
            )
            if not path_aware_prowlarr_url or path_aware_prowlarr_url == effective_prowlarr_url:
                raise
            self.deps.ensure_prowlarr_application(
                path_aware_prowlarr_url,
                self.context.indexer_manager_key,
                self.context.app_name,
                self.context.app_impl,
                self.context.app_url,
                self.context.app_key,
            )
            self.deps.log(
                f"[OK] {self.context.app_name}: retried Prowlarr application "
                f"registration with path-aware URL {path_aware_prowlarr_url}"
            )

    def _configure_download_clients(self) -> None:
        run_cfg = self.context.run_cfg
        if (
            run_cfg.configure_qbit_arr_clients
            and run_cfg.qbit_login_ok
            and self.context.app_caps.supports_download_clients
        ):
            self.deps.ensure_arr_download_client(
                self.context.app_payload,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.qbit_cfg,
                {
                    "username": self.context.qbit_auth.username,
                    "password": self.context.qbit_auth.password,
                },
            )

        if run_cfg.configure_sab_arr_clients and self.context.app_caps.supports_download_clients:
            usenet_auth = {
                "username": self.context.sab_auth.username,
                "password": self.context.sab_auth.password,
            }
            if run_cfg.sab_api_key:
                usenet_auth["api_key"] = run_cfg.sab_api_key
            self.deps.ensure_arr_download_client(
                self.context.app_payload,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.sab_cfg,
                usenet_auth,
            )
            if (
                self.context.app_caps.supports_remote_path_mappings
                and self.context.sab_remote_path_mappings
            ):
                self.deps.ensure_arr_remote_path_mappings(
                    self.context.app_payload,
                    self.context.app_url,
                    self.context.api_base,
                    self.context.app_key,
                    self.context.sab_remote_path_mappings,
                )

    def configure(self) -> None:
        run_cfg = self.context.run_cfg

        if (
            run_cfg.configure_arr_media_management
            and self.context.app_caps.supports_media_management
        ):
            self.deps.ensure_arr_media_management(
                self.context.app_model,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.arr_media_management_cfg,
            )

        if self.context.app_caps.supports_root_folder:
            self.deps.ensure_root_folder(
                self.context.app_name,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.app_payload["root_folder"],
            )

        if (
            run_cfg.configure_arr_download_handling
            and self.context.app_caps.supports_download_handling
        ):
            self.deps.ensure_arr_download_handling(
                self.context.app_model,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.arr_download_handling_cfg,
            )

        if run_cfg.configure_arr_quality_upgrade and self.context.app_caps.supports_quality_upgrade:
            self.deps.ensure_arr_quality_upgrade_policy(
                self.context.cfg,
                self.context.app_model,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
                self.context.arr_quality_upgrade_cfg,
            )

        if self.context.app_caps.supports_prowlarr_application:
            self._register_prowlarr_application()

        self._configure_download_clients()

        if run_cfg.configure_arr_discovery_lists and self.context.app_caps.supports_discovery_lists:
            self.deps.ensure_arr_discovery_lists_for_app(
                self.context.cfg,
                self.context.app_payload,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
            )

    def ensure(self) -> None:
        run_cfg = self.context.run_cfg
        if run_cfg.configure_arr_discovery_lists and self.context.app_caps.supports_discovery_lists:
            self.deps.trigger_arr_discovery_kickoff(
                self.context.cfg,
                self.context.app_payload,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
            )

        if run_cfg.refresh_health_after_setup and self.context.app_caps.supports_health_check:
            self.deps.trigger_health_check(
                self.context.app_name,
                self.context.app_url,
                self.context.api_base,
                self.context.app_key,
            )

    def status_check(self) -> dict[str, Any]:
        return {
            "implementation": self.context.app_impl,
            "api_base": self.context.api_base,
        }

    def clean_hygiene(self) -> None:
        return
