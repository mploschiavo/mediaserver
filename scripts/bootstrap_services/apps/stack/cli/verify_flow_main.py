#!/usr/bin/env python3
"""Verify media-stack bootstrap flow from logs + live cluster checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable

from core.exceptions import ConfigError, MediaStackError

from cli.cli_common import kube_cmd, run_command


@dataclass(frozen=True)
class VerifyFlowConfig:
    namespace: str


def parse_config(argv: list[str] | None = None) -> VerifyFlowConfig:
    parser = argparse.ArgumentParser(
        prog="scripts/verify-flow.sh",
        description="Verify bootstrap wiring from latest media-stack bootstrap logs and live checks.",
    )
    parser.add_argument("namespace", nargs="?", default="")
    args = parser.parse_args(argv)
    namespace = (
        str(args.namespace or "").strip()
        or os.environ.get("NAMESPACE", "media-stack").strip()
        or "media-stack"
    )
    return VerifyFlowConfig(namespace=namespace)


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    print(f"[INFO] {msg}")


class FlowVerifier:
    def __init__(self, cfg: VerifyFlowConfig) -> None:
        self.cfg = cfg
        self.kubectl = kube_cmd()
        self.bootstrap_log = ""

    def _ns_cmd(self, args: Iterable[str]) -> list[str]:
        return [*self.kubectl, "-n", self.cfg.namespace, *list(args)]

    def _kube_stdout(self, args: Iterable[str], *, check: bool = False) -> str:
        proc = run_command(self._ns_cmd(args), check=check)
        return proc.stdout or ""

    def _check_log(self, pattern: str, label: str, *, optional: bool = False) -> None:
        if re.search(pattern, self.bootstrap_log, flags=re.IGNORECASE | re.MULTILINE):
            _ok(label)
            return
        if optional:
            _info(f"{label} (not configured)")
            return
        _warn(label)

    def _check_writable(self, deploy: str, path: str, label: str) -> None:
        proc = run_command(
            self._ns_cmd(["exec", f"deploy/{deploy}", "--", "sh", "-lc", f"test -w '{path}'"]),
            check=False,
        )
        if proc.returncode == 0:
            _ok(label)
        else:
            _warn(label)

    def _check_arr_remote_mapping(
        self,
        deploy: str,
        port: int,
        api_base: str,
        remote_path: str,
        local_path: str,
        label: str,
    ) -> None:
        remote_norm = remote_path.rstrip("/")
        local_norm = local_path.rstrip("/")
        shell = (
            "API=$(grep -o '<ApiKey>[^<]*' /config/config.xml | head -n1 | sed 's#<ApiKey>##'); "
            f"curl -fsS -H \"X-Api-Key: $API\" http://localhost:{port}/api/{api_base}/remotepathmapping"
        )
        proc = run_command(
            self._ns_cmd(["exec", f"deploy/{deploy}", "--", "sh", "-lc", shell]),
            check=False,
        )
        if proc.returncode != 0:
            _warn(label)
            return
        try:
            payload = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            _warn(label)
            return
        if not isinstance(payload, list):
            _warn(label)
            return
        matched = any(
            str(item.get("remotePath", "")).rstrip("/") == remote_norm
            and str(item.get("localPath", "")).rstrip("/") == local_norm
            for item in payload
            if isinstance(item, dict)
        )
        if matched:
            _ok(label)
        else:
            _warn(label)

    def _check_homepage_hosts(self) -> None:
        ingress_hosts = self._kube_stdout(
            [
                "get",
                "ingress",
                "media-stack-ingress",
                "-o",
                "jsonpath={range .spec.rules[*]}{.host}{' '}{end}",
            ]
        ).strip()
        hp_services = self._kube_stdout(
            ["exec", "deploy/homepage", "--", "sh", "-lc", "cat /app/config/services.yaml 2>/dev/null"]
        )
        if not ingress_hosts or not hp_services.strip():
            _warn("Homepage ingress/service check skipped (missing ingress hosts or homepage config)")
            return
        missing = []
        for host in ingress_hosts.split():
            if host not in hp_services:
                missing.append(host)
        if missing:
            for host in missing:
                _warn(f"Homepage services.yaml missing host: {host}")
        else:
            _ok("Homepage services.yaml contains all ingress hosts")

        for card, label in (
            ("Jellyfin Setup QR", "Homepage onboarding includes Jellyfin QR card"),
            ("Samsung TV Quick Start", "Homepage onboarding includes Samsung quick steps"),
            ("Vizio Quick Start", "Homepage onboarding includes Vizio quick steps"),
            ("TCL Quick Start", "Homepage onboarding includes TCL quick steps"),
        ):
            if card in hp_services:
                _ok(label)
            else:
                _warn(label)

    def _check_cronjob(self) -> None:
        proc = run_command(
            self._ns_cmd(["get", "cronjob", "media-stack-bootstrap-reconcile", "-o", "jsonpath={.spec.schedule}"]),
            check=False,
        )
        schedule = (proc.stdout or "").strip()
        if proc.returncode == 0:
            _ok(f"Bootstrap reconcile CronJob present (schedule={schedule or 'unknown'})")
        else:
            _warn("Bootstrap reconcile CronJob present")

    def _check_sab_defaults(self) -> None:
        checks = [
            (
                "SAB download_dir defaults to /data/usenet/incomplete",
                "grep -Eq '^download_dir[[:space:]]*= /data/usenet/incomplete$' /config/sabnzbd.ini",
            ),
            (
                "SAB complete_dir defaults to /data/usenet/completed",
                "grep -Eq '^complete_dir[[:space:]]*= /data/usenet/completed$' /config/sabnzbd.ini",
            ),
        ]
        for label, shell in checks:
            proc = run_command(
                self._ns_cmd(["exec", "deploy/sabnzbd", "--", "sh", "-lc", shell]),
                check=False,
            )
            if proc.returncode == 0:
                _ok(label)
            else:
                _warn(label)

    def _print_media_counts(self) -> None:
        print("\nCurrent media file counts from Jellyfin pod mounts")
        proc = run_command(
            self._ns_cmd(
                [
                    "exec",
                    "deploy/jellyfin",
                    "--",
                    "sh",
                    "-lc",
                    """
