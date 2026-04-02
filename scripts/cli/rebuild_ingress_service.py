"""Ingress class selection and patching helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
WarnFn = Callable[[str], None]
RunKubeFn = Callable[..., object]


@dataclass(frozen=True)
class RebuildIngressConfig:
    namespace: str
    ingress_class: str
    internet_exposed: str = "0"
    route_strategy: str = "subdomain"
    app_gateway_host: str = ""
    app_path_prefix: str = "/app"
    media_server_direct_host: str = ""
    auth_provider: str = "none"
    auth_middleware: str = ""


@dataclass
class RebuildIngressService:
    cfg: RebuildIngressConfig
    info: InfoFn
    warn: WarnFn
    run_kube: RunKubeFn

    def _is_truthy(self, value: str) -> bool:
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}

    def _annotation_patch_payload(self) -> dict[str, object]:
        annotations: dict[str, str] = {}
        route_strategy = str(self.cfg.route_strategy or "subdomain").strip().lower() or "subdomain"
        annotations["media-stack.io/route-strategy"] = route_strategy

        gateway_host = str(self.cfg.app_gateway_host or "").strip()
        if gateway_host:
            annotations["media-stack.io/app-gateway-host"] = gateway_host

        app_path_prefix = str(self.cfg.app_path_prefix or "").strip()
        if app_path_prefix:
            annotations["media-stack.io/app-path-prefix"] = app_path_prefix

        media_server_direct_host = str(self.cfg.media_server_direct_host or "").strip()
        if media_server_direct_host:
            annotations["media-stack.io/media-server-direct-host"] = media_server_direct_host

        auth_provider = str(self.cfg.auth_provider or "none").strip().lower() or "none"
        annotations["media-stack.io/auth-provider"] = auth_provider
        auth_middleware = str(self.cfg.auth_middleware or "").strip()
        if auth_middleware:
            annotations["media-stack.io/auth-middleware"] = auth_middleware

        internet_exposed = "true" if self._is_truthy(self.cfg.internet_exposed) else "false"
        annotations["media-stack.io/internet-exposed"] = internet_exposed
        return {"metadata": {"annotations": annotations}}

    def reconcile_edge_routing_and_auth(self) -> bool:
        payload = self._annotation_patch_payload()
        annotations = (payload.get("metadata") or {}).get("annotations") or {}
        if not isinstance(annotations, dict) or not annotations:
            return False
        self.info("Reconciling ingress edge/auth annotations (provider-agnostic contract).")
        self.run_kube(
            [
                "-n",
                self.cfg.namespace,
                "patch",
                "ingress",
                "media-stack-ingress",
                "--type",
                "merge",
                "-p",
                json.dumps(payload),
            ],
            check=False,
        )
        return True

    def pick_ingress_class(self) -> str:
        if self.cfg.ingress_class != "auto":
            return self.cfg.ingress_class

        proc = self.run_kube(["get", "ingressclass"], check=False)
        classes = sorted({x.strip() for x in (proc.stdout or "").splitlines() if x.strip()})
        for target in ("public", "nginx"):
            if target in classes:
                return target
        return classes[0] if classes else ""

    def patch_ingress_class(self) -> bool:
        desired_class = self.pick_ingress_class()
        if not desired_class:
            self.warn("No ingress classes discovered; skipping ingress patch.")
            return False

        current = self.run_kube(
            [
                "-n",
                self.cfg.namespace,
                "get",
                "ingress",
                "media-stack-ingress",
                "-o",
                "json",
            ],
            check=False,
        )
        current_class = ""
        if current.returncode == 0 and current.stdout.strip():
            try:
                payload = json.loads(current.stdout)
                current_class = str(
                    (payload.get("spec") or {}).get("ingressClassName") or ""
                ).strip()
            except Exception:
                current_class = ""
        if current_class == desired_class:
            self.info(f"Ingress class already set to '{desired_class}'")
            self.reconcile_edge_routing_and_auth()
            return True

        self.info(
            f"Patching ingress class to '{desired_class}' "
            f"(current: '{current_class if current_class else '<empty>'}')"
        )
        self.run_kube(
            [
                "-n",
                self.cfg.namespace,
                "patch",
                "ingress",
                "media-stack-ingress",
                "--type",
                "merge",
                "-p",
                json.dumps({"spec": {"ingressClassName": desired_class}}),
            ],
        )
        self.reconcile_edge_routing_and_auth()
        return True
