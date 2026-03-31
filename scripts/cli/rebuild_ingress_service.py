"""Ingress class selection and patching helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable

InfoFn = Callable[[str], None]
WarnFn = Callable[[str], None]
RunScriptFn = Callable[..., None]


@dataclass(frozen=True)
class RebuildIngressConfig:
    namespace: str
    ingress_class: str
    kubectl: list[str]


@dataclass
class RebuildIngressService:
    cfg: RebuildIngressConfig
    info: InfoFn
    warn: WarnFn
    run_script: RunScriptFn

    def pick_ingress_class(self) -> str:
        if self.cfg.ingress_class != "auto":
            return self.cfg.ingress_class

        proc = subprocess.run(
            [
                *self.cfg.kubectl,
                "get",
                "ingressclass",
                "-o",
                "jsonpath={range .items[*]}{.metadata.name}{'\\n'}{end}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        classes = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
        for target in ("public", "nginx"):
            if target in classes:
                return target
        return classes[0] if classes else ""

    def patch_ingress_class(self) -> bool:
        desired_class = self.pick_ingress_class()
        if not desired_class:
            self.warn("No ingress classes discovered; skipping ingress patch.")
            return False

        current = subprocess.run(
            [
                *self.cfg.kubectl,
                "-n",
                self.cfg.namespace,
                "get",
                "ingress",
                "media-stack-ingress",
                "-o",
                "jsonpath={.spec.ingressClassName}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        current_class = (current.stdout or "").strip()
        if current_class == desired_class:
            self.info(f"Ingress class already set to '{desired_class}'")
            return True

        self.info(
            f"Patching ingress class to '{desired_class}' "
            f"(current: '{current_class if current_class else '<empty>'}')"
        )
        self.run_script(
            "microk8s-patch-ingress-class.sh",
            desired_class,
            env={"NAMESPACE": self.cfg.namespace},
        )
        return True
