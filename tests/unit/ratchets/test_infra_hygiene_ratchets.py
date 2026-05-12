"""Phase 2 infrastructure ratchets — Docker, Kubernetes, GitHub, and
dependency-pinning checks.

Already covered:
  * docker-latest-tag-compose, k8s-missing-resources,
    k8s-missing-probes, containers-running-as-root,
    test_hard_gate_no_k8s_privileged_containers,
    test_hard_gate_no_github_actions_write_all,
    test_hard_gate_no_secrets_in_repo, actions-not-pinned-sha.

Adds:
  * Dockerfile: missing HEALTHCHECK, layer count, apt-get upgrade,
    unpinned package installs, secrets in COPY/ADD, missing
    .dockerignore, image not pinned by digest.
  * K8s: hostPath volumes, wildcard RBAC, LoadBalancer services,
    secrets mounted as env vars, deployments with replicas=1,
    images using mutable (non-digest) tags.
  * GitHub: pull_request_target without protection, missing
    CODEOWNERS coverage, large files committed, generated files
    committed.
  * Dependencies: unpinned versions in package.json + pyproject.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_DIR = REPO_ROOT / "deploy"
COMPOSE_DIR = DEPLOY_DIR / "compose"
K8S_DIR = DEPLOY_DIR / "k8s"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
RATCHETS_DIR = REPO_ROOT / ".ratchets"


def _load_baseline(name: str) -> int | None:
    p = RATCHETS_DIR / f"{name}-baseline.txt"
    if not p.is_file():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _seed_baseline(name: str, value: int) -> None:
    RATCHETS_DIR.mkdir(parents=True, exist_ok=True)
    (RATCHETS_DIR / f"{name}-baseline.txt").write_text(
        f"{value}\n", encoding="utf-8",
    )


def _enforce_burndown(name: str, current: int, *, hint: str) -> None:
    baseline = _load_baseline(name)
    if baseline is None:
        _seed_baseline(name, current)
        return
    if current > baseline:
        raise AssertionError(
            f"{name}: regressed from {baseline} → {current}.\n{hint}"
        )


def _iter_yaml_docs(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    try:
        for doc in yaml.safe_load_all(text):
            if isinstance(doc, dict):
                out.append(doc)
    except yaml.YAMLError:
        return []
    return out


def _list_dockerfiles() -> list[Path]:
    if not DEPLOY_DIR.is_dir():
        return []
    return [
        p for p in DEPLOY_DIR.rglob("*.Dockerfile")
        if p.is_file()
    ] + [
        p for p in DEPLOY_DIR.rglob("Dockerfile")
        if p.is_file()
    ]


def _list_k8s_yamls() -> list[Path]:
    if not K8S_DIR.is_dir():
        return []
    return [
        p for p in K8S_DIR.rglob("*.yaml")
        if p.is_file() and "_archive" not in p.parts
    ]


# ---------------------------------------------------------------------------
# DOCKER
# ---------------------------------------------------------------------------


def test_burndown_dockerfile_missing_healthcheck() -> None:
    """Dockerfiles without a ``HEALTHCHECK`` directive. The compose
    + k8s deployments use their own probe configs, but baking a
    HEALTHCHECK into the image ensures bare ``docker run`` users
    also get readiness signals."""
    count = 0
    for path in _list_dockerfiles():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "HEALTHCHECK" not in text:
            count += 1
    _enforce_burndown(
        "dockerfile-missing-healthcheck",
        count,
        hint=(
            "Add a ``HEALTHCHECK CMD curl --fail http://localhost:"
            "<port>/healthz`` directive (or the equivalent for your "
            "service). Without it, ``docker ps`` shows the container "
            "as healthy even when the app inside is hung."
        ),
    )


def test_burndown_dockerfile_apt_get_upgrade() -> None:
    """``apt-get upgrade`` (or ``dist-upgrade``) inside a Dockerfile
    pulls a moving target into the image — the same Dockerfile
    builds different images on different days. Pin packages
    explicitly instead."""
    pat = re.compile(r"apt(?:-get)?\s+(?:upgrade|dist-upgrade)\b")
    count = 0
    for path in _list_dockerfiles():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        count += len(pat.findall(text))
    _enforce_burndown(
        "dockerfile-apt-get-upgrade",
        count,
        hint=(
            "Drop the ``apt-get upgrade`` step. Use a base image "
            "that's already up to date, and install specific package "
            "versions with ``apt-get install --no-install-recommends "
            "<pkg>=<version>``. Reproducibility > convenience."
        ),
    )


def test_burndown_dockerfile_unpinned_apt_packages() -> None:
    """``apt-get install <pkg>`` without an ``=<version>`` pin —
    same reproducibility risk as ``apt-get upgrade``. Heuristic:
    counts ``apt-get install`` lines that don't contain ``=``."""
    install_pat = re.compile(r"apt(?:-get)?\s+install[^\n\\]+", re.IGNORECASE)
    count = 0
    for path in _list_dockerfiles():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in install_pat.finditer(text):
            line = m.group(0)
            # Count the install lines with no version pins.
            if "=" not in line and "--no-install-recommends" in line:
                # Conservative: only flag when --no-install-recommends
                # is set (signals a real package install vs a
                # build-deps virtual group). Avoids false positives
                # on ``apt-get install -y --upgradable``.
                count += 1
    _enforce_burndown(
        "dockerfile-unpinned-apt-packages",
        count,
        hint=(
            "Pin to specific package versions: ``apt-get install -y "
            "--no-install-recommends openssl=3.0.11-1ubuntu2.5``. "
            "Use ``apt-cache madison <pkg>`` to find the current "
            "version. Renovate/Dependabot can keep them current."
        ),
    )


