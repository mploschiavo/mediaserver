"""Shared contract for pluggable platform integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

RequireDependencyFn = Callable[[object, object | None, str], object]
BuildAdapterFn = Callable[[object, RequireDependencyFn], object]
BuildRunnerRequestFn = Callable[[object, Callable[[str], None]], dict[str, object]]
ConfigureRunnerFn = Callable[[object], None]
RunBootstrapFn = Callable[[object], None]


@dataclass(frozen=True)
class PlatformPlugin:
    key: str
    aliases: tuple[str, ...]
    build_adapter: BuildAdapterFn
    build_runner_request: BuildRunnerRequestFn
    configure_runner: ConfigureRunnerFn
    run_bootstrap: RunBootstrapFn
    bootstrap_phase_name: str = "Run bootstrap pipeline"
    supports_secret_lifecycle: bool = False
    supports_secret_generation: bool = False
    supports_ingress_patch: bool = False
    supports_scale_policy_guardrails: bool = False
    supports_failure_status_snapshot: bool = False
    requires_dynamic_pvc_storage_mode: bool = False
    requires_runtime_config_policy_handler: bool = False
    logs_bootstrap_runner_image: bool = False
