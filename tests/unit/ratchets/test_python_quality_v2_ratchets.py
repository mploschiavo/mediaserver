"""Phase 2 Python code-quality ratchets.

Each ratchet pins a baseline at current state; count can only go DOWN.
Adds the missing pieces from the wishlist that the existing
``test_quality_burndown_ratchets.py`` and
``test_use_proper_objects_ratchets.py`` files don't cover:

  * Untyped defs (private + public separately)
  * ``cast(...)`` count (different antipattern from ``# type: ignore``)
  * Logging inside exception handlers (typically swallows the exc)
  * Direct filesystem access outside the adapter layer
  * Direct network calls outside the client/service layer
  * Test files without assertions
  * Cyclomatic complexity per function (rough McCabe)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src" / "media_stack"
TESTS = REPO_ROOT / "tests"
RATCHETS_DIR = REPO_ROOT / ".ratchets"


# ---------------------------------------------------------------------------
# Burndown plumbing
# ---------------------------------------------------------------------------


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


def _iter_business_logic_files() -> list[Path]:
    if not SRC.is_dir():
        return []
    out = []
    for path in SRC.rglob("*.py"):
        if any(p in {"__pycache__", ".venv"} for p in path.parts):
            continue
        if path.name.startswith("test_"):
            continue
        out.append(path)
    return out


# ---------------------------------------------------------------------------
# 1. Untyped defs (public + private separately)
# ---------------------------------------------------------------------------


def _count_untyped_defs(*, public_only: bool, private_only: bool) -> int:
    count = 0
    for path in _iter_business_logic_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            is_private = node.name.startswith("_")
            if public_only and is_private:
                continue
            if private_only and not is_private:
                continue
            # Skip __dunder__ methods — they're usually typed via
            # protocols / ABC inheritance.
            if node.name.startswith("__") and node.name.endswith("__"):
                continue
            # Untyped iff missing return annotation OR any positional/
            # kwonly arg lacks an annotation (excluding ``self``/``cls``).
            args = list(node.args.args) + list(node.args.kwonlyargs)
            untyped_arg = any(
                a.annotation is None and a.arg not in ("self", "cls")
                for a in args
            )
            untyped_return = node.returns is None
            if untyped_arg or untyped_return:
                count += 1
    return count


def test_burndown_untyped_public_defs() -> None:
    """Public functions/methods without type hints. Public APIs are
    contract surfaces — every untyped one is a leak."""
    count = _count_untyped_defs(public_only=True, private_only=False)
    _enforce_burndown(
        "untyped-public-defs",
        count,
        hint=(
            "Add type hints to public functions and methods. The "
            "public surface is what callers depend on; an untyped "
            "public function silently spreads ``Any`` through every "
            "call site."
        ),
    )


def test_burndown_untyped_private_defs() -> None:
    """Private (``_name``) helpers without type hints. Lower priority
    than public, but still a quality target."""
    count = _count_untyped_defs(public_only=False, private_only=True)
    _enforce_burndown(
        "untyped-private-defs",
        count,
        hint=(
            "Add type hints to private helpers. Private = single-"
            "module scope, but ``Any`` propagation through a chain "
            "of helpers still defeats type-checking on the public "
            "method that calls them."
        ),
    )


# ---------------------------------------------------------------------------
# 2. cast(...) usage
# ---------------------------------------------------------------------------


_RE_TYPING_CAST = re.compile(r"\bcast\s*\(\s*[A-Za-z]")


def test_burndown_typing_cast_usage() -> None:
    """``typing.cast(T, x)`` is a runtime no-op that asserts a type
    to the type-checker. Each use is a hand-wave — the upstream
    function returned ``Any`` or a Union the local code couldn't
    narrow on its own. Often a sign the upstream function should be
    typed better, or a runtime check (isinstance) should live there."""
    count = 0
    for path in _iter_business_logic_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            stripped = line.lstrip()
            if (
                stripped.startswith("#")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                continue
            count += len(_RE_TYPING_CAST.findall(line))
    _enforce_burndown(
        "typing-cast-usage",
        count,
        hint=(
            "Prefer narrowing via ``isinstance`` (which both the "
            "type-checker AND the runtime see) over ``cast`` (which "
            "only the type-checker sees). If the upstream function "
            "returns ``Any``, fix that function's type. Each "
            "``cast()`` is a place where a runtime type confusion "
            "is silently possible."
        ),
    )


# ---------------------------------------------------------------------------
# 3. Logging inside except handlers (without re-raise / re-enrich)
# ---------------------------------------------------------------------------


def _count_logging_only_in_except() -> int:
    """Count ``except Foo: log.<level>(...)`` handlers where the
    only body statement is a log call. These typically swallow the
    exception without surfacing it; legit cases re-raise with
    context, escalate, or store the failure for telemetry."""
    count = 0
    for path in _iter_business_logic_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = node.body
            # Filter out ``pass``-only (already a separate ratchet
            # for silent-failure-count).
            if len(body) == 1 and isinstance(body[0], ast.Expr):
                call = body[0].value
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in {
                        "debug", "info", "warning", "error", "exception",
                        "log_swallowed",
                    }
                ):
                    count += 1
    return count


def test_burndown_logging_only_in_exception_handlers() -> None:
    """``except Foo: log.warning(...)`` — the exception is swallowed
    after one log line. Operators see the warning but downstream
    code keeps running with stale state."""
    count = _count_logging_only_in_except()
    _enforce_burndown(
        "logging-only-in-exception-handlers",
        count,
        hint=(
            "Either re-raise (``raise``) so the caller can decide, "
            "OR set a structured failure flag the caller can check, "
            "OR use ``log_swallowed(exc)`` (the project helper) "
            "WITH a fallback action — never log-and-continue without "
            "either re-raising or substituting a default value."
        ),
    )


# ---------------------------------------------------------------------------
# 4. Direct filesystem access outside the adapter layer
# ---------------------------------------------------------------------------


_RE_FS_DIRECT = re.compile(
    r"\b(?:open\(|Path\(|os\.path\.|os\.makedirs\(|os\.remove\(|"
    r"os\.rmdir\(|shutil\.|pathlib\.Path|\.read_text\(|\.write_text\(|"
    r"\.read_bytes\(|\.write_bytes\(|\.unlink\(|\.mkdir\(|\.is_file\()",
)

_FS_ALLOWED_PATH_FRAGMENTS = (
    # Adapter layer — translating disk ↔ domain.
    "/adapters/",
    # Snapshot service writes archives.
    "/api/services/snapshots.py",
    # Config-resolution helpers OWN file paths.
    "/api/services/_resolve.py",
    "/core/defaults.py",
    "/core/cli_common.py",
    # Job framework persists history.
    "/application/jobs/framework.py",
    "/services/jobs/",
    # CLI entry points read profile files.
    "/cli/",
    # Versioning reads VERSION at import.
    "/version.py",
    # The api server / handlers serve openapi.yaml.
    # ADR-0007 Phase E cleanup: handlers_get/post.py deleted; their
    # filesystem-touching helpers live in route + service modules.
    "/api/server.py",
    "/api/services/openapi.py",
    "/api/services/logs_handlers.py",
    "/api/services/media_integrity_dispatch.py",
    "/api/services/media_integrity_handlers.py",
    "/api/services/security_get_handlers.py",
    "/api/services/security_post_handlers.py",
    "/api/routes/",
    "/api/routing/",
    # Audit log writes JSONL.
    "/api/services/audit_log.py",
    # State persistence.
    "/api/state.py",
    # Disk service IS the disk facade.
    "/api/services/disk.py",
    "/api/services/health.py",  # health-history persistence
    "/api/services/ops.py",  # archive log scan
    # Routing config services own disk files.
    "/api/services/config/",
    # Edge config writers.
    "/services/edge/",
    "/api/services/k8s_ingress_sync.py",
    "/api/services/auth_config.py",
    # App-config writers.
    "/services/app_config_service.py",
    "/services/branding/",
    "/services/livetv_config_service.py",
    # Stack update reads/writes upgrade state.
    "/api/services/stack_update.py",
    # Auth flow needs to read TLS / Authelia configs.
    "/core/auth/",
    "/domain/auth/",
    "/core/edge/",
    # Manifest/diagnostic services walk YAML on disk.
    "/api/services/_diagnostics.py",
    "/api/contract_validator.py",
    # Servarr/maintainerr/jellyseerr clients persist state files.
    "/services/apps/",
    # Bootstrap orchestrator loads contracts.
    "/services/bootstrap/",
    "/services/registry_loader.py",
    # Logging utilities.
    "/core/logging_utils.py",
)


def _count_pattern_outside_allowlist(
    pattern: re.Pattern[str],
    allowlist: tuple[str, ...],
) -> int:
    count = 0
    for path in _iter_business_logic_files():
        rel = "/" + str(path.relative_to(SRC)).replace("\\", "/")
        if any(frag in rel or frag in str(path) for frag in allowlist):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            stripped = line.lstrip()
            if (
                stripped.startswith("#")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                continue
            count += len(pattern.findall(line))
    return count


def test_burndown_direct_filesystem_access_outside_adapter() -> None:
    """``open()`` / ``Path(...).read_text()`` / ``os.makedirs`` etc.
    in business logic = an inline filesystem dependency. Tests get
    harder, alternative storage backends become impossible, and the
    layering bends."""
    count = _count_pattern_outside_allowlist(
        _RE_FS_DIRECT, _FS_ALLOWED_PATH_FRAGMENTS,
    )
    _enforce_burndown(
        "direct-filesystem-access-outside-adapter",
        count,
        hint=(
            "Move filesystem reads/writes behind an adapter "
            "(``XStore`` / ``XRepository``). Business logic should "
            "depend on the adapter interface; production wires the "
            "filesystem implementation, tests can wire an in-memory "
            "fake. Layering: domain ← application ← adapters ← "
            "filesystem."
        ),
    )


# ---------------------------------------------------------------------------
# 5. Direct network calls outside the client/service layer
# ---------------------------------------------------------------------------


_RE_NETWORK_DIRECT = re.compile(
    r"\b(?:urlopen\s*\(|urllib\.request\.|requests\.(?:get|post|put|"
    r"delete|patch|head)\(|httpx\.|aiohttp\.|"
    r"socket\.(?:socket|create_connection|getaddrinfo))",
)

_NETWORK_ALLOWED_PATH_FRAGMENTS = (
    "/adapters/",
    "/services/apps/",  # arr/maintainerr/jellyseerr clients
    "/api/services/auto_heal.py",
    "/api/services/metrics.py",  # envoy admin probe
    "/api/services/health.py",  # service probes
    "/api/services/content.py",  # arr version probes
    "/api/services/disk.py",  # smartctl etc.
    "/api/services/stack_update.py",  # registry tag fetch
    "/api/services/ops.py",  # log fetcher uses k8s SDK
    "/api/services/k8s_ingress_sync.py",
    "/api/services/_security.py",
    "/api/services/security_get_deps.py",
    "/api/services/_routing.py",
    "/services/edge/",
    "/services/livetv_config_service.py",  # IPTV probes
    "/services/bootstrap/",
    "/services/registry_loader.py",
    "/core/auth/",
    "/domain/auth/",
    "/core/edge/",
    "/cli/",
    # ADR-0007 Phase E cleanup: handlers_get/post.py deleted; their
    # network-touching probes live in route + service modules.
    "/api/routes/",
    "/api/routing/",
    "/api/services/routing_probes.py",  # routing probes (was handlers_get)
    "/api/services/route_probe.py",
    "/api/services/logs_handlers.py",
    "/api/services/media_integrity_dispatch.py",
    "/api/services/media_integrity_handlers.py",
    "/api/services/security_get_handlers.py",
    "/api/services/security_post_handlers.py",
    # The dispatch layer makes outbound HTTP for ext_authz callbacks.
    "/api/dispatch.py",
    # ``_ProbeHttpClient`` IS the client layer for orchestrator
    # promise probes — owns redirect policy, TLS skip-verify for
    # synthetic gateway URLs, and header extraction. Ratchet
    # recognises the role; the file just doesn't carry a ``client``
    # suffix in its name.
    "/infrastructure/promises/dispatcher.py",
)


def test_burndown_direct_network_calls_outside_client_layer() -> None:
    """``urlopen`` / ``requests.get`` / ``socket.create_connection``
    in business logic indicate an inline network dependency. Move
    them behind a client class so retries, timeouts, and
    auth-injection live in one place."""
    count = _count_pattern_outside_allowlist(
        _RE_NETWORK_DIRECT, _NETWORK_ALLOWED_PATH_FRAGMENTS,
    )
    _enforce_burndown(
        "direct-network-calls-outside-client-layer",
        count,
        hint=(
            "Move the outbound HTTP/socket call behind a client "
            "class (``ServarrClient``, ``AutheliaClient``, "
            "``EnvoyAdminClient``). The client owns retries, "
            "timeout policy, auth-header construction, and error "
            "translation. Business logic calls "
            "``client.fetch_X()`` and gets a typed result back."
        ),
    )


# ---------------------------------------------------------------------------
# 6. Test files without assertions
# ---------------------------------------------------------------------------


def _count_tests_without_assertions() -> int:
    """Test functions whose body has no ``assert``, ``pytest.raises``,
    ``self.assertEqual``, or similar. A test without an assertion
    just exercises code without verifying behavior."""
    count = 0
    if not TESTS.is_dir():
        return 0

    def _has_assertion(node: ast.AST) -> bool:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assert):
                return True
            if isinstance(sub, ast.Call):
                func = sub.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr.startswith(("assert", "assert_"))
                ):
                    return True
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "pytest"
                    and func.attr in ("raises", "warns", "fail")
                ):
                    return True
                if (
                    isinstance(func, ast.Name)
                    and func.id in ("expect", "should", "ensure")
                ):
                    return True
        return False

    for path in TESTS.rglob("test_*.py"):
        if any(p in {"__pycache__"} for p in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            if not node.name.startswith("test_"):
                continue
            if not _has_assertion(node):
                count += 1
    return count


def test_burndown_tests_without_assertions() -> None:
    """Test functions whose body never asserts. Each one is dead
    weight masquerading as coverage."""
    count = _count_tests_without_assertions()
    _enforce_burndown(
        "tests-without-assertions",
        count,
        hint=(
            "Either add an ``assert`` (or ``pytest.raises`` / "
            "``self.assertEqual`` / similar), or delete the test. "
            "A test that only runs code without verifying outcomes "
            "fails noisily on syntax errors but silently on logic "
            "bugs — worse than no test."
        ),
    )


# ---------------------------------------------------------------------------
# 7. Cyclomatic complexity per function (rough McCabe)
# ---------------------------------------------------------------------------


def _function_complexity(node: ast.AST) -> int:
    """McCabe-ish branch count: 1 + each decision point."""
    score = 1
    for sub in ast.walk(node):
        if isinstance(sub, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            score += 1
        elif isinstance(sub, ast.BoolOp):
            score += max(0, len(sub.values) - 1)
        elif isinstance(sub, ast.ExceptHandler):
            score += 1
        elif isinstance(sub, ast.Try):
            # The try itself doesn't add; its handlers do (above).
            pass
        elif isinstance(sub, ast.IfExp):
            score += 1
        elif isinstance(sub, ast.comprehension):
            score += 1 + len(sub.ifs)
        elif hasattr(ast, "Match") and isinstance(sub, ast.Match):
            # match/case: each case clause is a branch.
            score += len(sub.cases)
    return score


def _count_functions_over_complexity(threshold: int) -> int:
    count = 0
    for path in _iter_business_logic_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                continue
            if _function_complexity(node) > threshold:
                count += 1
    return count


def test_burndown_cyclomatic_complexity_over_10() -> None:
    """Functions with McCabe-ish complexity > 10. The threshold is
    the canonical "split this" line — higher means too many decision
    points to follow without a state diagram."""
    count = _count_functions_over_complexity(10)
    _enforce_burndown(
        "cyclomatic-complexity-over-10",
        count,
        hint=(
            "Extract subroutines, lift conditionals into a strategy "
            "table, or split into multiple functions. Complexity > 10 "
            "is where bugs hide and unit tests stop fitting on one "
            "screen."
        ),
    )


def test_burndown_cyclomatic_complexity_over_15() -> None:
    """Stricter variant — complexity > 15 is "really has to go"."""
    count = _count_functions_over_complexity(15)
    _enforce_burndown(
        "cyclomatic-complexity-over-15",
        count,
        hint=(
            "Same playbook as the >10 variant; >15 is the urgent "
            "tier. These functions are very hard to test "
            "comprehensively — one path missed is a real-world bug."
        ),
    )