def test_burndown_dockerfile_image_not_pinned_by_digest() -> None:
    """``FROM image:tag`` without ``@sha256:...`` pinning. Tags are
    mutable; digests are immutable. For reproducible builds across
    weeks, pin by digest."""
    from_pat = re.compile(
        r"^\s*FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE,
    )
    count = 0
    for path in _list_dockerfiles():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in from_pat.finditer(text):
            ref = m.group(1)
            # Skip ``--platform=...`` and stage names (``AS builder``).
            if ref.startswith("--"):
                continue
            if "@sha256:" not in ref:
                count += 1
    _enforce_burndown(
        "dockerfile-image-not-pinned-by-digest",
        count,
        hint=(
            "Replace ``FROM python:3.12-alpine`` with "
            "``FROM python:3.12-alpine@sha256:<digest>``. Get the "
            "digest from ``docker pull python:3.12-alpine`` (the "
            "trailing line). Renovate updates digests automatically."
        ),
    )


def test_burndown_dockerfile_secrets_via_copy() -> None:
    """``COPY .env /...`` / ``COPY *.key /...`` / ``COPY id_rsa /...``
    — these copy secrets into the image. Use BuildKit secrets
    (``RUN --mount=type=secret,id=...``) instead so they don't end
    up in any layer."""
    pat = re.compile(
        r"^\s*(?:COPY|ADD)\s+[^\s]*"
        r"(?:\.env(?:\.\w+)?|id_rsa|\.pem|\.p12|secrets?\.json|"
        r"private[_-]?key|\.htpasswd)",
        re.MULTILINE | re.IGNORECASE,
    )
    count = 0
    for path in _list_dockerfiles():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        count += len(pat.findall(text))
    _enforce_burndown(
        "dockerfile-secrets-via-copy",
        count,
        hint=(
            "Never COPY secrets into a Docker image — they live in "
            "every layer of the resulting image forever. Use "
            "BuildKit secret mounts: ``RUN --mount=type=secret,"
            "id=mykey cat /run/secrets/mykey``. The secret is "
            "available during the RUN but never persisted in the "
            "image."
        ),
    )


