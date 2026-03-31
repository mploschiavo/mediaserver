#!/usr/bin/env python3
"""Reconcile SABnzbd API accessibility for in-cluster Arr clients."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass

from core.exceptions import ConfigError, KubernetesError, MediaStackError
from core.kube import KubectlClient
from core.logging_utils import configure_logging, log_event

SAB_RECONCILE_SCRIPT = r"""
set -eu
conf="/config/sabnzbd.ini"
[ -f "$conf" ] || { echo "__ERR__=missing_config"; exit 1; }

mkdir -p \
  "${SAB_DOWNLOAD_DIR}" \
  "${SAB_COMPLETE_DIR}" \
  "${SAB_COMPLETE_DIR}/tv" \
  "${SAB_COMPLETE_DIR}/movies" \
  "${SAB_COMPLETE_DIR}/music" \
  "${SAB_COMPLETE_DIR}/books" \
  "/config/Downloads/incomplete" \
  "/config/Downloads/complete"
chmod -R 0777 "${SAB_DOWNLOAD_DIR}" "${SAB_COMPLETE_DIR}" "/config/Downloads" 2>/dev/null || true

current_hw="$(awk -F "=" '/^host_whitelist[[:space:]]*=/{print $2; exit}' "$conf" | tr -d " " || true)"
current_lr="$(awk -F "=" '/^local_ranges[[:space:]]*=/{print $2; exit}' "$conf" | tr -d " " || true)"

dedupe_csv() {
  printf "%s" "$1" \
    | tr "," "\n" \
    | sed "s/^[[:space:]]*//;s/[[:space:]]*$//" \
    | awk "NF && !seen[\$0]++" \
    | paste -sd "," -
}

desired_hw="$(dedupe_csv "${current_hw},${SAB_HOST},${SAB_CLUSTER_HOST_1},${SAB_CLUSTER_HOST_2},${SAB_CLUSTER_HOST_3},${SAB_INGRESS_HOST},localhost,127.0.0.1,${SAB_HOST_WHITELIST_APPEND}")"
desired_lr="$(dedupe_csv "${current_lr},${SAB_LOCAL_RANGES}")"

before="$(grep -E "^(host_whitelist|local_ranges|download_dir|complete_dir|auto_browser)[[:space:]]*=" "$conf" 2>/dev/null || true)"

if grep -q "^host_whitelist[[:space:]]*=" "$conf"; then
  sed -i "s#^host_whitelist[[:space:]]*=.*#host_whitelist = ${desired_hw}#" "$conf"
else
  echo "host_whitelist = ${desired_hw}" >>"$conf"
fi

if grep -q "^local_ranges[[:space:]]*=" "$conf"; then
  sed -i "s#^local_ranges[[:space:]]*=.*#local_ranges = ${desired_lr}#" "$conf"
else
  echo "local_ranges = ${desired_lr}" >>"$conf"
fi

if grep -q "^download_dir[[:space:]]*=" "$conf"; then
  sed -i "s#^download_dir[[:space:]]*=.*#download_dir = ${SAB_DOWNLOAD_DIR}#" "$conf"
else
  echo "download_dir = ${SAB_DOWNLOAD_DIR}" >>"$conf"
fi

if grep -q "^complete_dir[[:space:]]*=" "$conf"; then
  sed -i "s#^complete_dir[[:space:]]*=.*#complete_dir = ${SAB_COMPLETE_DIR}#" "$conf"
else
  echo "complete_dir = ${SAB_COMPLETE_DIR}" >>"$conf"
fi

if grep -q "^auto_browser[[:space:]]*=" "$conf"; then
  sed -i "s#^auto_browser[[:space:]]*=.*#auto_browser = ${SAB_AUTO_BROWSER}#" "$conf"
else
  echo "auto_browser = ${SAB_AUTO_BROWSER}" >>"$conf"
fi

after="$(grep -E "^(host_whitelist|local_ranges|download_dir|complete_dir|auto_browser)[[:space:]]*=" "$conf" 2>/dev/null || true)"

changed=0
[ "$before" = "$after" ] || changed=1

