"""Config-driven Servarr adapter hooks."""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import Any, Callable

BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
LogFn = Callable[[str], None]
EnsureReadarrMetadataFn = Callable[[dict[str, Any], dict[str, Any], str, str, str], None]


@dataclass(frozen=True)
class AdapterDependencies:
    bool_cfg: BoolCfgFn
    log: LogFn
    ensure_readarr_metadata_source: EnsureReadarrMetadataFn


@dataclass(frozen=True)
class AppBootstrapContext:
    cfg: dict[str, Any]
    app_cfg: dict[str, Any]
    app_url: str
    api_base: str
    api_key: str


HookFn = Callable[["AdapterDependencies", "AppBootstrapContext"], None]


class ServarrAdapterHooks:
    """Built-in Servarr adapter hook surface (ADR-0012).

    Holds the canonical ``before_common_steps`` hooks plus the
    spec-string hook loader. Methods are plain instance methods (no
    ``@staticmethod``); the module-level ``_INSTANCE`` singleton
    + module-level aliases preserve the historical import API
    (``noop_before_common_steps``, ``readarr_before_common_steps``,
    ``_load_hook_from_spec``).
    """

    def noop_before_common_steps(
        self,
        _deps: AdapterDependencies,
        _ctx: AppBootstrapContext,
    ) -> None:
        return None

    def readarr_before_common_steps(
        self,
        deps: AdapterDependencies,
        ctx: AppBootstrapContext,
    ) -> None:
        readarr_cfg = ctx.cfg.get("readarr") or {}
        try:
            deps.ensure_readarr_metadata_source(
                ctx.cfg,
                ctx.app_cfg,
                ctx.app_url,
                ctx.api_base,
                ctx.api_key,
            )
        except Exception as exc:
            if deps.bool_cfg(readarr_cfg, "metadata_source_required", False):
                raise
            deps.log(
                f"[WARN] Readarr metadata source: bootstrap skipped ({exc}). "
                "Set readarr.metadata_source_required=true to fail the bootstrap instead."
            )

    def load_hook_from_spec(self, spec: str) -> HookFn:
        raw = str(spec or "").strip()
        if not raw:
            raise ValueError("Adapter hook spec must not be empty.")
        if ":" not in raw:
            raise ValueError(
                f"Invalid adapter hook spec '{raw}'. Expected format 'module.submodule:function_name'."
            )
        module_name, func_name = raw.rsplit(":", 1)
        module_name = module_name.strip()
        func_name = func_name.strip()
        if not module_name or not func_name:
            raise ValueError(
                f"Invalid adapter hook spec '{raw}'. Expected format 'module.submodule:function_name'."
            )
        module = importlib.import_module(module_name)
        hook = getattr(module, func_name, None)
        if not callable(hook):
            raise TypeError(f"Adapter hook '{raw}' is not callable.")
        return hook


_INSTANCE = ServarrAdapterHooks()

# Module-level aliases preserve the historical import API and the
# spec-string lookup path (``getattr(module, "noop_before_common_steps")``
# inside ``ServarrAdapterHooks.load_hook_from_spec``).
noop_before_common_steps = _INSTANCE.noop_before_common_steps
readarr_before_common_steps = _INSTANCE.readarr_before_common_steps
_load_hook_from_spec = _INSTANCE.load_hook_from_spec


@dataclass(frozen=True)
class AdapterRegistry:
    before_common_steps: dict[str, HookFn]

    @classmethod
    def from_config(cls, adapter_hooks_cfg: dict[str, Any] | None = None) -> "AdapterRegistry":
        hooks: dict[str, HookFn] = {}
        cfg = adapter_hooks_cfg or {}
        before_cfg = cfg.get("before_common_steps") or {}
        if not isinstance(before_cfg, dict):
            raise ValueError("adapter_hooks.before_common_steps must be an object/map.")

        # Dispatch through ``sys.modules[__name__]`` so tests that
        # ``mock.patch`` ``_load_hook_from_spec`` on this module
        # intercept the call.
        loader = sys.modules[__name__]._load_hook_from_spec
        for impl, spec in before_cfg.items():
            key = str(impl or "").strip().lower()
            if not key:
                continue
            if spec is None or str(spec).strip() == "":
                hooks.pop(key, None)
                continue
            hooks[key] = loader(str(spec))

        return cls(before_common_steps=hooks)

    def before_common_steps_for(self, implementation: str) -> HookFn:
        impl = str(implementation or "").strip().lower()
        # Dispatch through the module alias so tests patching
        # ``noop_before_common_steps`` on this module intercept the
        # default-hook lookup.
        default_hook = sys.modules[__name__].noop_before_common_steps
        return self.before_common_steps.get(impl, default_hook)