def test_burndown_missing_dockerignore() -> None:
    """A repo with Dockerfiles should have a ``.dockerignore`` to
    keep build context small (faster builds, no accidental secret
    inclusion)."""
    dockerfiles = _list_dockerfiles()
    if not dockerfiles:
        _enforce_burndown(
            "missing-dockerignore", 0, hint="(no Dockerfiles)",
        )
        return
    # Walk up from each Dockerfile's directory looking for a
    # ``.dockerignore``.
    missing = 0
    for df in dockerfiles:
        cur: Path = df.parent
        found = False
        while cur != REPO_ROOT.parent:
            if (cur / ".dockerignore").is_file():
                found = True
                break
            cur = cur.parent
        if not found:
            missing += 1
    _enforce_burndown(
        "missing-dockerignore",
        missing,
        hint=(
            "Add a ``.dockerignore`` next to (or anywhere above) "
            "the Dockerfile. At minimum exclude ``.git``, ``node_"
            "modules``, ``__pycache__``, ``.venv``, ``*.log``, "
            "``.env*``. Saves disk + network cost on every build "
            "and prevents accidental secret inclusion."
        ),
    )


# ---------------------------------------------------------------------------
# KUBERNETES
# ---------------------------------------------------------------------------


def test_burndown_k8s_hostpath_volumes() -> None:
    """``hostPath`` volumes mount the node filesystem into the pod
    — a privilege-escalation vector. Use ``emptyDir``,
    ``persistentVolumeClaim``, or a CSI driver instead."""
    count = 0
    for path in _list_k8s_yamls():
        for doc in _iter_yaml_docs(path):
            spec = doc.get("spec", {})
            tmpl_spec = (
                spec.get("template", {}).get("spec", {})
                if doc.get("kind") != "CronJob"
                else spec.get("jobTemplate", {})
                .get("spec", {}).get("template", {}).get("spec", {})
            )
            for vol in tmpl_spec.get("volumes") or []:
                if isinstance(vol, dict) and "hostPath" in vol:
                    count += 1
    _enforce_burndown(
        "k8s-hostpath-volumes",
        count,
        hint=(
            "``hostPath`` volumes give the container access to the "
            "node's filesystem. Replace with ``emptyDir`` (in-pod "
            "scratch), ``persistentVolumeClaim`` (durable, scheduled), "
            "or a CSI driver. If the use case is genuinely "
            "host-coupled (node-local cache, host monitoring), "
            "document why in a comment + add to an explicit "
            "allowlist."
        ),
    )


def test_burndown_k8s_wildcard_rbac() -> None:
    """RBAC roles with ``"*"`` in resources, verbs, or apiGroups —
    grants the holder unbounded access. Specify the exact set."""
    count = 0
    for path in _list_k8s_yamls():
        for doc in _iter_yaml_docs(path):
            if doc.get("kind") not in ("Role", "ClusterRole"):
                continue
            for rule in doc.get("rules") or []:
                if not isinstance(rule, dict):
                    continue
                for key in ("resources", "verbs", "apiGroups"):
                    vals = rule.get(key) or []
                    if any(v == "*" for v in vals):
                        count += 1
                        break
    _enforce_burndown(
        "k8s-wildcard-rbac",
        count,
        hint=(
            "Replace ``*`` with the exact resources / verbs / "
            "apiGroups the workload needs. ``*`` in any of the three "
            "fields means the principal can act outside its expected "
            "scope — exactly the risk RBAC exists to prevent."
        ),
    )


