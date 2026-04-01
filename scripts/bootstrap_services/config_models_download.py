"""Typed models for download clients and core technology bindings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config_model_utils import to_int


@dataclass(frozen=True)
class QbitQueueGuardrailsConfig:
    enabled: bool
    dry_run: bool
    default_max_queued: int | None
    max_queued_by_category: dict[str, int] = field(default_factory=dict)
    max_total_size_gib_by_category: dict[str, float] = field(default_factory=dict)
    max_weight_percent_by_category: dict[str, float] = field(default_factory=dict)
    over_limit_max_delete_per_category: int = 15
    over_budget_max_delete_per_category: int = 20
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "QbitQueueGuardrailsConfig":
        src = dict(data or {})

        def _int_map(value: Any) -> dict[str, int]:
            out: dict[str, int] = {}
            if not isinstance(value, dict):
                return out
            for key, raw in value.items():
                token = str(key or "").strip().lower()
                if not token:
                    continue
                parsed = to_int(raw)
                if parsed is None or parsed < 0:
                    continue
                out[token] = int(parsed)
            return out

        def _float_map(value: Any) -> dict[str, float]:
            out: dict[str, float] = {}
            if not isinstance(value, dict):
                return out
            for key, raw in value.items():
                token = str(key or "").strip().lower()
                if not token:
                    continue
                try:
                    parsed = float(raw)
                except (TypeError, ValueError):
                    continue
                if parsed < 0:
                    continue
                out[token] = parsed
            return out

        return cls(
            enabled=bool(src.get("enabled", False)),
            dry_run=bool(src.get("dry_run", False)),
            default_max_queued=to_int(src.get("default_max_queued")),
            max_queued_by_category=_int_map(src.get("max_queued_by_category")),
            max_total_size_gib_by_category=_float_map(src.get("max_total_size_gib_by_category")),
            max_weight_percent_by_category=_float_map(src.get("max_weight_percent_by_category")),
            over_limit_max_delete_per_category=to_int(
                src.get("over_limit_max_delete_per_category"), 15
            )
            or 15,
            over_budget_max_delete_per_category=to_int(
                src.get("over_budget_max_delete_per_category"), 20
            )
            or 20,
            raw=src,
        )


@dataclass(frozen=True)
class QbitAuthBypassConfig:
    localhost: bool
    whitelist_enabled: bool
    whitelist_subnets: list[str] = field(default_factory=list)
    allow_open_world: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "QbitAuthBypassConfig":
        src = dict(data or {})
        whitelist_subnets = [
            str(x).strip() for x in (src.get("whitelist_subnets") or []) if str(x).strip()
        ]
        if not whitelist_subnets:
            whitelist_subnets = [
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "127.0.0.1/32",
                "::1/128",
            ]
        return cls(
            localhost=bool(src.get("localhost", True)),
            whitelist_enabled=bool(src.get("whitelist_enabled", True)),
            whitelist_subnets=whitelist_subnets,
            allow_open_world=bool(src.get("allow_open_world", False)),
            raw=src,
        )


@dataclass(frozen=True)
class QbitSeedingPolicyConfig:
    enabled: bool
    max_ratio: float | None = None
    max_ratio_enabled: bool = False
    max_seeding_time_minutes: int | None = None
    max_seeding_time_enabled: bool = False
    remove_on_limit_reached: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "QbitSeedingPolicyConfig":
        src = dict(data or {})
        max_ratio_val: float | None = None
        try:
            raw_ratio = src.get("max_ratio")
            if raw_ratio is not None and str(raw_ratio).strip() != "":
                max_ratio_val = float(raw_ratio)
        except (TypeError, ValueError):
            max_ratio_val = None
        max_seeding_minutes = to_int(src.get("max_seeding_time_minutes"))
        return cls(
            enabled=bool(src.get("enabled", False)),
            max_ratio=max_ratio_val,
            max_ratio_enabled=bool(src.get("max_ratio_enabled", False)),
            max_seeding_time_minutes=max_seeding_minutes,
            max_seeding_time_enabled=bool(src.get("max_seeding_time_enabled", False)),
            remove_on_limit_reached=bool(src.get("remove_on_limit_reached", False)),
            raw=src,
        )


@dataclass(frozen=True)
class DiskGuardrailsConfig:
    enabled: bool
    required: bool
    monitor_path: str
    max_used_percent: float
    target_used_percent: float
    qbit_cleanup: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DiskGuardrailsConfig":
        src = dict(data or {})
        try:
            max_used = float(src.get("max_used_percent", 65))
        except (TypeError, ValueError):
            max_used = 65.0
        try:
            target_used = float(src.get("target_used_percent", 58))
        except (TypeError, ValueError):
            target_used = 58.0
        return cls(
            enabled=bool(src.get("enabled", False)),
            required=bool(src.get("required", False)),
            monitor_path=str(src.get("monitor_path", "")).strip(),
            max_used_percent=max_used,
            target_used_percent=target_used,
            qbit_cleanup=dict(src.get("qbit_cleanup") or {}),
            raw=src,
        )


@dataclass(frozen=True)
class DownloadClientConfig:
    url: str
    host: str
    port: int | None
    implementation: str
    name: str
    use_ssl: bool = False
    url_base: str = ""
    priority: int = 1
    username_env: str = ""
    password_env: str = ""
    api_key_env: str = ""
    api_key_required: bool = False
    categories: dict[str, str] = field(default_factory=dict)
    completed_paths: dict[str, str] = field(default_factory=dict)
    default_save_path: str = "/data/torrents/completed"
    temp_path: str = "/data/torrents/incomplete"
    temp_path_enabled: bool = True
    auto_tmm_enabled: bool = True
    auth_bypass: dict[str, Any] = field(default_factory=dict)
    seeding_policy: dict[str, Any] = field(default_factory=dict)
    queue_guardrails: dict[str, Any] = field(default_factory=dict)
    auth_bypass_typed: QbitAuthBypassConfig = field(
        default_factory=lambda: QbitAuthBypassConfig.from_dict(None)
    )
    seeding_policy_typed: QbitSeedingPolicyConfig = field(
        default_factory=lambda: QbitSeedingPolicyConfig.from_dict(None)
    )
    queue_guardrails_typed: QbitQueueGuardrailsConfig = field(
        default_factory=lambda: QbitQueueGuardrailsConfig.from_dict(None)
    )
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DownloadClientConfig":
        src = dict(data or {})
        auth_bypass_typed = QbitAuthBypassConfig.from_dict(src.get("auth_bypass") or {})
        seeding_policy_typed = QbitSeedingPolicyConfig.from_dict(src.get("seeding_policy") or {})
        queue_guardrails_typed = QbitQueueGuardrailsConfig.from_dict(
            src.get("queue_guardrails") or {}
        )
        return cls(
            url=str(src.get("url", "")).strip(),
            host=str(src.get("host", "")).strip(),
            port=to_int(src.get("port")),
            implementation=str(src.get("implementation", "")).strip(),
            name=str(src.get("name", "")).strip(),
            use_ssl=bool(src.get("use_ssl", False)),
            url_base=str(src.get("url_base", "")).strip(),
            priority=to_int(src.get("priority"), 1) or 1,
            username_env=str(src.get("username_env", "")).strip(),
            password_env=str(src.get("password_env", "")).strip(),
            api_key_env=str(src.get("api_key_env", "")).strip(),
            api_key_required=bool(src.get("api_key_required", False)),
            categories=dict(src.get("categories") or {}),
            completed_paths=dict(src.get("completed_paths") or {}),
            default_save_path=str(src.get("default_save_path", "/data/torrents/completed")),
            temp_path=str(src.get("temp_path", "/data/torrents/incomplete")),
            temp_path_enabled=bool(src.get("temp_path_enabled", True)),
            auto_tmm_enabled=bool(src.get("auto_tmm_enabled", True)),
            auth_bypass=dict(src.get("auth_bypass") or {}),
            seeding_policy=dict(src.get("seeding_policy") or {}),
            queue_guardrails=dict(src.get("queue_guardrails") or {}),
            auth_bypass_typed=auth_bypass_typed,
            seeding_policy_typed=seeding_policy_typed,
            queue_guardrails_typed=queue_guardrails_typed,
            raw=src,
        )


@dataclass(frozen=True)
class DownloadClientsConfig:
    clients: dict[str, DownloadClientConfig] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DownloadClientsConfig":
        if data is not None and not isinstance(data, dict):
            raise ValueError("download_clients must be an object")
        src = dict(data or {})
        clients: dict[str, DownloadClientConfig] = {}
        for key, value in src.items():
            token = str(key or "").strip().lower()
            if not token or not isinstance(value, dict):
                continue
            clients[token] = DownloadClientConfig.from_dict(value)
        return cls(clients=clients, raw=src)

    def get(self, key: str) -> DownloadClientConfig | None:
        token = str(key or "").strip().lower()
        if not token:
            return None
        return self.clients.get(token)

    def configured_keys(self) -> list[str]:
        return sorted(self.clients.keys())


@dataclass(frozen=True)
class TechnologyBindingsConfig:
    torrent_client: str = ""
    usenet_client: str = ""
    media_server: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
    ) -> "TechnologyBindingsConfig":
        src = dict(data or {})
        return cls(
            torrent_client=str(src.get("torrent_client", "")).strip().lower(),
            usenet_client=str(src.get("usenet_client", "")).strip().lower(),
            media_server=str(src.get("media_server", "")).strip().lower(),
            raw=src,
        )
