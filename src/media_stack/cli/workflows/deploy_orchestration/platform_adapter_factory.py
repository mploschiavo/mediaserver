"""PlatformAdapterFactory — Factory for platform plugin + adapter resolution.

ADR-0015 Phase 4. Pre-Phase-4 these methods lived on
``RunnerServicesMixin`` (a god-mixin in commands/). The shape is
the GoF Factory pattern: resolve a platform plugin from the cfg's
``platform_target`` token, then ask the plugin to build a
:class:`RebuildPlatformAdapter` for the current runner.

All three caches (plugin, adapter, per-key client) survive on
this class so that platform plugins (compose / k8s) get
constructed exactly once per pipeline run — the runtime cost is
non-trivial (especially for the k8s adapter which probes the
cluster) and the prior implementation already cached.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from media_stack.cli.workflows.deploy_errors import DeployError
from media_stack.core.platform_adapter import (
    RebuildPlatformAdapter,
    RebuildPlatformAdapterBuildRequest,
    build_rebuild_platform_adapter,
)
from media_stack.core.platform_plugin_contract import PlatformPlugin
from media_stack.core.platform_plugin_registry import resolve_platform_plugin


if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )
    from media_stack.cli.workflows.deploy_orchestration.runtime_options import (
        DeployRuntimeOptions,
    )


class PlatformAdapterFactory:
    """Factory: build (and cache) platform plugins, adapters, and clients.

    The factory takes the cfg + runtime-options at construction so
    it can resolve ``platform_target`` lazily. ``configure_runtime``
    + ``adapter`` both take the runner reference at call-time
    because the platform plugin contract still needs access to the
    runner (for ``build_runner_request`` and ``configure_runner``).
    Future cleanup: replace the runner reference with a narrower
    Protocol so the plugin contract doesn't depend on the concrete
    pipeline class.
    """

    def __init__(
        self,
        cfg: "DeployStackConfig",
        runtime_options: "DeployRuntimeOptions",
        info_fn: Callable[[str], None],
    ) -> None:
        self._cfg = cfg
        self._runtime_options = runtime_options
        self._info_fn = info_fn
        self._plugin_cache: PlatformPlugin | None = None
        self._adapter_cache: RebuildPlatformAdapter | None = None
        self._client_cache: dict[str, object] = {}

    def platform_plugin(self) -> PlatformPlugin:
        if self._plugin_cache is None:
            plugin = resolve_platform_plugin(
                self._runtime_options.resolved_platform_target(),
            )
            if plugin is None:
                raise DeployError(
                    f"Unsupported platform target '{self._cfg.platform_target}'. "
                    "No platform plugin could be resolved."
                )
            self._plugin_cache = plugin
        return self._plugin_cache

    def configure_runtime(self, runner: object) -> None:
        try:
            self.platform_plugin().configure_runner(runner)
        except Exception as exc:
            raise DeployError(str(exc)) from exc

    def adapter(self, runner: object) -> RebuildPlatformAdapter:
        if self._adapter_cache is None:
            try:
                request_payload = self.platform_plugin().build_runner_request(
                    runner, self._info_fn,
                )
                request = RebuildPlatformAdapterBuildRequest(**request_payload)
                self._adapter_cache = build_rebuild_platform_adapter(request=request)
            except ValueError as exc:
                raise DeployError(str(exc)) from exc
        return self._adapter_cache

    def get_or_create_client(
        self,
        key: str,
        factory: Callable[[], object],
    ) -> object:
        token = str(key or "").strip().lower()
        if not token:
            raise DeployError("Platform client cache key cannot be empty.")
        if token not in self._client_cache:
            self._client_cache[token] = factory()
        return self._client_cache[token]


__all__ = ["PlatformAdapterFactory"]
