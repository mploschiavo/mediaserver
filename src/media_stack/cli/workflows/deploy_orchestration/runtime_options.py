"""DeployRuntimeOptions — Strategy for cfg-derived runtime decisions.

ADR-0015 Phase 4. Pre-Phase-4 these methods lived on
``RunnerServicesMixin`` (a god-mixin in commands/). They're all
pure functions of :class:`DeployStackConfig` plus a small amount
of cached state (``_delete_environment_enabled``) — the natural
shape is one SRP class that owns those derivations.

Strategy pattern: the runner asks ``runtime_options`` for the
right cfg-derived decision, and the strategy returns the answer.
The caller doesn't know whether the value came from cfg, from a
warn-and-block safeguard (delete-env), or from a static rule.

The cfg is constructor-injected; the strategy holds no global
state and no IO. Tests can instantiate :class:`DeployRuntimeOptions`
with a hand-built :class:`DeployStackConfig` and exercise the
methods without spinning a full :class:`DeployPipelineRunner`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.cli.workflows.deploy_errors import DeployError
from media_stack.core.auth.provider_registry import compose_service_names_by_provider
from media_stack.core.cli_common import warn
from media_stack.core.platform_adapter import normalize_platform_target


if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_cli_config_service import (
        DeployStackConfig,
    )


class DeployRuntimeOptions:
    """Strategy: derive runtime decisions from the operator's cfg.

    Methods return tuples / strings / bools — no IO, no caching
    apart from the delete-env safeguard cache (which short-circuits
    the warn-and-block path so the same deploy can't trip the
    safeguard twice).
    """

    def __init__(self, cfg: "DeployStackConfig") -> None:
        self._cfg = cfg
        self._delete_environment_enabled_cache: bool | None = None

    # -- token list helpers ---------------------------------------------

    def compose_profiles(self) -> tuple[str, ...]:
        raw = str(self._cfg.compose_profiles or "").strip()
        if not raw:
            return ()
        return tuple(token.strip() for token in raw.split(",") if token.strip())

    def selected_apps(self) -> tuple[str, ...]:
        raw = str(self._cfg.selected_apps or "").strip()
        out: list[str] = []
        seen: set[str] = set()
        for item in raw.split(",") if raw else ():
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)

        for service_name in self.auth_provider_service_names():
            token = str(service_name or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def auth_provider_service_names(self) -> tuple[str, ...]:
        provider = str(self._cfg.auth_provider or "").strip().lower()
        if not provider or provider == "none":
            return ()
        service_names = tuple(compose_service_names_by_provider().get(provider) or ())
        out: list[str] = []
        seen: set[str] = set()
        for item in service_names:
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    def chaos_actions(self) -> tuple[str, ...]:
        raw = str(self._cfg.chaos_actions or "").strip()
        if not raw:
            return ()
        out: list[str] = []
        seen: set[str] = set()
        for item in raw.split(","):
            token = str(item or "").strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return tuple(out)

    # -- truthiness + platform target -----------------------------------

    def is_truthy(self, value: str) -> bool:
        token = str(value or "").strip().lower()
        return token in {"1", "true", "yes", "on", "y"}

    def resolved_platform_target(self) -> str:
        resolved = normalize_platform_target(self._cfg.platform_target)
        if not resolved:
            raise DeployError("PLATFORM_TARGET cannot be empty.")
        return resolved

    # -- delete-environment safeguard -----------------------------------

    def delete_environment_requested(self) -> bool:
        return self.is_truthy(self._cfg.delete_namespace)

    def delete_environment_confirmation_target(self) -> str:
        target = self.resolved_platform_target()
        if target == "compose":
            candidate = str(self._cfg.compose_project_name or "").strip()
            if candidate:
                return candidate
        return str(self._cfg.namespace or "").strip()

    def delete_environment_enabled(self) -> bool:
        # The warn-on-block path must run only once per deploy; the
        # cache short-circuits subsequent calls so the operator
        # doesn't see a duplicated warning when the runner checks
        # this in both the banner-print and delete-phase paths.
        if self._delete_environment_enabled_cache is not None:
            return self._delete_environment_enabled_cache
        if not self.delete_environment_requested():
            self._delete_environment_enabled_cache = False
            return False
        confirmation = str(self._cfg.delete_namespace_confirm or "").strip()
        confirmation_target = self.delete_environment_confirmation_target()
        if confirmation == "I_UNDERSTAND":
            self._delete_environment_enabled_cache = True
            return True
        if confirmation and confirmation_target and confirmation == confirmation_target:
            self._delete_environment_enabled_cache = True
            return True
        warn(
            "Delete namespace requested but blocked by safeguard. "
            "Set DELETE_NAMESPACE_CONFIRM to the environment identifier "
            f"('{confirmation_target}') or 'I_UNDERSTAND' to allow teardown."
        )
        self._delete_environment_enabled_cache = False
        return False


__all__ = ["DeployRuntimeOptions"]