for d in /media/movies /media/tv /media/music /media/books; do
  c=$(find "$d" -type f 2>/dev/null | wc -l | tr -d " ")
  printf "%s -> %s files\\n" "$d" "$c"
done
""",
                ]
            ),
            check=False,
        )
        sys.stdout.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        print(
            """
Interpretation:
- 0 files means the automation wiring can still be healthy, but no media has been imported yet.
- To see content in Jellyfin, request items in Jellyseerr/Arr and wait for download + CDH import.
""".strip()
        )

    def run(self) -> int:
        ns_probe = run_command(self._ns_cmd(["get", "pods"]), check=False)
        if ns_probe.returncode != 0:
            raise ConfigError(f"Namespace '{self.cfg.namespace}' is not reachable")

        boot_log = self._kube_stdout(["logs", "job/media-stack-bootstrap", "--tail=500"])
        if not boot_log.strip():
            raise MediaStackError("No bootstrap logs found. Run: bash scripts/run-bootstrap-job.sh")
        self.bootstrap_log = boot_log

        print(f"Namespace: {self.cfg.namespace}")
        sys.stdout.write(ns_probe.stdout or "")

        print("\n[OK] Flow checks from latest bootstrap log")
        for app in ("Sonarr", "Radarr", "Lidarr", "Readarr"):
            self._check_log(rf"Prowlarr: updated application link for {app}", f"{app} <- Prowlarr app link")
            self._check_log(rf"{app}: (updated|created|reconciled existing named) qBittorrent download client", f"{app} -> qBittorrent client wired")
            self._check_log(rf"{app}: (updated|created|reconciled existing named) SABnzbd download client", f"{app} -> SABnzbd client wired", optional=True)
            self._check_log(rf"{app}: remote path mapping (created|updated|already set)", f"{app} SAB remote path mappings reconciled", optional=True)
            self._check_log(rf"{app}: discovery list reconcile complete", f"{app} discovery lists reconciled", optional=True)
            self._check_log(rf"{app}: (updated media management|media management already set).*hardlinks=True", f"{app} hardlinks policy enforced")
            self._check_log(rf"{app}: (updated download handling|download handling already set)", f"{app} CDH enabled")
            self._check_log(rf"{app}: (updated download handling|download handling already set).*removeFailed=True.*autoRedownloadFailed=True", f"{app} self-healing failed-download retry enabled")

        self._check_log(r"Jellyseerr: configured Jellyfin connection", "Jellyseerr -> Jellyfin wired")
        self._check_log(r"Jellyseerr: (updated|created|existing) (Radarr|Sonarr)", "Jellyseerr -> Arr mappings wired")
        self._check_log(r"Jellyfin libraries: reconcile complete", "Jellyfin libraries reconciled")
        self._check_log(r"Jellyfin plugins: reconcile complete", "Jellyfin plugins reconciled")
        self._check_log(r"Jellyfin Live TV: reconcile complete", "Jellyfin Live TV reconciled")
        self._check_log(r"Jellyfin playback: reconcile complete", "Jellyfin playback defaults reconciled", optional=True)
        self._check_log(r"Jellyfin home rails: reconcile complete", "Jellyfin curated home rails reconciled", optional=True)
        self._check_log(r"Homepage: (wrote services config|services config already up-to-date)", "Homepage services config reconciled", optional=True)
        self._check_log(r"Bazarr: (wrote integration config|Sonarr/Radarr integration already matches desired config|Sonarr/Radarr \+ subtitle automation config already matches desired state)", "Bazarr Sonarr/Radarr integration reconciled", optional=True)

        print("\n[OK] Live config checks")
        self._check_cronjob()
        self._check_homepage_hosts()

        print("\n[OK] Writable path checks")
        self._check_writable("radarr", "/media/movies", "Radarr can write /media/movies")
        self._check_writable("radarr", "/data/torrents/completed/movies", "Radarr can write /data/torrents/completed/movies")
        self._check_writable("sonarr", "/media/tv", "Sonarr can write /media/tv")
        self._check_writable("sonarr", "/data/torrents/completed/tv", "Sonarr can write /data/torrents/completed/tv")
        self._check_writable("lidarr", "/media/music", "Lidarr can write /media/music")
        self._check_writable("readarr", "/media/books", "Readarr can write /media/books")
        self._check_writable("qbittorrent", "/data/torrents/completed/tv", "qBittorrent can write category path /data/torrents/completed/tv")
        self._check_writable("sabnzbd", "/data/usenet/completed", "SABnzbd can write /data/usenet/completed")
        self._check_writable("sabnzbd", "/data/usenet/completed/tv", "SABnzbd can write /data/usenet/completed/tv")
        self._check_writable("sabnzbd", "/data/usenet/completed/movies", "SABnzbd can write /data/usenet/completed/movies")
        self._check_writable("sabnzbd", "/data/usenet/completed/music", "SABnzbd can write /data/usenet/completed/music")
        self._check_writable("sabnzbd", "/data/usenet/completed/books", "SABnzbd can write /data/usenet/completed/books")

        print("\n[OK] Arr SAB remote path mapping checks")
        self._check_arr_remote_mapping("sonarr", 8989, "v3", "/config/Downloads/complete", "/data/usenet/completed", "Sonarr has SAB legacy->local remote path mapping")
        self._check_arr_remote_mapping("radarr", 7878, "v3", "/config/Downloads/complete", "/data/usenet/completed", "Radarr has SAB legacy->local remote path mapping")
        self._check_arr_remote_mapping("lidarr", 8686, "v1", "/config/Downloads/complete", "/data/usenet/completed", "Lidarr has SAB legacy->local remote path mapping")
        self._check_arr_remote_mapping("readarr", 8787, "v1", "/config/Downloads/complete", "/data/usenet/completed", "Readarr has SAB legacy->local remote path mapping")

        print("\n[OK] SAB config-as-code checks")
        self._check_sab_defaults()
        print()
        self._print_media_counts()
        return 0


def main(argv: list[str] | None = None) -> int:
    try:
        verifier = FlowVerifier(parse_config(argv))
        return verifier.run()
    except (ConfigError, MediaStackError, OSError, ValueError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