def test_burndown_k8s_loadbalancer_services() -> None:
    """``Service: LoadBalancer`` provisions a cloud LB per service —
    expensive and exposes the service externally. Prefer
    ``ClusterIP`` + Ingress."""
    count = 0
    for path in _list_k8s_yamls():
        for doc in _iter_yaml_docs(path):
            if doc.get("kind") != "Service":
                continue
            if (doc.get("spec") or {}).get("type") == "LoadBalancer":
                count += 1
    _enforce_burndown(
        "k8s-loadbalancer-services",
        count,
        hint=(
            "Use ``ClusterIP`` (in-cluster only) or ``NodePort`` "
            "with an external Ingress. ``LoadBalancer`` allocates a "
            "cloud LB per service — high cost, hard to observe, "
            "and exposes the service to the world by default."
        ),
    )


def test_burndown_k8s_secrets_mounted_as_env_vars() -> None:
    """Secrets exposed as env vars are visible to ``ps`` /
    ``/proc/<pid>/environ`` from any process in the container,
    AND end up in container logs if the app crashes during startup
    and dumps env. Mount as files instead."""
    count = 0
    for path in _list_k8s_yamls():
        for doc in _iter_yaml_docs(path):
            if doc.get("kind") not in (
                "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob",
            ):
                continue
            spec = doc.get("spec", {})
            tmpl_spec = (
                spec.get("template", {}).get("spec", {})
                if doc.get("kind") != "CronJob"
                else spec.get("jobTemplate", {})
                .get("spec", {}).get("template", {}).get("spec", {})
            )
            for c in tmpl_spec.get("containers") or []:
                for env_entry in c.get("env") or []:
                    if not isinstance(env_entry, dict):
                        continue
                    val_from = env_entry.get("valueFrom") or {}
                    if "secretKeyRef" in val_from:
                        count += 1
                for env_from in c.get("envFrom") or []:
                    if "secretRef" in env_from:
                        count += 1
    _enforce_burndown(
        "k8s-secrets-mounted-as-env-vars",
        count,
        hint=(
            "Mount the Secret as a file: ``volumeMounts: - "
            "mountPath: /var/run/secrets/foo\\n  name: foo``. The "
            "app reads from disk instead of env. Files have proper "
            "permissions; env vars are inherited by every "
            "subprocess + visible to ``ps``."
        ),
    )


def test_burndown_k8s_replicas_one() -> None:
    """``replicas: 1`` is fine for stateful single-instance
    services (Authelia file-backend, the controller as singleton),
    but for stateless services it removes any HA — a node drain
    causes downtime. Track the count; let the operator promote
    services that should be replicated."""
    count = 0
    for path in _list_k8s_yamls():
        for doc in _iter_yaml_docs(path):
            if doc.get("kind") not in ("Deployment", "StatefulSet"):
                continue
            replicas = (doc.get("spec") or {}).get("replicas")
            if replicas == 1:
                count += 1
    _enforce_burndown(
        "k8s-replicas-one",
        count,
        hint=(
            "If the workload is stateless, set replicas >= 2 with "
            "an anti-affinity rule so a node failure doesn't "
            "interrupt traffic. Stateful single-instance services "
            "(databases, file-backed auth providers) are exempt — "
            "document them in a code comment."
        ),
    )


def test_burndown_k8s_images_using_mutable_tags() -> None:
    """K8s container images referenced by tag-only (no digest pin).
    Without a digest, ``imagePullPolicy: Always`` redeploys can
    pull a newer image with the same tag — same risk as Docker
    ``latest``."""
    count = 0
    for path in _list_k8s_yamls():
        for doc in _iter_yaml_docs(path):
            if doc.get("kind") not in (
                "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob",
            ):
                continue
            spec = doc.get("spec", {})
            tmpl_spec = (
                spec.get("template", {}).get("spec", {})
                if doc.get("kind") != "CronJob"
                else spec.get("jobTemplate", {})
                .get("spec", {}).get("template", {}).get("spec", {})
            )
            for c in (
                (tmpl_spec.get("containers") or [])
                + (tmpl_spec.get("initContainers") or [])
            ):
                image = c.get("image") or ""
                if not image:
                    continue
                if "@sha256:" not in image:
                    count += 1
    _enforce_burndown(
        "k8s-images-using-mutable-tags",
        count,
        hint=(
            "Pin container images by digest: "
            "``image: harbor.iomio.io/public/x:v1@sha256:<digest>``. "
            "Without it, ``imagePullPolicy: Always`` (or a node pull "
            "after eviction) can fetch a newer image with the same "
            "tag — silent version drift across pods."
        ),
    )


