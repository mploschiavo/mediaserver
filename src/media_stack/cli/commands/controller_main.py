#!/usr/bin/env python3
"""Media Automation Stack bootstrap entrypoint.

Project steward: Matthew Loschiavo (https://matthewloschiavo.com)
Contact: mploschiavo@gmail.com | https://www.linkedin.com/in/matthewloschiavo
"""

import argparse
import logging
import os
import sys
import traceback

import media_stack.services.runtime_platform as runtime_platform
from media_stack.services.enums import BootstrapMode

# Re-export everything that external code imports from this module.
# This preserves backward compatibility for all existing import paths.
from media_stack.cli.commands.controller_handlers import (  # noqa: F401
    _load_handler_specs,
    _resolve_config_path,
    _resolve_handler,
    _run_handler_specs,
)
from media_stack.cli.commands.controller_dispatch import (  # noqa: F401
    _apply_overrides,
    _dispatch_action,
    _OVERRIDE_ENV_MAP,
    _SERVICE_ERROR_PATTERNS,
    _track_failed_service,
)
from media_stack.cli.commands.controller_k8s import (  # noqa: F401
    _persist_preflight_keys_to_secret,
)
from media_stack.cli.commands.controller_runner import (  # noqa: F401
    _build_config_policy,
    _build_runner,
)
from media_stack.cli.commands.controller_profile import (  # noqa: F401
    _apply_profile_env,
)
from media_stack.cli.commands.controller_serve import (  # noqa: F401
    _run_serve,
    _validate_key_against_service,
)

logger = logging.getLogger("bootstrap_service")


# ---------------------------------------------------------------------------
# One-shot mode (used by Docker Compose)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

class ControllerMainCommand:
    """Wraps the controller CLI entrypoint."""

    def main(self):
        logging.basicConfig(
            level=logging.DEBUG if os.environ.get("BOOTSTRAP_DEBUG") else logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )

        parser = argparse.ArgumentParser(
            description="Idempotent bootstrap for Arr + indexer-manager + request-manager integration."
        )
        parser.add_argument(
            "--config", default="/contracts/config.json", help="Bootstrap config JSON path"
        )
        parser.add_argument(
            "--config-root",
            default="/srv-config",
            help="Root path containing app config folders",
        )
        parser.add_argument(
            "--wait-timeout",
            type=int,
            default=600,
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
                "Runtime environment overlay key (used when config_overlays.enabled=true), "
                "for example: dev|stage|prod"
            ),
        )
        # API server mode flags.
        parser.add_argument(
            "--serve",
            action="store_true",
            help="Start persistent HTTP API service with action dispatch loop",
        )
        parser.add_argument(
            "--auto-run",
            action="store_true",
            help="When --serve, automatically begin bootstrap on startup",
        )
        parser.add_argument(
            "--api-port",
            type=int,
            default=int(os.environ.get("BOOTSTRAP_API_PORT", "9100")),
            help="HTTP API listen port (default: 9100)",
        )
        args = parser.parse_args()

        if args.serve:
            _run_serve(args)
        else:
            _run_oneshot(args)


    @staticmethod
    def _run_oneshot(args: argparse.Namespace) -> None:
        """Original one-shot bootstrap mode."""
        runner, runtime_state = _build_runner(args)
        runner.run(runtime_state)


_instance = ControllerMainCommand()
main = _instance.main


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
_run_oneshot = _instance._run_oneshot
