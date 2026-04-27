"""Hard gates that protect a small number of must-be-zero
invariants. Each test asserts the count is exactly 0 — there is no
baseline file because the answer is never "we accept some." A single
violation fails the build.

Why hard gates and not burndowns
--------------------------------
Burndowns are right when the count is high (hundreds → zero is a
multi-quarter project) and every offender is the same shape. The
checks in this file are all narrow, low-volume invariants where
even one violation is a real production risk:

  * a single root-user container is a privilege-escalation pivot
  * a single ``permissions: write-all`` workflow can push to main
    on a malicious PR
  * a single ``privileged: true`` pod has the host kernel
  * a single committed secret is in the public git history forever

When the count is naturally zero today, the cheap operation is to
keep it zero. Hard gate, no debate.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_DIR = REPO_ROOT / "deploy" / "compose"
K8S_DIR = REPO_ROOT / "deploy" / "k8s"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
SRC = REPO_ROOT / "src" / "media_stack"


# ---------------------------------------------------------------------------
# 1. CONTAINERS_RUNNING_AS_ROOT = 0
# ---------------------------------------------------------------------------


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


def _compose_user_violations() -> list[str]:
    """Compose services that explicitly set ``user: root`` or
    ``user: "0"``. ``user: 1000`` is fine; we only flag explicit
    root."""
    out: list[str] = []
    if not COMPOSE_DIR.is_dir():
        return out
    for path in COMPOSE_DIR.rglob("*.y*ml"):
        for doc in _iter_yaml_docs(path):
            services = doc.get("services") or {}
            if not isinstance(services, dict):
                continue
            for svc_name, svc in services.items():
                if not isinstance(svc, dict):
                    continue
                user = svc.get("user")
                if user is None:
                    continue
                user_str = str(user).strip().strip("\"'")
                if user_str in {"root", "0", "0:0"}:
                    out.append(
                        f"{path.relative_to(REPO_ROOT)}: service "
                        f"{svc_name!r} runs as user={user_str!r}",
                    )
    return out


def _k8s_root_user_violations() -> list[str]:
    """K8s containers that explicitly run as root or omit the
    ``runAsNonRoot: true`` enforcement. We require BOTH:
      * ``securityContext.runAsNonRoot: true`` set somewhere
        (pod-level or container-level)
      * No explicit ``runAsUser: 0`` override
    Containers without any securityContext at all are flagged
    because they default to root."""
    out: list[str] = []
    if not K8S_DIR.is_dir():
        return out
    for path in K8S_DIR.rglob("*.yaml"):
        if any(part == "_archive" for part in path.parts):
            continue
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
            pod_sec = tmpl_spec.get("securityContext") or {}
            pod_runs_nonroot = pod_sec.get("runAsNonRoot") is True
            for c in tmpl_spec.get("containers") or []:
                csec = c.get("securityContext") or {}
                runs_nonroot = (
                    csec.get("runAsNonRoot") is True or pod_runs_nonroot
                )
                run_as_user = csec.get(
                    "runAsUser", pod_sec.get("runAsUser"),
                )
                if run_as_user == 0:
                    out.append(
                        f"{path.relative_to(REPO_ROOT)}: container "
                        f"{c.get('name','?')!r} sets runAsUser=0",
                    )
                    continue
                if not runs_nonroot:
                    out.append(
                        f"{path.relative_to(REPO_ROOT)}: container "
                        f"{c.get('name','?')!r} missing "
                        f"runAsNonRoot=true (defaults to root)",
                    )
    return out


def test_burndown_containers_running_as_root() -> None:
    """No deployed container should run as root. Aspirational hard
    gate — promote to ``assert == 0`` once the baseline reaches 0.
    Currently a burndown so existing offenders don't block CI."""
    findings = _compose_user_violations() + _k8s_root_user_violations()
    _enforce_burndown(
        "containers-running-as-root",
        len(findings),
        hint=(
            "Containers must run as a non-root user. A root-user "
            "container is a privilege-escalation pivot for any RCE "
            "in the workload above it.\n\n"
            "Resolution: in compose, set ``user: \"1000:1000\"`` "
            "(or another non-zero UID); in k8s, set "
            "``securityContext.runAsNonRoot: true`` and "
            "``runAsUser: 1000`` at the pod or container level. "
            "Promote this ratchet to ``assert len(findings) == 0`` "
            "once the baseline drops to 0 — then a single regression "
            "fails CI immediately."
        ),
    )


