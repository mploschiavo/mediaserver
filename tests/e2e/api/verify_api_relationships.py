#!/usr/bin/env python3
"""Cluster API-level integration verification for media-stack relationships."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    ok: bool
    message: str


class Runner:
    def __init__(self, namespace: str, config_path: Path, timeout: int):
        self.namespace = namespace
        self.config_path = config_path
        self.timeout = timeout
        self.failures: list[str] = []
        self.kubectl = self._detect_kubectl()
        self.pod_name = f"media-stack-api-e2e-{int(time.time())}"
        self.cfg = self._load_json(config_path)

    def _detect_kubectl(self) -> list[str]:
        if shutil.which("microk8s"):
            return ["microk8s", "kubectl"]
        if shutil.which("kubectl"):
            return ["kubectl"]
        raise RuntimeError("Neither microk8s nor kubectl is available in PATH.")

    def _load_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _run(
        self, args: list[str], *, check: bool = True, text: bool = True
    ) -> subprocess.CompletedProcess:
        proc = subprocess.run(args, capture_output=True, text=text)
        if check and proc.returncode != 0:
            cmd = " ".join(shlex.quote(x) for x in args)
            raise RuntimeError(f"Command failed ({proc.returncode}): {cmd}\n{proc.stderr.strip()}")
        return proc

    def _k(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self._run([*self.kubectl, *args], check=check)

    def _kns(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return self._run([*self.kubectl, "-n", self.namespace, *args], check=check)

    def _print(self, line: str) -> None:
        print(line, flush=True)

    def _ok(self, message: str) -> None:
        self._print(f"[OK] {message}")

    def _warn(self, message: str) -> None:
        self._print(f"[WARN] {message}")

    def _fail(self, message: str) -> None:
        self.failures.append(message)
        self._print(f"[ERR] {message}")

    def setup(self) -> None:
        self._print(f"Using kubectl command: {' '.join(self.kubectl)}")
        self._print(f"Namespace: {self.namespace}")

        self._kns("get", "pods")
        self._kns(
            "run",
            self.pod_name,
            "--image=curlimages/curl:8.10.1",
            "--restart=Never",
            "--command",
            "--",
            "sh",
            "-lc",
            "sleep 3600",
        )
        self._kns(
            "wait", "--for=condition=Ready", f"pod/{self.pod_name}", f"--timeout={self.timeout}s"
        )
        self._ok(f"Created helper pod {self.pod_name}")

    def cleanup(self) -> None:
        self._kns("delete", "pod", self.pod_name, "--ignore-not-found", check=False)

    def kexec(self, *cmd: str, check: bool = True) -> subprocess.CompletedProcess:
        return self._kns("exec", self.pod_name, "--", *cmd, check=check)

    def curl_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        args = ["curl", "-fsS", "--connect-timeout", "8", "--max-time", "30"]
        for key, value in (headers or {}).items():
            args.extend(["-H", f"{key}: {value}"])
        args.append(url)
        proc = self.kexec(*args)
        return proc.stdout.strip()

    def curl_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        data = self.curl_text(url, headers=headers)
        return json.loads(data)

    def read_arr_api_key(self, deploy: str) -> str:
        cmd = "sed -n 's:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p' /config/config.xml | head -n1"
        proc = self._kns("exec", f"deploy/{deploy}", "--", "sh", "-lc", cmd)
        key = proc.stdout.strip()
        if not key:
            raise RuntimeError(f"Missing API key in {deploy} config.xml")
        return key

    def detect_arr_api_base(self, service: str, port: int, api_key: str) -> str:
        for base in ("/api/v3", "/api/v1", "/api"):
            url = f"http://{service}:{port}{base}/ping?apikey={api_key}"
            try:
                body = self.curl_text(url)
                if body is not None:
                    return base
            except Exception:
                continue
        raise RuntimeError(f"Could not detect API base for {service}:{port}")

    def get_secret(self, name: str = "media-stack-secrets") -> dict[str, Any]:
        proc = self._kns("get", "secret", name, "-o", "json")
        return json.loads(proc.stdout)

    @staticmethod
    def secret_value(secret_obj: dict[str, Any], key: str) -> str:
        encoded = ((secret_obj.get("data") or {}).get(key) or "").strip()
        if not encoded:
            return ""
        return base64.b64decode(encoded).decode("utf-8", errors="replace")

    def run_checks(self) -> None:
        arr_expected_roots = {
            str(item.get("implementation") or ""): str(item.get("root_folder") or "")
            for item in (self.cfg.get("arr_apps") or [])
            if isinstance(item, dict)
        }

        prowlarr_key = self.read_arr_api_key("prowlarr")
        prowlarr_base = self.detect_arr_api_base("prowlarr", 9696, prowlarr_key)
        apps_payload = self.curl_json(
            f"http://prowlarr:9696{prowlarr_base}/applications?apikey={prowlarr_key}"
        )
        if not isinstance(apps_payload, list):
            raise RuntimeError("Prowlarr applications response was not a list")

        required_impls = {"Sonarr", "Radarr", "Lidarr", "Readarr"}
        linked_impls = {
            str(item.get("implementation") or "")
            for item in apps_payload
            if isinstance(item, dict) and bool(item.get("enable", True))
        }
        missing = sorted(required_impls - linked_impls)
        if missing:
            self._fail(f"Prowlarr application links missing implementations: {', '.join(missing)}")
        else:
            self._ok("Prowlarr application links include Sonarr/Radarr/Lidarr/Readarr")

        arr_matrix = [
            ("Sonarr", "sonarr", 8989),
            ("Radarr", "radarr", 7878),
            ("Lidarr", "lidarr", 8686),
            ("Readarr", "readarr", 8787),
        ]
        for impl, service, port in arr_matrix:
            key = self.read_arr_api_key(service)
            base = self.detect_arr_api_base(service, port, key)

            clients = self.curl_json(f"http://{service}:{port}{base}/downloadclient?apikey={key}")
            if not isinstance(clients, list):
                self._fail(f"{impl}: downloadclient API did not return a list")
                continue

            impls = {
                (str(item.get("implementation") or "").lower())
                for item in clients
                if isinstance(item, dict)
            }
            has_qbit = any("qbittorrent" in value for value in impls)
            has_sab = any("sab" in value for value in impls)
            if has_qbit and has_sab:
                self._ok(f"{impl}: qBittorrent + SABnzbd download clients configured")
            else:
                self._fail(
                    f"{impl}: missing download client wiring " f"(qB={has_qbit}, SAB={has_sab})"
                )

            mappings = self.curl_json(
                f"http://{service}:{port}{base}/remotepathmapping?apikey={key}"
            )
            if not isinstance(mappings, list):
                self._fail(f"{impl}: remote path mapping API did not return a list")
            else:
                has_sab_mapping = any(
                    isinstance(item, dict)
                    and str(item.get("host") or "").strip().lower() == "sabnzbd"
                    for item in mappings
                )
                if has_sab_mapping:
                    self._ok(f"{impl}: SABnzbd remote path mapping exists")
                else:
                    self._fail(f"{impl}: missing SABnzbd remote path mapping")

            roots = self.curl_json(f"http://{service}:{port}{base}/rootfolder?apikey={key}")
            expected_root = arr_expected_roots.get(impl, "")
            if isinstance(roots, list) and expected_root:
                root_paths = {
                    str(item.get("path") or "") for item in roots if isinstance(item, dict)
                }
                if expected_root in root_paths:
                    self._ok(f"{impl}: expected root folder exists ({expected_root})")
                else:
                    self._fail(f"{impl}: expected root folder missing ({expected_root})")

            queue = self.curl_json(
                f"http://{service}:{port}{base}/queue?page=1&pageSize=20&apikey={key}"
            )
            if isinstance(queue, dict) and any(k in queue for k in ("records", "Records")):
                self._ok(f"{impl}: queue API reachable")
            elif isinstance(queue, list):
                self._ok(f"{impl}: queue API reachable")
            else:
                self._warn(f"{impl}: queue API returned unexpected payload shape")

            if impl in {"Lidarr", "Readarr"}:
                import_lists = self.curl_json(
                    f"http://{service}:{port}{base}/importlist?apikey={key}"
                )
                if isinstance(import_lists, list) and import_lists:
                    self._ok(f"{impl}: import lists configured ({len(import_lists)})")
                else:
                    self._fail(f"{impl}: import lists are not configured")

        # Bazarr flow verification (config + API status)
        bazarr_cfg_proc = self._kns(
            "exec", "deploy/bazarr", "--", "sh", "-lc", "cat /config/config/config.yaml"
        )
        bazarr_cfg = bazarr_cfg_proc.stdout
        if re.search(r"(?m)^\s*use_sonarr:\s*true\s*$", bazarr_cfg) and re.search(
            r"(?m)^\s*use_radarr:\s*true\s*$", bazarr_cfg
        ):
            self._ok("Bazarr config enables Sonarr/Radarr integration")
        else:
            self._fail("Bazarr config missing Sonarr/Radarr integration toggles")
        if re.search(r"(?m)^\s*ip:\s*'?sonarr'?\s*$", bazarr_cfg) and re.search(
            r"(?m)^\s*ip:\s*'?radarr'?\s*$", bazarr_cfg
        ):
            self._ok("Bazarr points at in-cluster Sonarr/Radarr hosts")
        else:
            self._fail("Bazarr host mappings to Sonarr/Radarr are missing")

        bazarr_api_key_match = re.search(r"(?m)^\s*apikey:\s*'?([^'\n]+)'?\s*$", bazarr_cfg)
        bazarr_api_key = bazarr_api_key_match.group(1).strip() if bazarr_api_key_match else ""
        if bazarr_api_key:
            try:
                status_payload = self.curl_json(
                    "http://bazarr:6767/api/system/status",
                    headers={"X-API-KEY": bazarr_api_key},
                )
                if isinstance(status_payload, (dict, list)):
                    self._ok("Bazarr API reachable with configured API key")
            except Exception as exc:
                self._warn(f"Bazarr API status check failed: {exc}")

        # Jellyseerr API checks
        settings_proc = self._kns(
            "exec", "deploy/jellyseerr", "--", "sh", "-lc", "cat /app/config/settings.json"
        )
        settings = json.loads(settings_proc.stdout)
        jellyseerr_key = str(((settings.get("main") or {}).get("apiKey") or "").strip())
        if jellyseerr_key:
            headers = {"X-Api-Key": jellyseerr_key}
            sonarr_settings = self.curl_json(
                "http://jellyseerr:5055/api/v1/settings/sonarr", headers=headers
            )
            radarr_settings = self.curl_json(
                "http://jellyseerr:5055/api/v1/settings/radarr", headers=headers
            )
            jellyfin_settings = self.curl_json(
                "http://jellyseerr:5055/api/v1/settings/jellyfin", headers=headers
            )

            if isinstance(sonarr_settings, list) and sonarr_settings:
                self._ok("Jellyseerr API: Sonarr mapping present")
            else:
                self._fail("Jellyseerr API: Sonarr mapping missing")
            if isinstance(radarr_settings, list) and radarr_settings:
                self._ok("Jellyseerr API: Radarr mapping present")
            else:
                self._fail("Jellyseerr API: Radarr mapping missing")
            if isinstance(jellyfin_settings, dict) and jellyfin_settings.get("hostname"):
                self._ok("Jellyseerr API: Jellyfin mapping present")
            else:
                self._fail("Jellyseerr API: Jellyfin mapping missing")
        else:
            self._fail("Jellyseerr API key missing in settings.json")

        # qBittorrent + SAB API checks
        secret = self.get_secret("media-stack-secrets")
        qbit_user = self.secret_value(secret, "QBITTORRENT_USERNAME")
        qbit_pass = self.secret_value(secret, "QBITTORRENT_PASSWORD")
        sab_key = self.secret_value(secret, "SABNZBD_API_KEY")

        if qbit_user and qbit_pass:
            login_cmd = (
                "curl -fsS -c /tmp/qb.cookies -d "
                + shlex.quote(f"username={qbit_user}&password={qbit_pass}")
                + " http://qbittorrent:8080/api/v2/auth/login"
            )
            login = self.kexec("sh", "-lc", login_cmd, check=False)
            if "Ok." in (login.stdout or ""):
                cats_cmd = "curl -fsS -b /tmp/qb.cookies http://qbittorrent:8080/api/v2/torrents/categories"
                categories = json.loads(self.kexec("sh", "-lc", cats_cmd).stdout)
                expected = {"tv", "movies", "music", "books"}
                if expected.issubset(set(categories.keys())):
                    self._ok("qBittorrent API: expected categories exist")
                else:
                    self._fail(
                        "qBittorrent API: missing categories "
                        f"{sorted(expected - set(categories.keys()))}"
                    )
            else:
                self._fail("qBittorrent API login failed with secret credentials")
        else:
            self._fail("qBittorrent credentials missing in media-stack-secrets")

        if sab_key:
            try:
                sab_queue = self.curl_json(
                    f"http://sabnzbd:8080/api?mode=queue&output=json&apikey={sab_key}"
                )
                if isinstance(sab_queue, dict):
                    self._ok("SABnzbd API reachable with configured API key")
                else:
                    self._fail("SABnzbd queue API returned unexpected payload")
            except Exception as exc:
                self._fail(f"SABnzbd API request failed: {exc}")
        else:
            self._warn("SABNZBD_API_KEY missing in secret; skipping SAB API verification")

    def finish(self) -> int:
        if self.failures:
            self._print("\n[ERR] API e2e verification failed:")
            for item in self.failures:
                self._print(f"  - {item}")
            return 1
        self._print("\n[OK] API e2e verification passed.")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="API e2e relationship checks")
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument(
        "--config",
        default="bootstrap/media-stack.bootstrap.json",
        help="Bootstrap config file used for expected roots",
    )
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    runner = Runner(args.namespace, Path(args.config), args.timeout)
    try:
        runner.setup()
        runner.run_checks()
        return runner.finish()
    finally:
        runner.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
