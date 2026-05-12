"""ControllerBootPreparation — pre-API-server boot orchestration.

ADR-0015 Phase 7m. Pre-Phase-7m these 4 boot-prep helpers lived
on :class:`ControllerServeCommand` in commands/. They're workflow
material (config-path resolution + boot profile application +
API-key predispatch), not HTTP-server glue, so Phase 7m moves them
to workflows/. The remaining ``controller_serve.py`` keeps only
HTTP-server wiring (action queue, log instrumentation, dispatch
loop) per ADR-0015's explicit HTTP-tier exemption.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Callable

from media_stack.api.preflight.api_keys import run_preflight as _discover_keys
from media_stack.api.preflight.profile_validation import validate_profile
from media_stack.cli.workflows.controller_boot.key_canary_validator import (
    KeyCanaryValidator,
)
from media_stack.cli.workflows.controller_profile_env_loader import (
    ControllerProfileEnvLoader,
)
from media_stack.services.jobs.controller_handlers import (
    _auto_generate_config_json,
    _resolve_config_path as _services_resolve_config_path,
)


class ControllerBootPreparation:
    """Pre-API-server boot orchestration: config + profile + API keys."""

    def __init__(
        self,
        key_canary: KeyCanaryValidator,
        log: Callable[[str], None],
        profile_env_loader: ControllerProfileEnvLoader | None = None,
    ) -> None:
        self._key_canary = key_canary
        self._log = log
        self._profile_env_loader = (
            profile_env_loader or ControllerProfileEnvLoader()
        )

    def resolve_config_path(self, args: argparse.Namespace) -> None:
        """Resolve or auto-generate the bootstrap config JSON path."""
        resolved = _services_resolve_config_path(args.config)
        if resolved and resolved != args.config:
            self._log(f"[INFO] Config resolved: {args.config} → {resolved}")
            args.config = resolved
            return
        if not resolved:
            self._log(
                "[INFO] Bootstrap config JSON not found — "
                "generating from contracts + profile"
            )
            try:
                generated = _auto_generate_config_json(args.config)
                if generated:
                    args.config = generated
                    self._log(
                        f"[OK] Generated config from contracts: {generated}"
                    )
            except Exception as exc:  # noqa: BLE001 — best-effort generation
                self._log(
                    f"[WARN] Config generation failed: {exc}. "
                    "Bootstrap may skip some steps."
                )

    def opt_out_of_legacy_media_server_adapter(self) -> None:
        """Tell finalize to skip the legacy media server adapter.

        Media server ops are handled by the configure-media-server job
        framework. The old adapter reads ``config.json`` which has
        fewer tuners/guides than the profile, so it would silently
        narrow what the controller exposed; we skip it explicitly.
        """
        os.environ["SKIP_MEDIA_SERVER_ADAPTER_IN_FINALIZE"] = "1"

    def apply_boot_profile(self, args: argparse.Namespace) -> None:
        """Validate + apply the boot profile YAML if present."""
        del args  # profile lookup uses BOOTSTRAP_PROFILE_FILE, not args
        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE")
        if not profile_file:
            return
        profile_path = Path(profile_file)
        if not profile_path.is_file():
            self._log(
                f"[INFO] Profile not yet available at {profile_file} — "
                "will apply from config when action is triggered"
            )
            return
        try:
            validate_profile(profile_file, log=self._log)
        except Exception as exc:  # noqa: BLE001 — non-fatal validation
            self._log(
                f"[WARN] Profile validation failed: {exc}. "
                "The controller will still start — fix the profile and restart."
            )
        self._profile_env_loader._apply_profile_env(profile_file)

    def predispatch_api_keys(self, args: argparse.Namespace) -> None:
        """Pre-discover API keys before the API server opens."""
        try:
            config_root = getattr(
                args, "config_root",
                os.environ.get("CONFIG_ROOT", "/srv-config"),
            )
            # Plumb the resolved value into ``os.environ`` so downstream
            # probes/ensurers see the same value the CLI was given.
            os.environ["CONFIG_ROOT"] = config_root
            self._log(
                f"[INFO] Config root discovery starting "
                f"(configured: {config_root})"
            )
            discovered = _discover_keys(
                config_root=config_root, log=self._log,
            )
            # Re-read CONFIG_ROOT — discovery may have rewritten it.
            config_root = os.environ.get("CONFIG_ROOT", config_root)
            for env_key, val in discovered.items():
                if val and not os.environ.get(env_key):
                    os.environ[env_key] = val
            if discovered:
                self._log(
                    f"[INFO] Pre-discovered {len(discovered)} API keys "
                    f"(config_root={config_root})"
                )
            self._key_canary.validate(discovered, config_root, self._log)
        except Exception as exc:  # noqa: BLE001 — best-effort pre-discovery
            self._log(f"[WARN] API key pre-discovery failed: {exc}")


__all__ = ["ControllerBootPreparation"]