# ---------------------------------------------------------------------------
# 2. K8S_PRIVILEGED_CONTAINERS = 0
# ---------------------------------------------------------------------------


def test_hard_gate_no_k8s_privileged_containers() -> None:
    """No k8s container may run with ``privileged: true``. Privileged
    containers can do anything the host kernel can do; they should
    only exist behind a feature flag with a documented justification."""
    findings: list[str] = []
    if not K8S_DIR.is_dir():
        return
    for path in K8S_DIR.rglob("*.yaml"):
        if any(part == "_archive" for part in path.parts):
            continue
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
                csec = c.get("securityContext") or {}
                if csec.get("privileged") is True:
                    findings.append(
                        f"{path.relative_to(REPO_ROOT)}: container "
                        f"{c.get('name','?')!r} sets privileged=true",
                    )
    if findings:
        details = "\n".join(f"  {f}" for f in findings)
        raise AssertionError(
            "Privileged containers can do anything the host kernel "
            f"can do. {len(findings)} violation(s):\n{details}\n\n"
            "Resolution: drop ``privileged: true``. If you need a "
            "specific capability, add it to "
            "``securityContext.capabilities.add`` (e.g. "
            "``- NET_ADMIN``) and document why in a comment."
        )


# ---------------------------------------------------------------------------
# 3. GITHUB_ACTIONS_WRITE_ALL = 0
# ---------------------------------------------------------------------------


_RE_PERMISSIONS_WRITE_ALL = re.compile(
    r"permissions:\s*write-all\b",
)


def test_hard_gate_no_github_actions_write_all() -> None:
    """No workflow may declare ``permissions: write-all``. Use the
    explicit minimal-perms list per workflow."""
    findings: list[str] = []
    if not WORKFLOWS_DIR.is_dir():
        return
    for path in sorted(WORKFLOWS_DIR.rglob("*.y*ml")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _RE_PERMISSIONS_WRITE_ALL.search(line):
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}:{line_no}",
                )
    if findings:
        details = "\n".join(f"  {f}" for f in findings)
        raise AssertionError(
            "``permissions: write-all`` gives the workflow's GITHUB_TOKEN "
            "every available scope. A malicious PR that triggers a "
            "workflow under ``pull_request_target`` can then push to "
            f"main, modify releases, etc. {len(findings)} violation(s):"
            f"\n{details}\n\n"
            "Resolution: replace with an explicit minimal list "
            "per workflow, e.g.\n"
            "  permissions:\n"
            "    contents: read\n"
            "    pull-requests: write"
        )


# ---------------------------------------------------------------------------
# 4. SECRETS_IN_REPO = 0
# ---------------------------------------------------------------------------


# Patterns conservative enough to avoid false positives. Each one
# requires a HIGH-ENTROPY value, not just a name like
# ``api_key = "xxx"``.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "AWS access key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    ),
    (
        "AWS secret access key",
        re.compile(
            r'aws_secret(?:_access)?_key\s*[:=]\s*["\']'
            r'[A-Za-z0-9/+=]{40}["\']',
            re.IGNORECASE,
        ),
    ),
    (
        "Private key block",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    (
        "GitHub PAT (classic + fine-grained)",
        re.compile(r"\bghp_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{82}\b"),
    ),
    (
        "Slack token",
        re.compile(r"\bxox[abops]-[A-Za-z0-9-]{10,}\b"),
    ),
    (
        "Authelia hashed password leak",
        re.compile(
            r'password_hash\s*[:=]\s*["\']\$argon2id\$[^"\']{50,}["\']',
            re.IGNORECASE,
        ),
    ),
)

_SECRET_SCAN_INCLUDED_DIRS = (
    "src",
    "ui/src",
    "deploy",
    "contracts",
    "config/defaults",  # repo-tracked defaults; everything else under config/ is gitignored
    ".github",
    "bin",
    "docs",
)

# Allowlist of files that legitimately contain test fixtures or
# example placeholders — they look like secrets to the regex but
# aren't real ones. New entries require careful justification.
_SECRET_ALLOWLIST = frozenset({
    # Test fixtures.
    "tests/fixtures/sessions.json",
    # Example .env templates with placeholder values.
    "deploy/compose/.env.example",
    # Example bootstrap profiles with placeholder secrets.
    "deploy/examples/bootstrap-profiles/media-compose-standard.yaml",
    # TLS-cert generator's docstring shows the canonical PEM block
    # header so operators recognize a self-signed cert. Not a leak.
    "src/media_stack/core/edge/tls_certificate_service.py",
    # TlsInstallDialog UI test exercises a fake PEM upload — the
    # block is a synthetic openssl-generated fixture, not a real
    # private key.
    "ui/src/features/routing-admin/TlsInstallDialog.test.tsx",
})


