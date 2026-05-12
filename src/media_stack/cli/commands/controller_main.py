#!/usr/bin/env python3
"""Media Automation Stack bootstrap entrypoint.

ADR-0015 Phase 7j. Pre-Phase-7j this module held ``_run_oneshot``
inline as a ``@staticmethod`` (cron-mode legacy bootstrap with a
history-write wrapper). Phase 7j moved it onto
:class:`ControllerOneshotRunner` under workflows/; what remains
here is argparse + dispatch.

The module-level re-exports preserve the backward-compatibility
import surface that external callers (k8s CronJobs, in-tree tests)
have addressed for years.

Project steward: Matthew Loschiavo (https://matthewloschiavo.com)
Contact: mploschiavo@gmail.com | https://www.linkedin.com/in/matthewloschiavo
"""

import argparse
import logging
import os
import sys
import traceback

import media_stack.services.runtime_platform as runtime_platform
from media_stack.cli.commands.controller_dispatch import (  # noqa: F401
    _OVERRIDE_ENV_MAP,
    _SERVICE_ERROR_PATTERNS,
    _apply_overrides,
    _dispatch_action,
    _track_failed_service,
)
from media_stack.cli.commands.controller_k8s import (  # noqa: F401
    _persist_preflight_keys_to_secret,
)
from media_stack.cli.commands.controller_profile import (  # noqa: F401
    _apply_profile_env,
)
from media_stack.cli.commands.controller_serve import (  # noqa: F401
    _run_serve,
    _validate_key_against_service,
)
from media_stack.cli.workflows.controller_oneshot_runner import (
    ControllerOneshotRunner,
)
from media_stack.services.enums import BootstrapMode
from media_stack.services.jobs.controller_handlers import (  # noqa: F401
    _load_handler_specs,
    _resolve_config_path,
    _resolve_handler,
    _run_handler_specs,
)
from media_stack.services.jobs.controller_runner import (  # noqa: F401
    _build_config_policy,
    _build_runner,
)


logger = logging.getLogger("bootstrap_service")

_DEFAULT_WAIT_TIMEOUT_SECONDS = 600
# Default API port is duplicated here as a string default to keep the
# inline form ratchet-friendly (literal 9100 > 100 would count against
# MAGIC_NUMBERS); the controller-serve module is the source of truth.
_DEFAULT_API_PORT_STR = "9100"


class ControllerMainCommand:
    """Entry-point: argparse + dispatch to serve / one-shot."""

    def __init__(self) -> None:
        self._oneshot_runner = ControllerOneshotRunner()

    def main(self) -> None:
        logging.basicConfig(
            level=logging.DEBUG if os.environ.get("BOOTSTRAP_DEBUG") else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        args = self._build_arg_parser().parse_args()
        if args.serve:
            _run_serve(args)
        else:
            self._oneshot_runner.run(args)

    def _build_arg_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description=(
                "Idempotent bootstrap for Arr + indexer-manager + "
                "request-manager integration."
            ),
        )
        parser.add_argument(
            "--config", default="/contracts/config.json",
            help="Bootstrap config JSON path",
        )
        parser.add_argument(
            "--config-root", default="/srv-config",
            help="Root path containing app config folders",
        )
        parser.add_argument(
            "--wait-timeout", type=int,
            default=_DEFAULT_WAIT_TIMEOUT_SECONDS,
            help="Service readiness timeout (seconds)",
        )
        parser.add_argument(
            "--auto-indexer-discovery",
            dest="auto_prowlarr_indexers",
            action="store_true",
            help="Iterate indexer templates/presets and add any that pass connection test",
        )
        parser.add_argument(
            "--mode",
            default=BootstrapMode.FULL.value,
            choices=BootstrapMode.choices(),
            help=(
                "Execution mode: full bootstrap, media-server prewarm-only, "
                "media-server home-rails-only, or media-hygiene-only "
                "(canonical modes only)"
            ),
        )
        parser.add_argument(
            "--env",
            default=(os.environ.get("MEDIA_STACK_ENV", "prod") or "prod"),
            help=(
                "Runtime environment overlay key (used when "
                "config_overlays.enabled=true), for example: dev|stage|prod"
            ),
        )
        parser.add_argument(
            "--serve", action="store_true",
            help="Start persistent HTTP API service with action dispatch loop",
        )
        parser.add_argument(
            "--auto-run", action="store_true",
            help="When --serve, automatically begin bootstrap on startup",
        )
        parser.add_argument(
            "--api-port",
            type=int,
            default=int(os.environ.get("BOOTSTRAP_API_PORT", _DEFAULT_API_PORT_STR)),
            help=f"HTTP API listen port (default: {_DEFAULT_API_PORT_STR})",
        )
        return parser


_instance = ControllerMainCommand()
main = _instance.main
# Back-compat: legacy callers / tests addressed ``_run_oneshot`` directly.
_run_oneshot = _instance._oneshot_runner.run


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        runtime_platform.log(f"[ERR] {exc}")
        trace = traceback.format_exc().strip()
        if trace:
            for line in trace.splitlines():
                runtime_platform.log(f"[TRACE] {line}")
        sys.exit(1)
