#!/usr/bin/env python3
"""Run the full media-stack bootstrap Kubernetes job."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any, Callable

from core.exceptions import ConfigError, KubernetesError, MediaStackError
from core.kube import KubectlClient


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


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


@dataclass
class PhaseTracker:
    run_start_epoch: int = field(default_factory=lambda: int(time.time()))
    current_phase: str = ""
    current_phase_start: int = 0
    phase_names: list[str] = field(default_factory=list)
    phase_results: list[str] = field(default_factory=list)
    phase_seconds: list[int] = field(default_factory=list)

    def start(self, phase_name: str) -> None:
        self.current_phase = phase_name
        self.current_phase_start = int(time.time())
        info(f"[PHASE] START: {phase_name}")

    def end(self, result: str = "ok") -> None:
        now = int(time.time())
        if self.current_phase and self.current_phase_start > 0:
            elapsed = now - self.current_phase_start
            self.phase_names.append(self.current_phase)
            self.phase_results.append(result)
            self.phase_seconds.append(elapsed)
            if result == "ok":
                info(f"[PHASE] DONE: {self.current_phase} ({elapsed}s)")
            elif result == "skipped":
                info(f"[PHASE] SKIP: {self.current_phase} ({elapsed}s)")
            else:
                warn(f"[PHASE] FAIL: {self.current_phase} ({elapsed}s)")
        self.current_phase = ""
        self.current_phase_start = 0

    def print_summary(self) -> None:
        total = int(time.time()) - self.run_start_epoch
        info(f"Phase Summary (total {total}s)")
        if not self.phase_names:
            info("  (no phases recorded)")
            return
        for idx, name in enumerate(self.phase_names):
            info(f"  {name} => {self.phase_results[idx]} ({self.phase_seconds[idx]}s)")


class RunBootstrapJobRunner:
    def __init__(self, cfg: RunBootstrapJobConfig, kube: KubectlClient, tracker: PhaseTracker) -> None:
        self.cfg = cfg
        self.kube = kube
        self.tracker = tracker
        self.job_log_file = Path(
            NamedTemporaryFile(
                prefix="media-stack-bootstrap-log.",
                suffix=".log",
                delete=False,
            ).name
        )
        self.job_config_file = Path(
            NamedTemporaryFile(
                prefix="media-stack-bootstrap-config.",
                suffix=".json",
                delete=False,
            ).name
        )

    def run(self) -> int:
        if not self.cfg.config_file.exists():
            raise ConfigError(f"Config file not found: {self.cfg.config_file}")

        info(f"Namespace: {self.cfg.namespace}")
        info(f"Config: {self.cfg.config_file}")
        info(f"Ingress: {self.cfg.ingress_name}")
        info(f"Bootstrap runner image: {self.cfg.bootstrap_runner_image}")
        info(f"Heartbeat interval: {self.cfg.heartbeat_interval}s")
        self.notify("info", f"media-stack bootstrap job started (namespace={self.cfg.namespace})")

        try:
            self._run_phase(
                "Ensure qBittorrent credentials",
                lambda: self._run_script(
                    "ensure-qbit-credentials.sh",
                    env={
                        "NAMESPACE": self.cfg.namespace,
                        "PREPARE_HOST_ROOT": self.cfg.prepare_host_root,
                    },
                ),
                enabled=not self.cfg.skip_qbit_ensure,
            )
            self._run_phase(
                "Ensure SABnzbd API access",
                lambda: self._run_script(
                    "ensure-sabnzbd-api-access.sh",
                    env={"NAMESPACE": self.cfg.namespace},
                ),
                enabled=not self.cfg.skip_sab_ensure,
            )
            self._run_phase("Resolve bootstrap config", self.resolve_bootstrap_config)
            self._run_phase("Ensure bootstrap PVC prerequisites", self.ensure_bootstrap_pvc_prereqs)
            self._run_phase("Prime Arr API keys into secret", self.prime_servarr_api_keys_secret)
            self._run_phase("Prime SAB API key into secret", self.prime_sab_api_key_secret)
            self._run_phase("Update bootstrap ConfigMaps", self.update_bootstrap_configmaps)
            self._run_phase("Recreate bootstrap Job", self.recreate_bootstrap_job)
            self._run_phase("Wait for bootstrap Job completion", self.wait_for_bootstrap_job)
            self._run_phase("Print bootstrap Job logs", self.print_bootstrap_job_logs)

            if self._log_contains("Jellyseerr: settings file bootstrap applied"):
                self._run_phase(
                    "Restart Jellyseerr after file bootstrap",
                    lambda: self.restart_deployment("jellyseerr", timeout_seconds=180),
                )

            if self._log_contains("Homepage: wrote services config"):
                self._run_phase(
                    "Restart Homepage after config sync",
                    lambda: self.restart_deployment_if_exists("homepage", timeout_seconds=180),
                )

            if self._log_contains("Bazarr: wrote integration config"):
                self._run_phase(
                    "Restart Bazarr after config sync",
                    lambda: self.restart_deployment_if_exists("bazarr", timeout_seconds=180),
                )

            self._run_phase(
                "Activate Jellyfin plugins (restart if needed)",
                self.activate_jellyfin_plugins,
            )

            info("Bootstrap job completed.")
            self.tracker.print_summary()
            self.notify("ok", f"media-stack bootstrap job completed (namespace={self.cfg.namespace})")
            return 0
        except Exception:
            self.notify("error", f"media-stack bootstrap job failed (namespace={self.cfg.namespace})")
            raise
        finally:
            self.cleanup()

    def _run_phase(self, phase_name: str, fn: Callable[[], None], *, enabled: bool = True) -> None:
        self.tracker.start(phase_name)
        if not enabled:
            self.tracker.end("skipped")
            return
        try:
            fn()
            self.tracker.end("ok")
        except Exception:
            self.tracker.end("failed")
            raise

    def cleanup(self) -> None:
        for file_path in (self.job_log_file, self.job_config_file):
            try:
                file_path.unlink(missing_ok=True)
            except Exception:
                pass

    def notify(self, status: str, message: str) -> None:
        if not self.cfg.alert_webhook_url:
            return
        payload = json.dumps({"status": status, "message": message}).encode("utf-8")
        request = urllib.request.Request(
            self.cfg.alert_webhook_url,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=8):
                return
        except urllib.error.URLError:
            return

    def _run_script(self, script_name: str, *args: str, env: dict[str, str] | None = None) -> None:
        script_path = self.cfg.root_dir / "scripts" / script_name
        call_env = dict(os.environ)
        if env:
            call_env.update({k: str(v) for k, v in env.items()})
        proc = subprocess.run(
            ["bash", str(script_path), *list(args)],
            cwd=str(self.cfg.root_dir),
            env=call_env,
            check=False,
            text=True,
            capture_output=True,
        )
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{script_name} failed ({proc.returncode}): "
                f"{' '.join(shlex.quote(x) for x in [str(script_path), *args])}"
            )

    def manifest_overrides(self, text: str) -> str:
        out = re.sub(
            r"namespace:\s*media-stack\b",
            f"namespace: {self.cfg.namespace}",
            text,
        )
        out = re.sub(
            r"name:\s*media-stack\s*$",
            f"name: {self.cfg.namespace}",
            out,
            flags=re.MULTILINE,
        )
        out = re.sub(
            r"image:\s*192\.168\.1\.60:30002/library/media-stack-bootstrap-runner:latest",
            f"image: {self.cfg.bootstrap_runner_image}",
            out,
        )
        out = out.replace("/srv/media-stack", self.cfg.prepare_host_root)
        return out

    def ensure_bootstrap_pvc_prereqs(self) -> None:
        storage_manifest = self.cfg.root_dir / "k8s" / "storage-pvc.yaml"
        required = [
            "media-stack-config-jellyfin",
            "media-stack-config-jellyseerr",
            "media-stack-config-sonarr",
            "media-stack-config-radarr",
            "media-stack-config-lidarr",
            "media-stack-config-readarr",
            "media-stack-config-bazarr",
            "media-stack-config-prowlarr",
            "media-stack-config-sabnzbd",
            "media-stack-config-homepage",
            "media-stack-config-maintainerr",
            "media-stack-config-jellyfin-auto-collections",
            "media-stack-data-torrents",
            "media-stack-data-usenet",
            "media-stack-media",
        ]

        if storage_manifest.exists():
            info(f"Ensuring bootstrap PVC prerequisites via {storage_manifest}")
            with TemporaryDirectory(prefix="media-stack-storage-pvc-") as tmpdir:
                patched = Path(tmpdir) / "storage-pvc.yaml"
                patched.write_text(
                    self.manifest_overrides(storage_manifest.read_text(encoding="utf-8")),
                    encoding="utf-8",
                )
                result = self.kube.run(["apply", "-f", str(patched)], check=False)
                if result.stdout.strip():
                    print(result.stdout.rstrip())
                if result.stderr.strip():
                    print(result.stderr.rstrip(), file=sys.stderr)
        else:
            warn(f"PVC manifest not found at {storage_manifest}")

        missing = []
        for pvc in required:
            result = self.kube.run(
                ["-n", self.cfg.namespace, "get", "pvc", pvc],
                check=False,
            )
            if result.returncode != 0:
                missing.append(pvc)

        if missing:
            warn(f"Missing required PVC(s) for bootstrap job: {' '.join(missing)}")
            warn(
                "Apply storage PVCs and retry: "
                f"{' '.join(self.kube.cmd_prefix)} apply -f {self.cfg.root_dir / 'k8s' / 'storage-pvc.yaml'}"
            )
            raise ConfigError("Missing required PVCs for bootstrap job")

        info("Bootstrap PVC prerequisites are present.")

    def _load_json(self, path: Path) -> dict[str, Any]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ConfigError(f"Expected JSON object in {path}")
        return data

    def resolve_bootstrap_config(self) -> None:
        hosts_result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "ingress",
                self.cfg.ingress_name,
                "-o",
                "jsonpath={range .spec.rules[*]}{.host}{'\\n'}{end}",
            ],
            check=False,
        )
        hosts: list[str] = []
        if hosts_result.returncode == 0:
            for line in (hosts_result.stdout or "").splitlines():
                host = line.strip()
                if host:
                    hosts.append(host)
        hosts = sorted(set(hosts))
        hosts_csv = ",".join(hosts)
        if hosts_csv:
            info(f"Injecting homepage hosts from ingress/{self.cfg.ingress_name}: {hosts_csv}")
        else:
            info(
                f"No ingress hosts discovered from ingress/{self.cfg.ingress_name}; "
                "using bootstrap config defaults."
            )

        cfg = self._load_json(self.cfg.config_file)
        if hosts:
            homepage = cfg.get("homepage")
            if not isinstance(homepage, dict):
                homepage = {}
            homepage["enabled"] = True
            homepage["hosts"] = hosts
            cfg["homepage"] = homepage

        self.job_config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        info(f"Resolved job config: {self.job_config_file}")

    def _read_api_key_from_deploy(self, app: str) -> str:
        command = (
            "sed -n 's:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p' /config/config.xml "
            "| head -n1"
        )
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", f"deploy/{app}", "--", "sh", "-c", command],
            check=False,
        )
        if result.returncode != 0:
            return ""
        return (result.stdout or "").replace("\r", "").replace("\n", "").strip()

    def _read_sab_api_key_from_deploy(self) -> str:
        command = (
            "sed -n 's/^[[:space:]]*api_key[[:space:]]*=[[:space:]]*//p' /config/sabnzbd.ini "
            "| head -n1"
        )
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", "deploy/sabnzbd", "--", "sh", "-c", command],
            check=False,
        )
        if result.returncode != 0:
            return ""
        return (result.stdout or "").replace("\r", "").replace("\n", "").strip()

    def _patch_secret_string(self, key_name: str, key_value: str) -> None:
        if not key_name or not key_value:
            return
        payload = json.dumps({"stringData": {key_name: key_value}})
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "patch",
                "secret",
                "media-stack-secrets",
                "--type",
                "merge",
                "-p",
                payload,
            ],
            check=False,
        )
        if result.returncode != 0:
            raise KubernetesError(result.stderr or result.stdout)

    def prime_servarr_api_keys_secret(self) -> None:
        secret_exists = self.kube.run(
            ["-n", self.cfg.namespace, "get", "secret", "media-stack-secrets"],
            check=False,
        ).returncode == 0
        if not secret_exists:
            warn(
                f"Secret {self.cfg.namespace}/media-stack-secrets not found; "
                "skipping Arr API key priming."
            )
            return

        apps = ["sonarr", "radarr", "lidarr", "readarr", "prowlarr"]
        found = 0
        for app in apps:
            key = self._read_api_key_from_deploy(app)
            if not key:
                warn(f"Could not read API key from deploy/{app} yet; continuing.")
                continue
            upper = app.upper()
            self._patch_secret_string(f"{upper}_API_KEY", key)
            if app != "prowlarr":
                self._patch_secret_string(f"UNPACKERR_{upper}_API_KEY", key)
            info(f"Seeded {upper}_API_KEY in media-stack-secrets from deploy/{app}")
            found += 1

        if found == 0:
            warn("No Arr/Prowlarr API keys were discovered from running deployments.")
        else:
            info(f"Primed API keys in secret for {found} app(s).")

    def prime_sab_api_key_secret(self) -> None:
        secret_exists = self.kube.run(
            ["-n", self.cfg.namespace, "get", "secret", "media-stack-secrets"],
            check=False,
        ).returncode == 0
        if not secret_exists:
            warn(
                f"Secret {self.cfg.namespace}/media-stack-secrets not found; "
                "skipping SABnzbd API key priming."
            )
            return

        key = os.environ.get("SABNZBD_API_KEY", "").strip()
        if not key:
            key = self._read_sab_api_key_from_deploy()
        if not key:
            warn("Could not discover SABnzbd API key from env or deploy/sabnzbd; continuing.")
            return

        self._patch_secret_string("SABNZBD_API_KEY", key)
        info("Seeded SABNZBD_API_KEY in media-stack-secrets.")

    def _replace_or_create_yaml(self, yaml_path: Path, kind_name: str) -> None:
        replaced = self.kube.run(
            ["-n", self.cfg.namespace, "replace", "-f", str(yaml_path)],
            check=False,
        )
        if replaced.returncode == 0:
            info(f"{kind_name} replaced")
            return
        created = self.kube.run(
            ["-n", self.cfg.namespace, "create", "-f", str(yaml_path)],
            check=False,
        )
        if created.returncode != 0:
            raise KubernetesError(created.stderr or created.stdout)

    def update_bootstrap_configmaps(self) -> None:
        info("Updating bootstrap config ConfigMap")
        with TemporaryDirectory(prefix="media-stack-bootstrap-config-") as tmpdir:
            configmap_yaml = Path(tmpdir) / "bootstrap-config.yaml"
            generated = self.kube.run(
                [
                    "-n",
                    self.cfg.namespace,
                    "create",
                    "configmap",
                    "media-stack-bootstrap-config",
                    f"--from-file=config.json={self.job_config_file}",
                    "--dry-run=client",
                    "-o",
                    "yaml",
                ]
            )
            configmap_yaml.write_text(generated.stdout, encoding="utf-8")
            self._replace_or_create_yaml(configmap_yaml, "configmap/media-stack-bootstrap-config")

    def recreate_bootstrap_job(self) -> None:
        info("Recreating bootstrap Job")
        self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "delete",
                "job",
                "media-stack-bootstrap",
                "--ignore-not-found",
            ],
            check=False,
        )
        manifest_path = self.cfg.root_dir / "k8s" / "bootstrap-job.yaml"
        with TemporaryDirectory(prefix="media-stack-bootstrap-job-") as tmpdir:
            patched = Path(tmpdir) / "bootstrap-job.yaml"
            patched.write_text(
                self.manifest_overrides(manifest_path.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            result = self.kube.run(["-n", self.cfg.namespace, "apply", "-f", str(patched)], check=False)
            if result.stdout.strip():
                print(result.stdout.rstrip())
            if result.stderr.strip():
                print(result.stderr.rstrip(), file=sys.stderr)
            if result.returncode != 0:
                raise KubernetesError(result.stderr or result.stdout)

    def _get_job(self, job_name: str) -> dict[str, Any] | None:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", "job", job_name, "-o", "json"],
            check=False,
        )
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None

    def _get_pods(self, selector: str) -> list[dict[str, Any]]:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods", "-l", selector, "-o", "json"],
            check=False,
        )
        if result.returncode != 0:
            return []
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return []
        items = payload.get("items")
        return list(items) if isinstance(items, list) else []

    def _describe(self, kind: str, name: str) -> str:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "describe", kind, name],
            check=False,
        )
        return result.stdout or ""

    def _heartbeat(self, job_name: str, selector: str, elapsed: int) -> None:
        info(f"Waiting on job/{job_name} (elapsed {elapsed}s, timeout {self.cfg.timeout_raw})")
        job_table = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "job",
                job_name,
                "-o",
                "custom-columns=NAME:.metadata.name,COMPLETIONS:.status.succeeded,FAILED:.status.failed,ACTIVE:.status.active,AGE:.metadata.creationTimestamp",
                "--no-headers",
            ],
            check=False,
        )
        if job_table.stdout.strip():
            print(job_table.stdout.rstrip())

        pod_table = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "pods",
                "-l",
                selector,
                "-o",
                "custom-columns=NAME:.metadata.name,PHASE:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount",
                "--no-headers",
            ],
            check=False,
        )
        if pod_table.stdout.strip():
            print(pod_table.stdout.rstrip())

    def _pod_schedule_reason_and_message(self, pod: dict[str, Any]) -> tuple[str, str]:
        for condition in pod.get("status", {}).get("conditions", []) or []:
            if condition.get("type") == "PodScheduled":
                return str(condition.get("reason") or ""), str(condition.get("message") or "")
        return "", ""

    def _print_pending_events(self, pod_name: str) -> None:
        describe = self._describe("pod", pod_name)
        if not describe:
            return
        lines = describe.splitlines()
        if "Events:" not in lines:
            return
        idx = lines.index("Events:")
        print("[PENDING] Events:")
        for line in lines[idx + 1 : idx + 16]:
            print(f"[PENDING] {line}")

    def _tail_pod_logs(self, pod_name: str, lines: int = 8) -> None:
        logs = self.kube.run(
            ["-n", self.cfg.namespace, "logs", pod_name, f"--tail={lines}"],
            check=False,
        )
        if logs.stdout.strip():
            for line in logs.stdout.rstrip().splitlines():
                print(f"[JOB] {line}")

    def _print_failure_context(self, job_name: str, selector: str) -> None:
        describe_job = self.kube.run(
            ["-n", self.cfg.namespace, "describe", "job", job_name],
            check=False,
        )
        if describe_job.stdout.strip():
            print(describe_job.stdout.rstrip())
        if describe_job.stderr.strip():
            print(describe_job.stderr.rstrip(), file=sys.stderr)

        pods_wide = self.kube.run(
            ["-n", self.cfg.namespace, "get", "pods", "-l", selector, "-o", "wide"],
            check=False,
        )
        if pods_wide.stdout.strip():
            print(pods_wide.stdout.rstrip())
        if pods_wide.stderr.strip():
            print(pods_wide.stderr.rstrip(), file=sys.stderr)

        pods = self._get_pods(selector)
        if pods:
            pod_name = str(pods[0].get("metadata", {}).get("name") or "")
            if pod_name:
                describe_pod = self.kube.run(
                    ["-n", self.cfg.namespace, "describe", "pod", pod_name],
                    check=False,
                )
                if describe_pod.stdout.strip():
                    print(describe_pod.stdout.rstrip())
                if describe_pod.stderr.strip():
                    print(describe_pod.stderr.rstrip(), file=sys.stderr)

        job_logs = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "logs",
                f"job/{job_name}",
                "--tail=300",
                "--timestamps",
            ],
            check=False,
        )
        if job_logs.stdout.strip():
            print(job_logs.stdout.rstrip())
        if job_logs.stderr.strip():
            print(job_logs.stderr.rstrip(), file=sys.stderr)

    def wait_for_bootstrap_job(self) -> None:
        job_name = "media-stack-bootstrap"
        selector = "app=media-stack-bootstrap"
        start = int(time.time())
        last_heartbeat = -self.cfg.heartbeat_interval
        last_pending_dump = -99999

        while True:
            now = int(time.time())
            elapsed = now - start

            if elapsed - last_heartbeat >= self.cfg.heartbeat_interval:
                self._heartbeat(job_name, selector, elapsed)
                last_heartbeat = elapsed

            job = self._get_job(job_name)
            if not job:
                warn(f"Job {self.cfg.namespace}/{job_name} not found while waiting.")
                raise KubernetesError("Bootstrap job disappeared while waiting")

            status = job.get("status", {}) or {}
            succeeded = int(status.get("succeeded") or 0)
            failed = int(status.get("failed") or 0)
            conditions = status.get("conditions") or []
            complete = any(c.get("type") == "Complete" and c.get("status") == "True" for c in conditions)
            failed_condition = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)
            backoff = any(
                c.get("reason") == "BackoffLimitExceeded" and c.get("status") == "True"
                for c in conditions
            )

            if complete or succeeded >= 1:
                return
            if failed_condition or backoff or failed >= 1:
                warn("Job failed before completion.")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Bootstrap job failed")

            pods = self._get_pods(f"job-name={job_name}")
            pod = pods[0] if pods else None
            if pod:
                pod_name = str(pod.get("metadata", {}).get("name") or "")
                pod_phase = str(pod.get("status", {}).get("phase") or "")
                statuses = pod.get("status", {}).get("containerStatuses") or []
                wait_reason = ""
                wait_message = ""
                if statuses and isinstance(statuses, list):
                    waiting = (statuses[0] or {}).get("state", {}).get("waiting", {}) or {}
                    wait_reason = str(waiting.get("reason") or "")
                    wait_message = str(waiting.get("message") or "")

                if wait_reason in ("ErrImagePull", "ImagePullBackOff"):
                    warn(f"Job pod cannot pull bootstrap runner image ({wait_reason}).")
                    if wait_message:
                        warn(f"Image pull message: {wait_message}")
                    warn("Build/push the runner image and retry: bash scripts/build-bootstrap-runner-image.sh")
                    raise KubernetesError("Bootstrap job image pull failed")

                if pod_phase in ("Failed", "Unknown"):
                    warn("Job failed before completion.")
                    self._print_failure_context(job_name, selector)
                    raise KubernetesError("Bootstrap job pod failed")

                if pod_phase == "Pending":
                    reason, sched_message = self._pod_schedule_reason_and_message(pod)
                    if elapsed - last_pending_dump >= 45:
                        warn(f"Job pod is Pending: {pod_name} (reason={reason or 'unknown'})")
                        if sched_message:
                            warn(f"Job pod scheduling message: {sched_message}")
                        if pod_name:
                            self._print_pending_events(pod_name)
                        last_pending_dump = elapsed
                    if elapsed >= 20 and "persistentvolumeclaim" in sched_message and "not found" in sched_message:
                        warn("Job pod remained Pending because required PVCs are missing.")
                        warn(f"Scheduling message: {sched_message}")
                        raise KubernetesError("Missing required PVCs for bootstrap job")
                    if elapsed >= 120 and any(
                        marker in sched_message
                        for marker in (
                            "persistentvolumeclaim",
                            "unbound immediate PersistentVolumeClaims",
                            "volume node affinity conflict",
                            "Multi-Attach",
                            "didn't match Pod's node affinity",
                        )
                    ):
                        warn(
                            "Job pod remained Pending with a hard scheduling/storage "
                            f"error for {elapsed}s."
                        )
                        warn(f"Scheduling message: {sched_message}")
                        raise KubernetesError("Bootstrap job scheduling failed")
                elif pod_name:
                    self._tail_pod_logs(pod_name, lines=8)

            if elapsed >= self.cfg.timeout_seconds:
                warn(f"Job did not complete within {self.cfg.timeout_raw}.")
                self._print_failure_context(job_name, selector)
                raise KubernetesError("Bootstrap job timed out")

            time.sleep(2)

    def print_bootstrap_job_logs(self) -> None:
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "logs",
                "job/media-stack-bootstrap",
                "--timestamps",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise KubernetesError(result.stderr or result.stdout)
        self.job_log_file.write_text(result.stdout or "", encoding="utf-8")
        lines = (result.stdout or "").splitlines()
        tail = lines[-max(1, self.cfg.job_log_tail_lines) :]
        if tail:
            print("\n".join(tail))

    def _log_contains(self, marker: str) -> bool:
        if not self.job_log_file.exists():
            return False
        try:
            return marker in self.job_log_file.read_text(encoding="utf-8")
        except Exception:
            return False

    def deployment_exists(self, deployment: str) -> bool:
        result = self.kube.run(
            ["-n", self.cfg.namespace, "get", f"deploy/{deployment}"],
            check=False,
        )
        return result.returncode == 0

    def restart_deployment(self, deployment: str, *, timeout_seconds: int) -> None:
        info(f"Restarting deployment/{deployment}.")
        restart = self.kube.run(
            ["-n", self.cfg.namespace, "rollout", "restart", f"deployment/{deployment}"],
            check=False,
        )
        if restart.returncode != 0:
            raise KubernetesError(restart.stderr or restart.stdout)
        status = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "rollout",
                "status",
                f"deployment/{deployment}",
                f"--timeout={timeout_seconds}s",
            ],
            check=False,
        )
        if status.returncode != 0:
            raise KubernetesError(status.stderr or status.stdout)

    def restart_deployment_if_exists(self, deployment: str, *, timeout_seconds: int) -> None:
        if not self.deployment_exists(deployment):
            info(f"deployment/{deployment} not found in namespace/{self.cfg.namespace}; skipping restart.")
            return
        self.restart_deployment(deployment, timeout_seconds=timeout_seconds)

    def _read_secret_key(self, secret: str, key_name: str) -> str:
        result = self.kube.run(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "secret",
                secret,
                f"-o=jsonpath={{.data.{key_name}}}",
            ],
            check=False,
        )
        if result.returncode != 0:
            return ""
        value = (result.stdout or "").strip()
        if not value:
            return ""
        try:
            return base64.b64decode(value).decode("utf-8")
        except Exception:
            return ""

    def activate_jellyfin_plugins(self) -> None:
        if not self.deployment_exists("jellyfin"):
            info(f"deployment/jellyfin not found in namespace/{self.cfg.namespace}; skipping restart check.")
            return

        jellyfin_api_key = self._read_secret_key("media-stack-secrets", "JELLYFIN_API_KEY")
        if not jellyfin_api_key:
            info("JELLYFIN_API_KEY not found in secret; skipping Jellyfin plugin activation restart.")
            return

        plugins_url = f"http://localhost:8096/Plugins?api_key={jellyfin_api_key}"
        command = f"curl -fsS {shlex.quote(plugins_url)}"
        result = self.kube.run(
            ["-n", self.cfg.namespace, "exec", "deploy/jellyfin", "--", "sh", "-lc", command],
            check=False,
        )
        if result.returncode != 0:
            warn("Could not query Jellyfin plugins; skipping plugin activation restart check.")
            return

        restart_count = 0
        try:
            payload = json.loads(result.stdout or "[]")
            if isinstance(payload, list):
                restart_count = sum(
                    1 for item in payload if isinstance(item, dict) and item.get("Status") == "Restart"
                )
        except json.JSONDecodeError:
            restart_count = (result.stdout or "").count('"Status":"Restart"')

        if restart_count > 0:
            info(
                f"Detected {restart_count} Jellyfin plugin(s) pending restart; "
                "restarting deployment/jellyfin."
            )
            self.restart_deployment("jellyfin", timeout_seconds=300)
            info("Jellyfin restarted to activate pending plugin changes.")
        else:
            info("No Jellyfin plugin restart pending.")


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
        help="Skip qBittorrent ensure phase.",
    )
    parser.add_argument(
        "--skip-sab-ensure",
        action="store_true",
        default=env_bool("SKIP_SAB_ENSURE", False),
        help="Skip SABnzbd ensure phase.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    root_dir = Path(__file__).resolve().parents[2]
    parser = build_parser(root_dir)
    args = parser.parse_args(argv)

    cfg = RunBootstrapJobConfig(
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

    runner = RunBootstrapJobRunner(cfg=cfg, kube=KubectlClient.from_environment(), tracker=PhaseTracker())
    return runner.run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MediaStackError as exc:
        err(str(exc))
        raise SystemExit(1)
    except KeyboardInterrupt:
        err("Interrupted.")
        raise SystemExit(130)