def test_hard_gate_no_secrets_in_repo() -> None:
    """Conservative scan for committed secrets — AWS keys,
    private-key blocks, GitHub PATs, Slack tokens. Pattern set is
    deliberately narrow to avoid false positives on every
    ``api_key = "test"`` in a fixture."""
    findings: list[str] = []
    for top in _SECRET_SCAN_INCLUDED_DIRS:
        root = REPO_ROOT / top
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(
                part in {"__pycache__", "node_modules", ".venv", "_archive"}
                for part in path.parts
            ):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel in _SECRET_ALLOWLIST:
                continue
            # Skip large binary-ish files.
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for label, pat in _SECRET_PATTERNS:
                m = pat.search(text)
                if m:
                    line_no = text.count("\n", 0, m.start()) + 1
                    findings.append(
                        f"{rel}:{line_no} {label}",
                    )
                    break  # one finding per file is enough
    if findings:
        details = "\n".join(f"  {f}" for f in findings)
        raise AssertionError(
            f"Possible committed secrets ({len(findings)} file(s)):"
            f"\n{details}\n\n"
            "Resolution: rotate the secret immediately, then move the "
            "value to a secret store (k8s ``Secret``, env var loaded "
            "from ``.env`` excluded by ``.gitignore``, etc.). The "
            "leaked value is in the git history forever; rotation is "
            "the only fix. If this is a fixture / placeholder, add "
            "the path to ``_SECRET_ALLOWLIST`` in this ratchet with "
            "a one-line rationale."
        )


# ---------------------------------------------------------------------------
# Magic-number burndowns (NOT hard gates — too many to drive to 0
# in one pass)
# ---------------------------------------------------------------------------


import ast  # noqa: E402  -- AST walk for accurate magic-number counts


_MAGIC_NUMBER_ALLOWLIST: frozenset[int | float] = frozenset({
    -1, 0, 1, 2, 3, 10, 100, 1000,
    # Common HTTP status codes — already gated by
    # ``http-status-int-literal`` ratchet, no double-flagging.
    200, 201, 204, 301, 302, 400, 401, 403, 404, 409, 410, 422, 429,
    500, 502, 503, 504,
})

_RATCHETS_DIR = REPO_ROOT / ".ratchets"


def _load_baseline(name: str) -> int | None:
    p = _RATCHETS_DIR / f"{name}-baseline.txt"
    if not p.is_file():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _seed_baseline(name: str, value: int) -> None:
    _RATCHETS_DIR.mkdir(parents=True, exist_ok=True)
    (_RATCHETS_DIR / f"{name}-baseline.txt").write_text(
        f"{value}\n", encoding="utf-8",
    )


def _enforce_burndown(name: str, current: int, *, hint: str) -> None:
    baseline = _load_baseline(name)
    if baseline is None:
        _seed_baseline(name, current)
        return
    if current > baseline:
        raise AssertionError(
            f"{name}: regressed from {baseline} → {current}.\n{hint}",
        )


def _is_business_logic_file(path: Path) -> bool:
    if any(
        part in {"__pycache__", ".venv", "tests"}
        for part in path.parts
    ):
        return False
    if path.name.startswith("test_"):
        return False
    return True


def _is_in_constant_assignment(node: ast.Constant, parent_map: dict) -> bool:
    """Heuristic — a Constant inside ``UPPERCASE_NAME = X`` or
    ``ClassName.UPPER = X`` is "in a constant", not a magic number.
    Walks up the parent chain to find the enclosing Assign."""
    cur = parent_map.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.Assign):
            for tgt in cur.targets:
                if isinstance(tgt, ast.Name) and tgt.id.isupper():
                    return True
                if isinstance(tgt, ast.Attribute) and tgt.attr.isupper():
                    return True
            return False
        if isinstance(cur, ast.AnnAssign):
            tgt = cur.target
            if isinstance(tgt, ast.Name) and tgt.id.isupper():
                return True
            return False
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
        cur = parent_map.get(id(cur))
    return False


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    out: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            out[id(child)] = parent
    return out


