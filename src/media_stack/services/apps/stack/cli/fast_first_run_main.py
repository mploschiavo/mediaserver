#!/usr/bin/env python3
"""Print fast first-run setup flow and helper commands."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

from media_stack.core.cli_common import kube_cmd


@dataclass(frozen=True)
class FastFirstRunConfig:
    node_ip: str
    namespace: str
    kubectl_cmd: str


def parse_config(argv: list[str] | None = None) -> FastFirstRunConfig:
    parser = argparse.ArgumentParser(
        prog="bin/fast-first-run.sh",
        description=(
            "Print fastest first-run wiring flow for media-stack "
            "(URLs, setup order, API key helper commands)."
        ),
    )
    parser.add_argument("node_ip")
    args = parser.parse_args(argv)
    node_ip = str(args.node_ip or "").strip()
    namespace = os.environ.get("NAMESPACE", "media-stack").strip() or "media-stack"

    try:
        kubectl = " ".join(kube_cmd())
    except Exception:
        print(
            "[WARN] Neither microk8s nor kubectl found. API key fetch commands will be shown but not tested.",
            file=sys.stderr,
        )
        kubectl = "kubectl"
    return FastFirstRunConfig(node_ip=node_ip, namespace=namespace, kubectl_cmd=kubectl)


def _print_api_key_cmd(app: str, port: int, kubectl_cmd: str, namespace: str) -> None:
    print(f"\n{app} API key:")
    print(
        f"  {kubectl_cmd} -n {namespace} exec deploy/{app} -- sh -c "
        "\"sed -n 's:.*<ApiKey>\\\\(.*\\\\)</ApiKey>.*:\\\\1:p' /config/config.xml | head -n1\""
    )
    print(f"  URL: http://{app}:{port}")


def run(cfg: FastFirstRunConfig) -> int:
    print(
        f"""Fast first-run for media-stack
==============================

Use these URLs in your browser:
- Homepage:   http://homepage.local
- Jellyfin:   http://jellyfin.local
- Jellyseerr: http://jellyseerr.local
- Prowlarr:   http://prowlarr.local
- qBittorrent:http://qbittorrent.local
- Maintainerr:http://maintainerr.local

If .local does not resolve on your device, add hosts entry:
{cfg.node_ip} homepage.local jellyfin.local jellyseerr.local sonarr.local radarr.local lidarr.local readarr.local bazarr.local prowlarr.local qbittorrent.local sabnzbd.local maintainerr.local tautulli.local

Recommended fastest order (about 15-25 minutes):
1) Full zero-to-usable run (recommended):
   - bash bin/install.sh --profile full --node-ip {cfg.node_ip}
   - bash bin/deploy-stack.sh {cfg.node_ip}
2) Run full bootstrap automation (if namespace already exists):
   - bash bin/set-qbit-secret.sh   # defaults to admin/<namespace>
   - bash bin/ensure-jellyfin-bootstrap.sh   # auto-discovers/updates Jellyfin API key in secret
   - bash bin/bootstrap-all.sh
   - (this wires Arr + Prowlarr + qBittorrent clients/categories + Jellyseerr Sonarr/Radarr + Unpackerr keys)
3) qBittorrent:
   - verify login with secret credentials
   - categories are auto-managed: tv, movies, music, books
4) Jellyfin:
   - startup wizard/admin are bootstrap-managed from stack secret
   - add/verify libraries under /media/*
5) Prowlarr:
   - add indexers (only trusted/permitted sources)
   - app connections are bootstrap-managed for Sonarr/Radarr/Lidarr/Readarr
6) Sonarr/Radarr/Lidarr/Readarr:
   - root folders are bootstrap-managed (/media/tv, /media/movies, /media/music, /media/books)
   - qBittorrent download client is bootstrap-managed (http://qbittorrent:8080)
7) Jellyseerr:
   - Sonarr + Radarr + Jellyfin are bootstrap-configured
   - local admin account is seeded from STACK_ADMIN credentials
8) Bazarr:
   - set subtitle providers/languages
9) Optional apps:
   - SABnzbd / Plex / Tautulli / FlareSolverr as needed
10) Unpackerr:
   - enable only after Arr API keys are configured

API key helpers (run on this host):
"""
    )
    _print_api_key_cmd("sonarr", 8989, cfg.kubectl_cmd, cfg.namespace)
    _print_api_key_cmd("radarr", 7878, cfg.kubectl_cmd, cfg.namespace)
    _print_api_key_cmd("lidarr", 8686, cfg.kubectl_cmd, cfg.namespace)
    _print_api_key_cmd("readarr", 8787, cfg.kubectl_cmd, cfg.namespace)
    _print_api_key_cmd("prowlarr", 9696, cfg.kubectl_cmd, cfg.namespace)
    print(
        """

Optional sanity checks:
  bash bin/microk8s-smoke-test.sh <NODE_IP>
  microk8s kubectl -n media-stack get pods
""".rstrip()
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_config(argv))
    except Exception as exc:  # pragma: no cover - defensive CLI guard
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
