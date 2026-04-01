from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RunBootstrapJobConfig:
    namespace: str
    timeout_raw: str
    heartbeat_interval: int
    job_log_tail_lines: int
    alert_webhook_url: str
    prepare_host_root: str
    ingress_name: str
    bootstrap_runner_image: str
    root_dir: Path
    config_file: Path
    skip_qbit_ensure: bool
    skip_sab_ensure: bool

    @property
    def timeout_seconds(self) -> int:
        raw = self.timeout_raw.strip()
        match = re.match(r"^(\d+)([smh]?)$", raw)
        if not match:
            return 600
        num = int(match.group(1))
        unit = match.group(2)
        if unit == "h":
            return num * 3600
        if unit in ("m", ""):
            return num * 60
        if unit == "s":
            return num
        return 600


def build_parser(root_dir: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run media-stack bootstrap job.\n\n"
            "Usage:\n"
            "  scripts/run-bootstrap-job.sh [CONFIG_FILE]"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "config_file",
        nargs="?",
        default=str(root_dir / "bootstrap" / "media-stack.bootstrap.json"),
        help="Bootstrap JSON file path.",
    )
    parser.add_argument(
        "--namespace",
        default=os.environ.get("NAMESPACE", "media-stack"),
        help="Kubernetes namespace (env: NAMESPACE).",
    )
    parser.add_argument(
        "--timeout",
        default=os.environ.get("TIMEOUT", "10m"),
        help="Wait timeout, e.g. 600s, 10m, 1h (env: TIMEOUT).",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=max(1, int(os.environ.get("HEARTBEAT_INTERVAL", "15"))),
        help="Heartbeat seconds while waiting for job completion.",
    )
    parser.add_argument(
        "--job-log-tail-lines",
        type=int,
        default=max(1, int(os.environ.get("JOB_LOG_TAIL_LINES", "120"))),
        help="Tail lines to print from bootstrap job logs.",
    )
    parser.add_argument(
        "--prepare-host-root",
        default=os.environ.get("PREPARE_HOST_ROOT", "/srv/media-stack"),
        help="Host root used in manifest overrides.",
    )
    parser.add_argument(
        "--ingress-name",
        default=os.environ.get("INGRESS_NAME", "media-stack-ingress"),
        help="Ingress to read hosts from.",
    )
    parser.add_argument(
        "--bootstrap-runner-image",
        default=os.environ.get(
            "BOOTSTRAP_RUNNER_IMAGE",
            "192.168.1.60:30002/library/media-stack-bootstrap-runner:latest",
        ),
        help="Bootstrap runner container image.",
    )
    parser.add_argument(
        "--alert-webhook-url",
        default=os.environ.get("ALERT_WEBHOOK_URL", ""),
        help="Optional webhook for status notifications.",
    )
    parser.add_argument(
        "--skip-qbit-ensure",
        action="store_true",
        default=env_bool("SKIP_QBIT_ENSURE", False),
        help="Skip torrent client credential ensure phase (legacy flag name).",
    )
    parser.add_argument(
        "--skip-sab-ensure",
        action="store_true",
        default=env_bool("SKIP_SAB_ENSURE", False),
        help="Skip SABnzbd ensure phase.",
    )
    return parser


def parse_run_bootstrap_job_config(
    argv: list[str] | None, *, root_dir: Path
) -> RunBootstrapJobConfig:
    parser = build_parser(root_dir)
    args = parser.parse_args(argv)
    return RunBootstrapJobConfig(
        namespace=str(args.namespace).strip() or "media-stack",
        timeout_raw=str(args.timeout).strip() or "10m",
        heartbeat_interval=max(1, int(args.heartbeat_interval)),
        job_log_tail_lines=max(1, int(args.job_log_tail_lines)),
        alert_webhook_url=str(args.alert_webhook_url).strip(),
        prepare_host_root=str(args.prepare_host_root).strip() or "/srv/media-stack",
        ingress_name=str(args.ingress_name).strip() or "media-stack-ingress",
        bootstrap_runner_image=str(args.bootstrap_runner_image).strip()
        or "192.168.1.60:30002/library/media-stack-bootstrap-runner:latest",
        root_dir=root_dir,
        config_file=Path(str(args.config_file)),
        skip_qbit_ensure=bool(args.skip_qbit_ensure),
        skip_sab_ensure=bool(args.skip_sab_ensure),
    )