echo "__CHANGED__=${changed}"
echo "__HOST_WHITELIST__=${desired_hw}"
echo "__LOCAL_RANGES__=${desired_lr}"
echo "__DOWNLOAD_DIR__=${SAB_DOWNLOAD_DIR}"
echo "__COMPLETE_DIR__=${SAB_COMPLETE_DIR}"
echo "__AUTO_BROWSER__=${SAB_AUTO_BROWSER}"
"""


@dataclass(frozen=True)
class SabnzbdApiAccessConfig:
    namespace: str = "media-stack"
    deployment: str = "sabnzbd"
    sab_host: str = "sabnzbd"
    sab_ingress_host: str = "sabnzbd.local"
    sab_local_ranges: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    sab_host_whitelist_append: str = ""
    sab_download_dir: str = "/data/usenet/incomplete"
    sab_complete_dir: str = "/data/usenet/completed"
    sab_auto_browser: str = "0"

    @classmethod
    def from_env(cls) -> "SabnzbdApiAccessConfig":
        return cls(
            namespace=os.environ.get("NAMESPACE", cls.namespace),
            deployment=os.environ.get("SAB_DEPLOYMENT", cls.deployment),
            sab_host=os.environ.get("SAB_HOST", cls.sab_host),
            sab_ingress_host=os.environ.get("SAB_INGRESS_HOST", cls.sab_ingress_host),
            sab_local_ranges=os.environ.get("SAB_LOCAL_RANGES", cls.sab_local_ranges),
            sab_host_whitelist_append=os.environ.get(
                "SAB_HOST_WHITELIST_APPEND", cls.sab_host_whitelist_append
            ),
            sab_download_dir=os.environ.get("SAB_DOWNLOAD_DIR", cls.sab_download_dir),
            sab_complete_dir=os.environ.get("SAB_COMPLETE_DIR", cls.sab_complete_dir),
            sab_auto_browser=os.environ.get("SAB_AUTO_BROWSER", cls.sab_auto_browser),
        )


@dataclass(frozen=True)
class ReconcileResult:
    changed: bool
    host_whitelist: str
    local_ranges: str
    download_dir: str
    complete_dir: str
    auto_browser: str
    raw_output: str


class SabnzbdApiAccessService:
    """Service object for side-effecting SAB config reconciliation."""

    MARKER_RE = re.compile(r"^__(?P<key>[A-Z_]+)__=(?P<value>.*)$", re.MULTILINE)

    def __init__(
        self,
        cfg: SabnzbdApiAccessConfig,
        kube: KubectlClient,
        logger: logging.Logger,
    ) -> None:
        self.cfg = cfg
        self.kube = kube
        self.logger = logger

    def run(self) -> int:
        if not self._deployment_exists():
            log_event(
                self.logger,
                logging.INFO,
                "sab.reconcile.skip_deployment_missing",
                namespace=self.cfg.namespace,
                deployment=self.cfg.deployment,
            )
            return 0

        self._rollout_status()
        result = self._reconcile_in_pod()

        log_event(
            self.logger,
            logging.INFO,
            "sab.reconcile.settings",
            host_whitelist=result.host_whitelist,
            local_ranges=result.local_ranges,
            download_dir=result.download_dir,
            complete_dir=result.complete_dir,
            auto_browser=result.auto_browser,
        )

        if result.changed:
            log_event(
                self.logger,
                logging.INFO,
                "sab.reconcile.restart",
                deployment=self.cfg.deployment,
                namespace=self.cfg.namespace,
            )
            self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "rollout",
                    "restart",
                    f"deploy/{self.cfg.deployment}",
                ]
            )
            self._rollout_status()
            log_event(self.logger, logging.INFO, "sab.reconcile.completed", restarted=True)
        else:
            log_event(self.logger, logging.INFO, "sab.reconcile.completed", restarted=False)

        return 0

    def _deployment_exists(self) -> bool:
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "deploy",
                self.cfg.deployment,
            ],
            check=False,
        )
        return result.returncode == 0

    def _rollout_status(self) -> None:
        log_event(
            self.logger,
            logging.INFO,
            "sab.rollout.wait",
            deployment=self.cfg.deployment,
            namespace=self.cfg.namespace,
            timeout="10m",
        )
        self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "rollout",
                "status",
                f"deploy/{self.cfg.deployment}",
                "--timeout=10m",
            ]
        )

    def _reconcile_in_pod(self) -> ReconcileResult:
        cluster_host_1 = f"{self.cfg.sab_host}.{self.cfg.namespace}"
        cluster_host_2 = f"{self.cfg.sab_host}.{self.cfg.namespace}.svc"
        cluster_host_3 = f"{self.cfg.sab_host}.{self.cfg.namespace}.svc.cluster.local"

        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "exec",
                f"deploy/{self.cfg.deployment}",
                "--",
                "env",
                f"SAB_HOST={self.cfg.sab_host}",
                f"SAB_CLUSTER_HOST_1={cluster_host_1}",
                f"SAB_CLUSTER_HOST_2={cluster_host_2}",
                f"SAB_CLUSTER_HOST_3={cluster_host_3}",
                f"SAB_INGRESS_HOST={self.cfg.sab_ingress_host}",
                f"SAB_LOCAL_RANGES={self.cfg.sab_local_ranges}",
                f"SAB_HOST_WHITELIST_APPEND={self.cfg.sab_host_whitelist_append}",
                f"SAB_DOWNLOAD_DIR={self.cfg.sab_download_dir}",
                f"SAB_COMPLETE_DIR={self.cfg.sab_complete_dir}",
                f"SAB_AUTO_BROWSER={self.cfg.sab_auto_browser}",
                "sh",
                "-lc",
                SAB_RECONCILE_SCRIPT,
            ]
        )

        markers = self._parse_markers(result.stdout)
        changed_raw = markers.get("CHANGED")
        if changed_raw is None:
            log_event(
                self.logger,
                logging.WARNING,
                "sab.reconcile.marker_missing",
                marker="CHANGED",
                raw_output=result.stdout.strip(),
            )
            changed = True
        else:
            changed = changed_raw.strip() == "1"

        return ReconcileResult(
            changed=changed,
            host_whitelist=markers.get("HOST_WHITELIST", ""),
            local_ranges=markers.get("LOCAL_RANGES", ""),
            download_dir=markers.get("DOWNLOAD_DIR", ""),
            complete_dir=markers.get("COMPLETE_DIR", ""),
            auto_browser=markers.get("AUTO_BROWSER", ""),
            raw_output=result.stdout,
        )

    def _parse_markers(self, output: str) -> dict[str, str]:
        markers: dict[str, str] = {}
        for match in self.MARKER_RE.finditer(output):
            markers[match.group("key")] = match.group("value")
        return markers


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/ensure-sabnzbd-api-access.sh",
        description=(
            "Ensures SABnzbd API is reachable by Arr apps inside the cluster by "
            "reconciling host_whitelist and local_ranges in /config/sabnzbd.ini."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    build_arg_parser().parse_args(argv)
    logger = configure_logging()

    try:
        cfg = SabnzbdApiAccessConfig.from_env()
        service = SabnzbdApiAccessService(
            cfg=cfg, kube=KubectlClient.from_environment(), logger=logger
        )
        return service.run()
    except (ConfigError, KubernetesError, MediaStackError) as exc:
        log_event(logger, logging.ERROR, "sab.reconcile.failed", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