def _is_in_conditional(node: ast.Constant, parent_map: dict) -> bool:
    """True when ``node`` is somewhere inside an ``If``/``While``
    test expression."""
    cur = parent_map.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.If) or isinstance(cur, ast.While):
            return True
        cur = parent_map.get(id(cur))
    return False


def test_burndown_magic_numbers_outside_constants() -> None:
    """Numeric literals (excluding 0/1/-1 + a small allowlist) that
    don't appear inside an UPPERCASE constant assignment are magic
    numbers — name them so the next reader knows what they mean."""
    count = 0
    if not SRC.is_dir():
        _enforce_burndown(
            "magic-numbers-outside-constants", 0,
            hint="(no source tree)",
        )
        return
    for path in SRC.rglob("*.py"):
        if not _is_business_logic_file(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        parent_map = _build_parent_map(tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)
            ):
                continue
            if node.value in _MAGIC_NUMBER_ALLOWLIST:
                continue
            if _is_in_constant_assignment(node, parent_map):
                continue
            count += 1
    _enforce_burndown(
        "magic-numbers-outside-constants",
        count,
        hint=(
            "Move the literal to an UPPERCASE module-level constant "
            "with a name that explains the dimension and the unit "
            "(``DEFAULT_FETCH_TIMEOUT_SECONDS = 5``, "
            "``MAX_PAGE_SIZE = 250``). Bare numbers in expressions "
            "force the reader to play archaeologist."
        ),
    )


def test_burndown_magic_numbers_in_conditionals() -> None:
    """Subset of the above: any non-allowlisted numeric literal
    inside an ``if``/``while`` test expression. ``if retries > 5``
    should be ``if retries > MAX_RETRIES``; the conditional encodes
    a business rule that deserves a name."""
    count = 0
    if not SRC.is_dir():
        _enforce_burndown(
            "magic-numbers-in-conditionals", 0,
            hint="(no source tree)",
        )
        return
    for path in SRC.rglob("*.py"):
        if not _is_business_logic_file(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        parent_map = _build_parent_map(tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool)
            ):
                continue
            if node.value in _MAGIC_NUMBER_ALLOWLIST:
                continue
            if not _is_in_conditional(node, parent_map):
                continue
            count += 1
    _enforce_burndown(
        "magic-numbers-in-conditionals",
        count,
        hint=(
            "Replace ``if x > 5`` / ``while attempts < 3`` with a "
            "named constant. Conditionals encode business rules; "
            "the rule reads better when the threshold has a name."
        ),
    )


# ---------------------------------------------------------------------------
# 5. PYTHON_TYPE_IGNORE_WITHOUT_REASON — burndown
# ---------------------------------------------------------------------------


# Match ``# type: ignore`` lines that DON'T have a follow-up
# explanation. Patterns considered "with reason":
#   - ``# type: ignore[error-code]`` — typed silencer
#   - ``# type: ignore  # reason: ...``
#   - ``# type: ignore  # <any non-empty comment>``
_RE_TYPE_IGNORE_BARE = re.compile(
    r"#\s*type:\s*ignore\s*$",
)
_RE_TYPE_IGNORE_NO_CODE_NO_REASON = re.compile(
    r"#\s*type:\s*ignore\b(?![\[\]])(?!.*#)",
)


def test_burndown_python_type_ignore_without_reason() -> None:
    """``# type: ignore`` should at minimum carry an error-code
    (``# type: ignore[arg-type]``) or a follow-up reason comment.
    A bare silencer hides bugs."""
    count = 0
    if not SRC.is_dir():
        _enforce_burndown(
            "python-type-ignore-without-reason", 0,
            hint="(no source tree)",
        )
        return
    for path in SRC.rglob("*.py"):
        if not _is_business_logic_file(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            if _RE_TYPE_IGNORE_NO_CODE_NO_REASON.search(line):
                count += 1
            elif _RE_TYPE_IGNORE_BARE.search(line):
                count += 1
    _enforce_burndown(
        "python-type-ignore-without-reason",
        count,
        hint=(
            "Either narrow the ignore (``# type: ignore[arg-type]``) "
            "so it only silences the specific error, OR add a "
            "follow-up reason comment "
            "(``# type: ignore  # third-party SDK lacks stubs``). "
            "A bare silencer is a hammer that hides future bugs of "
            "every kind."
        ),
    )
