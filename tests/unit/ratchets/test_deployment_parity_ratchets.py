"""Batch 4 ratchets shipped in v1.0.119.

Integration / cross-artifact ratchets — these check invariants
that hold ACROSS files (compose ↔ k8s ↔ contracts ↔ profiles).
The whole-product correctness checks the per-file ratchets miss.

Bug classes covered:

  C    compose ↔ k8s service parity (every default-profile compose
       service has a k8s deployment; image-tagged controller refs
       agree across all manifests)
  C2   k8s Service.targetPort matches ServiceDef.port from the
       registry (one source of truth across compose, k8s, controller)
  E2   contract job ``requires:`` graph is a DAG (no cycle would
       deadlock the bootstrap planner — caught silently before the
       prereq DAG was added in v1.0.105)
  E3   technology_bindings in profile YAMLs resolve to a real
       contract id or plugin.technology value
  K3   k8s/profiles/* kustomization.yaml references resolve
       (resources + patches files exist on disk)
  V1   media-stack-controller image tags across compose/k8s/profiles
       agree with the canonical VERSION file
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "media_stack"
sys.path.insert(0, str(ROOT / "src"))


# ---------------------------------------------------------------------------
# C — compose ↔ k8s deployment parity
# ---------------------------------------------------------------------------
class ComposeKubernetesDeploymentParity(unittest.TestCase):
    """Every compose service that runs in the default profile must
    have a corresponding k8s Deployment / StatefulSet / CronJob.
    Catches "I added a service to compose but forgot the k8s
    manifest" drift."""

    _COMPOSE_ONLY_OK = {
        # Init containers — not standalone deployments.
        "envoy-config-init",
        "init-permissions",
        # Profile-gated alternatives — operator opts in via
        # COMPOSE_PROFILES, not part of the default install.
        "jellyfin-nvidia",   # nvidia profile (skipped on Intel-only hosts)
        "traefik",           # traefik profile (envoy is the default)
    }

    def test_every_compose_service_has_kube_resource(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        compose = _yaml.safe_load(
            (ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
        )
        compose_svcs = set((compose.get("services") or {}).keys())

        k8s_resources: set[str] = set()
        for f in (ROOT / "k8s").rglob("*.yaml"):
            if "kustomization" in f.name:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for doc in _yaml.safe_load_all(text):
                if not isinstance(doc, dict):
                    continue
                if doc.get("kind") in ("Deployment", "StatefulSet", "CronJob"):
                    name = (doc.get("metadata") or {}).get("name", "")
                    if name:
                        k8s_resources.add(name)

        check = compose_svcs - self._COMPOSE_ONLY_OK
        missing: list[str] = []
        for svc in sorted(check):
            if svc in k8s_resources:
                continue
            if f"media-stack-{svc}" in k8s_resources:
                continue
            missing.append(svc)
        self.assertFalse(
            missing,
            f"Compose services with no k8s deployment "
            f"({len(missing)} of {len(check)} checked):\n  - "
            + "\n  - ".join(missing),
        )


# ---------------------------------------------------------------------------
# C2 — k8s Service port ↔ registry port
# ---------------------------------------------------------------------------
class KubernetesRegistryPortParity(unittest.TestCase):
    """Every k8s Service whose name matches a registry ID must
    target the registry's ``port``. Catches "I changed port in the
    registry but the k8s Service still targets the old number"
    drift."""

    _ALLOWED_DRIFT = {
        # Envoy serves on multiple ports; registry pins the admin
        # port (9901) used by the controller's /stats probe, while
        # the k8s Service targets the public HTTP listener (8880).
        "envoy",
    }

    def test_kube_service_target_port_matches_registry(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        from media_stack.api.services.registry import SERVICE_MAP
        drift: list[str] = []
        for f in (ROOT / "k8s").rglob("*.yaml"):
            if "kustomization" in f.name:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for doc in _yaml.safe_load_all(text):
                if not isinstance(doc, dict) or doc.get("kind") != "Service":
                    continue
                name = (doc.get("metadata") or {}).get("name", "")
                svc_id = name.replace("media-stack-", "", 1)
                if svc_id in self._ALLOWED_DRIFT:
                    continue
                registry = SERVICE_MAP.get(svc_id)
                if not registry or not registry.port:
                    continue
                spec = doc.get("spec") or {}
                for port in spec.get("ports") or []:
                    target = port.get("targetPort") or port.get("port")
                    if target and int(target) != int(registry.port):
                        drift.append(
                            f"{name}: targetPort={target} but registry "
                            f"port={registry.port}"
                        )
        self.assertFalse(
            drift,
            "k8s Service targetPort doesn't match registry port — "
            "the registry is the single source of truth:\n  - "
            + "\n  - ".join(drift),
        )


# ---------------------------------------------------------------------------
# E2 — contract job requires: graph is a DAG
# ---------------------------------------------------------------------------
class ContractJobRequiresDag(unittest.TestCase):
    """Contract-declared jobs form a prerequisite graph via the
    ``requires:`` field. A cycle would deadlock the bootstrap
    planner. The planner has its own runtime cycle-check, but
    that fires AT bootstrap time when it's already too late to
    fix the user's install — catch it at config-load time."""

    def test_no_cycles_in_job_graph(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        contracts_dir = ROOT / "contracts" / "services"
        if not contracts_dir.is_dir():
            self.skipTest("contracts/services not present")

        graph: dict[str, list[str]] = {}
        for f in contracts_dir.glob("*.yaml"):
            if f.stem.startswith("_"):
                continue
            doc = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            jobs = ((doc.get("plugin") or {}).get("jobs") or {})
            for job_name, job_def in jobs.items():
                requires = (job_def or {}).get("requires") or []
                if isinstance(requires, list):
                    graph[job_name] = [str(r) for r in requires]

        # Iterative DFS to detect cycles
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in graph}
        cycles: list[list[str]] = []

        def dfs(start: str) -> None:
            stack: list[tuple[str, int]] = [(start, 0)]
            path: list[str] = []
            while stack:
                node, idx = stack[-1]
                if idx == 0:
                    if color.get(node) == GRAY:
                        cycles.append(path + [node])
                        stack.pop()
                        continue
                    if color.get(node) == BLACK:
                        stack.pop()
                        continue
                    color[node] = GRAY
                    path.append(node)
                deps = graph.get(node, [])
                if idx < len(deps):
                    stack[-1] = (node, idx + 1)
                    stack.append((deps[idx], 0))
                else:
                    color[node] = BLACK
                    path.pop()
                    stack.pop()

        for n in graph:
            if color[n] == WHITE:
                dfs(n)

        self.assertFalse(
            cycles,
            f"Contract job 'requires:' graph has {len(cycles)} cycle(s) "
            f"— the bootstrap planner would deadlock:\n  - "
            + "\n  - ".join(" → ".join(c) for c in cycles[:5]),
        )


# ---------------------------------------------------------------------------
# E3 — technology_bindings resolve to real contracts
# ---------------------------------------------------------------------------
class TechnologyBindingsResolveToContracts(unittest.TestCase):
    """profile YAMLs declare ``technology_bindings: {slot: id}``;
    each id must map to a real contract YAML's stem or
    ``plugin.technology``. Catches typos like
    ``media_server: jelly_fin`` that wouldn't fail at config load
    but would fail at runtime when nothing answered."""

    def test_every_binding_resolves(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        contracts_dir = ROOT / "contracts" / "services"
        if not contracts_dir.is_dir():
            self.skipTest("contracts/services not present")

        contract_ids: set[str] = set()
        contract_techs: set[str] = set()
        for f in contracts_dir.glob("*.yaml"):
            if f.stem.startswith("_"):
                continue
            contract_ids.add(f.stem)
            doc = _yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            tech = ((doc.get("plugin") or {}).get("technology"))
            if tech:
                contract_techs.add(str(tech))

        profile_files: list[Path] = [
            ROOT / "contracts" / "media-stack.profile.yaml",
        ]
        bp = ROOT / "examples" / "bootstrap-profiles"
        if bp.is_dir():
            profile_files += list(bp.glob("*.yaml"))

        bad: list[str] = []
        for p in profile_files:
            if not p.is_file():
                continue
            try:
                doc = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except _yaml.YAMLError:
                continue
            bindings = doc.get("technology_bindings") or {}
            for slot, tech in bindings.items():
                if isinstance(tech, str) and tech:
                    if tech not in contract_ids and tech not in contract_techs:
                        bad.append(
                            f"{p.relative_to(ROOT)}: {slot}={tech!r} "
                            f"— no contract or plugin.technology with that id"
                        )
        self.assertFalse(
            bad,
            "technology_bindings reference non-existent contracts:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# K3 — k8s/profiles/* kustomization references resolve
# ---------------------------------------------------------------------------
class KustomizationReferencesResolve(unittest.TestCase):
    """Every ``resources:`` and ``patches:`` reference in a
    kustomization.yaml under k8s/profiles/* must resolve to a file
    on disk. Stale references are silent until the operator
    actually runs ``kubectl apply -k``."""

    def test_kustomize_resources_and_patches_exist(self) -> None:
        try:
            import yaml as _yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        profiles_dir = ROOT / "k8s" / "profiles"
        if not profiles_dir.is_dir():
            self.skipTest("k8s/profiles not present")

        bad: list[str] = []
        for kf in profiles_dir.rglob("kustomization.yaml"):
            doc = _yaml.safe_load(kf.read_text(encoding="utf-8")) or {}
            base = kf.parent
            for r in (doc.get("resources") or []):
                # Skip remote refs (start with http://, ../, github.com/, etc.)
                if not isinstance(r, str):
                    continue
                if r.startswith(("http://", "https://", "github.com/")):
                    continue
                p = (base / r).resolve()
                if not p.exists():
                    bad.append(f"{kf.relative_to(ROOT)}: resource {r!r} not found")
            for patch in (doc.get("patches") or []):
                path_str = (
                    patch.get("path", "") if isinstance(patch, dict) else str(patch)
                )
                if not path_str:
                    continue
                p = (base / path_str).resolve()
                if not p.exists():
                    bad.append(f"{kf.relative_to(ROOT)}: patch {path_str!r} not found")
        self.assertFalse(
            bad,
            "Kustomize references that don't exist on disk:\n  - "
            + "\n  - ".join(bad),
        )


# ---------------------------------------------------------------------------
# V1 — controller image tags match VERSION across all manifests
# ---------------------------------------------------------------------------
class ControllerImageVersionParity(unittest.TestCase):
    """Every ``media-stack-controller:vX.Y.Z`` reference in
    docker-compose, k8s manifests, profile YAMLs, and example
    bootstrap profiles must agree with the canonical ``VERSION``
    file. Catches "I bumped VERSION but forgot to update one of
    the seven k8s manifests" drift."""

    def test_image_tags_match_version_file(self) -> None:
        ver_file = ROOT / "VERSION"
        if not ver_file.is_file():
            self.skipTest("VERSION not present")
        canonical = ver_file.read_text(encoding="utf-8").strip()

        bad: list[str] = []
        # Scan YAMLs across the source tree (skip dist/ — generated;
        # skip .claude / .venv / node_modules — vendor / agent state).
        for f in ROOT.rglob("*.yaml"):
            fs = str(f)
            if any(s in fs for s in (".claude", ".venv", "node_modules", "/dist/")):
                continue
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, IsADirectoryError):
                continue
            for m in re.finditer(
                r"media-stack-controller:v(\d+\.\d+\.\d+)", text,
            ):
                if m.group(1) != canonical:
                    bad.append(
                        f"{f.relative_to(ROOT)}: pinned to v{m.group(1)} "
                        f"(VERSION says {canonical})"
                    )
        self.assertFalse(
            bad,
            f"Controller image tags drift from VERSION ({canonical}):\n  - "
            + "\n  - ".join(bad[:15]),
        )


if __name__ == "__main__":
    unittest.main()