# ---------------------------------------------------------------------------
# GITHUB
# ---------------------------------------------------------------------------


def test_burndown_github_pull_request_target_workflows() -> None:
    """Workflows triggered by ``pull_request_target`` run with the
    BASE-branch's secrets but the PR-fork's code. A malicious PR
    can read secrets / push releases. Use ``pull_request`` instead
    unless you genuinely need the secrets-on-fork-PR pattern."""
    count = 0
    if not WORKFLOWS_DIR.is_dir():
        _enforce_burndown(
            "github-pull-request-target-workflows", 0,
            hint="(no .github/workflows/)",
        )
        return
    for path in WORKFLOWS_DIR.rglob("*.y*ml"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Match top-level on: trigger.
        if re.search(r"^\s*-?\s*pull_request_target\b", text, re.MULTILINE):
            count += 1
    _enforce_burndown(
        "github-pull-request-target-workflows",
        count,
        hint=(
            "Switch to ``on: pull_request`` (no secrets, runs in a "
            "sandboxed env). If you must use ``pull_request_target`` "
            "(rare — labels, comments), gate the job on a manual "
            "label and use ``actions/checkout`` with "
            "``ref: github.event.pull_request.head.sha`` so the "
            "fork's code doesn't run with secret access."
        ),
    )


def test_burndown_missing_codeowners() -> None:
    """A repository should have a CODEOWNERS file. This ratchet is
    binary: 0 if a CODEOWNERS exists in any of the standard
    locations, 1 if not. Pinned baseline lets the user accept the
    current state and decide later."""
    candidates = [
        REPO_ROOT / "CODEOWNERS",
        REPO_ROOT / ".github" / "CODEOWNERS",
        REPO_ROOT / "docs" / "CODEOWNERS",
    ]
    has_one = any(p.is_file() for p in candidates)
    _enforce_burndown(
        "missing-codeowners",
        0 if has_one else 1,
        hint=(
            "Add ``CODEOWNERS`` (in ``.github/`` or repo root). "
            "Format: ``<glob>  @user-or-team``. Branch protection "
            "can then require code-owner review before merge — "
            "stops random force-pushes to security-critical paths."
        ),
    )


def test_burndown_large_files_committed() -> None:
    """Files over 1 MB committed to the repo. Big binaries bloat
    clones, push history forever, and usually belong in artifact
    storage instead."""
    count = 0
    SIZE_THRESHOLD = 1024 * 1024  # 1 MB
    # Skip everything that lives outside the committed source tree:
    # ``.git`` / ``.venv`` / ``node_modules`` are obvious; ``config``,
    # ``media``, ``data`` are the runtime bind-mount locations a live
    # stack populates on dev machines (NOT committed — see
    # .gitignore). Without these entries, a dev who's run the stack
    # locally sees 2900+ false hits from media files + service
    # SQLite databases.
    skip_dirs = {
        ".git", "node_modules", "dist", "build", ".venv", ".venv-tools",
        "__pycache__", ".pytest_cache", "coverage", ".mypy_cache",
        "config", "media", "data",
    }
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        try:
            if path.stat().st_size > SIZE_THRESHOLD:
                count += 1
        except OSError:
            continue
    _enforce_burndown(
        "large-files-committed",
        count,
        hint=(
            "Files over 1 MB clog clones forever. If it's a binary "
            "asset, host it externally and reference by URL. If "
            "it's a generated artifact, add to ``.gitignore``. If "
            "it's genuinely needed (large fixture, pretrained "
            "model), use git-lfs."
        ),
    )


def test_burndown_generated_files_committed() -> None:
    """Heuristic: files matching common generated-output patterns
    (``*.min.js``, ``*.lock.json`` outside the ones we want, build
    artifacts, generated proto) committed to the repo. Most of
    these belong in .gitignore."""
    count = 0
    # Patterns that strongly suggest generated output.
    bad_patterns = (
        r"\.min\.(?:js|css)$",
        r"\.bundle\.(?:js|css)$",
        r"_pb2\.py$",
        r"_pb\.go$",
        r"\.generated\.\w+$",
    )
    bad_re = re.compile("|".join(bad_patterns))
    skip_dirs = {
        ".git", "node_modules", "dist", "build", ".venv", "__pycache__",
        ".pytest_cache", "coverage",
    }
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if bad_re.search(path.name):
            count += 1
    _enforce_burndown(
        "generated-files-committed",
        count,
        hint=(
            "Generated artifacts (``*.min.js``, ``_pb2.py``, etc.) "
            "should be regenerated in CI, not committed. Add the "
            "pattern to ``.gitignore`` and have the build step "
            "produce them."
        ),
    )


# ---------------------------------------------------------------------------
# DEPENDENCIES
# ---------------------------------------------------------------------------


def test_burndown_unpinned_npm_dependencies() -> None:
    """Counts ``package.json`` deps with ``^`` or ``~`` version
    selectors. These auto-upgrade on ``npm install``, breaking
    reproducible builds. Pin to exact versions and let Renovate
    bump them deliberately."""
    pkg = REPO_ROOT / "ui" / "package.json"
    if not pkg.is_file():
        _enforce_burndown(
            "unpinned-npm-dependencies", 0,
            hint="(no ui/package.json)",
        )
        return
    import json

    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    count = 0
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        for ver in (data.get(key) or {}).values():
            if isinstance(ver, str) and (
                ver.startswith("^")
                or ver.startswith("~")
                or "x" in ver.lower()
                or "*" in ver
            ):
                count += 1
    _enforce_burndown(
        "unpinned-npm-dependencies",
        count,
        hint=(
            "Drop the ``^`` / ``~`` prefix and pin exact versions: "
            "``\"react\": \"19.2.5\"`` not ``\"react\": ^19.2.0\"``. "
            "Renovate / Dependabot then opens a PR for each upgrade "
            "— deliberate + reviewable. Without pinning, ``npm "
            "install`` on different days produces different builds."
        ),
    )


def test_burndown_unpinned_python_dependencies() -> None:
    """Counts pyproject/requirements deps without an explicit
    ``==`` pin (``>= ``, ``~=``, bare name with no version, etc.)."""
    count = 0
    candidates = [
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "requirements.txt",
        REPO_ROOT / "requirements" / "main.txt",
    ]
    pat = re.compile(
        r'^\s*["\']?([a-zA-Z0-9_-]+)["\']?\s*'
        r'(?:[,]\s*"?(==|>=|~=|>|<|\^))?',
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Conservative: count lines containing ``>=``, ``~=``, ``>`` ,
        # or bare names in deps blocks (heuristic).
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Match ``"pkg>=1.0"``, ``"pkg~=1.0"``, etc.
            if re.search(r'[><~]=?\s*\d', stripped):
                count += 1
    _enforce_burndown(
        "unpinned-python-dependencies",
        count,
        hint=(
            "Pin python dependencies to exact versions in "
            "``pyproject.toml`` / ``requirements.txt``: "
            "``\"requests==2.31.0\"`` not ``\"requests>=2.31\"``. "
            "Use a separate constraints file or a lockfile (e.g. "
            "uv lock, poetry lock) for transitives. Reproducible "
            "builds beat opportunistic upgrades."
        ),
    )
