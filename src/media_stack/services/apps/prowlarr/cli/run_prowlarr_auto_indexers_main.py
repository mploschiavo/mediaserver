#!/usr/bin/env python3
"""Run Prowlarr auto-indexer discovery job with Kubernetes orchestration."""

from __future__ import annotations

import argparse
import os
import sys

from media_stack.cli.workflows.cli_common import repo_root_from_script_file
from media_stack.core.defaults import default_controller_image
from media_stack.core.exceptions import ConfigError, KubernetesError, MediaStackError
from media_stack.core.platforms.kubernetes.kube_client import KubernetesClient

from .prowlarr_auto_indexers_runtime import (
    AutoIndexerConfig,
    PhaseTracker,
    ProwlarrAutoIndexerRunner,
    warn,
)


class ProwlarrAutoIndexersMain:

    def build_arg_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="bin/run-prowlarr-auto-indexers.sh",
            description=(
                "Auto-discovers Prowlarr indexer templates/presets, tests each, and adds only "
                "those that pass."
            ),
        )
        parser.add_argument("--namespace", default=os.getenv("NAMESPACE", "media-stack"))
        parser.add_argument("--timeout", default="20m")
        parser.add_argument("--heartbeat-interval", type=int, default=15)
        parser.add_argument("--prepare-host-root", default="/srv/media-stack")
        parser.add_argument(
            "--bootstrap-runner-image",
            default=default_controller_image(),
        )
        parser.add_argument(
            "--exclude-name-token",
            action="append",
            default=None,
            help=(
                "Exclude auto-indexer candidates whose name contains this token. "
                "Can be passed multiple times."
            ),
        )
        parser.add_argument(
            "--reputation-state-path",
            default=os.getenv(
                "AUTO_INDEXER_REPUTATION_STATE_PATH",
                "/srv-config/prowlarr/indexer-reputation-state.json",
            ),
            help="State file path for indexer reputation scoring and quarantine.",
        )
        parser.add_argument(
            "--quarantine-score-threshold",
            type=int,
            default=int(os.getenv("AUTO_INDEXER_QUARANTINE_SCORE_THRESHOLD", "-10")),
            help="Quarantine an indexer when score <= threshold.",
        )
        parser.add_argument(
            "--quarantine-failure-threshold",
            type=int,
            default=int(os.getenv("AUTO_INDEXER_QUARANTINE_FAILURE_THRESHOLD", "3")),
            help="Quarantine an indexer when failure count >= threshold.",
        )
        parser.add_argument(
            "--quarantine-ttl-hours",
            type=int,
            default=int(os.getenv("AUTO_INDEXER_QUARANTINE_TTL_HOURS", "72")),
            help="Auto-unquarantine after this TTL (hours).",
        )
        return parser

    def parse_config(self, argv: list[str] | None = None) -> AutoIndexerConfig:
        args = build_arg_parser().parse_args(argv)
        namespace = str(args.namespace or "").strip()
        timeout_raw = str(args.timeout or "").strip()
        heartbeat_interval = int(args.heartbeat_interval)
        prepare_host_root = str(args.prepare_host_root or "").strip()
        bootstrap_runner_image = str(args.bootstrap_runner_image or "").strip()
        cli_excludes = [
            str(item).strip().lower() for item in (args.exclude_name_token or []) if str(item).strip()
        ]
        env_excludes = [
            item.strip().lower()
            for item in str(os.getenv("AUTO_INDEXER_EXCLUDE_NAME_TOKENS", "")).split(",")
            if item.strip()
        ]
        # Exclude tokens come from --exclude-name-token (CLI) and
        # AUTO_INDEXER_EXCLUDE_NAME_TOKENS (env). No hardcoded
        # defaults: opinionated source-code denylists made the
        # indexer set look mysteriously different from Prowlarr's
        # own preset list. Operators who want specific names
        # excluded can set the env var or pass the flag.
        exclude_name_tokens = list(dict.fromkeys(cli_excludes + env_excludes))

        if not namespace:
            raise ConfigError("namespace must be non-empty")
        if not timeout_raw:
            raise ConfigError("timeout must be non-empty")
        if heartbeat_interval <= 0:
            raise ConfigError("heartbeat interval must be > 0")
        if not prepare_host_root:
            raise ConfigError("prepare host root must be non-empty")
        if not bootstrap_runner_image:
            raise ConfigError("bootstrap runner image must be non-empty")

        return AutoIndexerConfig(
            namespace=namespace,
            timeout_raw=timeout_raw,
            heartbeat_interval=heartbeat_interval,
            prepare_host_root=prepare_host_root,
            bootstrap_runner_image=bootstrap_runner_image,
            exclude_name_tokens=exclude_name_tokens,
            reputation_cfg={
                "enabled": True,
                "state_path": str(args.reputation_state_path or "").strip(),
                "quarantine_score_threshold": int(args.quarantine_score_threshold),
                "quarantine_failure_threshold": int(args.quarantine_failure_threshold),
                "quarantine_ttl_hours": int(args.quarantine_ttl_hours),
            },
            root_dir=repo_root_from_script_file(__file__),
        )

    def main(self, argv: list[str] | None = None) -> int:
        tracker = PhaseTracker()
        try:
            cfg = parse_config(argv)
            runner = ProwlarrAutoIndexerRunner(
                cfg=cfg,
                kube=KubernetesClient.from_environment(),
                tracker=tracker,
            )
            return runner.run()
        except (ConfigError, KubernetesError, MediaStackError) as exc:
            if tracker.current_phase:
                tracker.end("failed")
            warn(f"Auto-indexer job runner failed: {exc}")
            tracker.print_summary()
            return 1


_instance = ProwlarrAutoIndexersMain()
build_arg_parser = _instance.build_arg_parser
parse_config = _instance.parse_config
main = _instance.main


if __name__ == "__main__":
    sys.exit(main())
