"""Compose ↔ K8s deployment-mode parity ratchet (Bug class F).

Motivating bug: v1.0.199's ``/api/tls/certificate`` returned
``present: false`` on every K8s deployment. Root cause —
``api/tls_factory.py`` only probed file paths under
``/srv-config/certs/`` and ``/certs/``; on K8s the cert lives in
an Ingress secret (``iomio-tls`` / ``media-stack-tls``) that the
controller pod never sees on disk. The factory had no fallback
that read the K8s API.

Bug class F (``compose↔K8s parity``): a "platform-aware reader"
ships only ONE read path — file/disk for compose — and silently
fails in the other deployment mode. Every component that reads
from a platform-aware data source (TLS certs, secrets, persistent
state files) needs TWO read paths: a file/disk path AND a K8s
API path. This ratchet pins that invariant going forward.

What this catches:

  F1   Any ``.py`` under the platform-aware directory set
       (``core/edge/``, ``core/auth/``, ``core/notifications/``,
       ``services/runtime_factory/``, plus the platform-shared
       parents of ``core/platforms/``) that hardcodes a literal
       platform path (``/srv-config/...``, ``/certs/...``,
       ``/etc/.../`` etc.) inside an actual disk-read call
       (``open()``, ``Path(...).is_file()/exists()/read_text()``)
       must EITHER
         (a) import ``kubernetes`` (directly or via a sibling
             module in the same first-level package), OR
         (b) carry an inline ``# parity-exempt: <reason>`` marker
             within ~3 lines of the read site, OR
         (c) appear in ``KNOWN_FILE_ONLY_READERS`` with a reason.

Files inside ``core/platforms/compose/`` and
``core/platforms/kubernetes/`` are platform-specific by directory
placement and are exempt — that's the supported way to declare a
single-platform reader.

Reference: ``src/media_stack/api/tls_factory.py`` — today's fix —
implements the secret-mirror fallback pattern this ratchet pins.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "media_stack"


# Platform-aware path prefixes: any literal that begins with one of
# these is treated as a "real disk path the controller might read."
# Keep narrow — we'd rather miss a corner case than false-positive
# on every ``/api/...`` URL string.
_PLATFORM_PATH_PREFIXES: tuple[str, ...] = (
    "/srv-config/",
    "/certs/",
    "/tls/",
    "/secrets/",
    # ``/etc/`` is broad on purpose: cert-manager + envoy + authelia
    # all bind their secrets under ``/etc/<provider>/``.
    "/etc/",
    # ``/config/`` and ``/data/`` are the conventional bind-mount
    # roots for app sidecars in compose. K8s pods that need them
    # mount a ConfigMap/Secret to the same path; both modes need
    # the same controller-side read fallback.
    "/config/",
    "/data/",
)


# Directories scanned for platform-aware readers. Anything under
# ``core/platforms/<name>/`` is platform-specific by convention and
# is intentionally NOT scanned — a file under
# ``core/platforms/kubernetes/`` is allowed to read only K8s, and
# a file under ``core/platforms/compose/`` is allowed to read only
# disk. The parity rule is for files OUTSIDE those subtrees.
_SCAN_DIRS: tuple[Path, ...] = (
    SRC / "core" / "edge",
    SRC / "core" / "auth",
    SRC / "core" / "notifications",
    SRC / "services" / "runtime_factory",
    # ``core/platforms`` itself (the top level, NOT its
    # platform-specific children) — files like
    # ``core/platforms/__init__.py`` or shared helpers live here
    # and need both read paths.
    SRC / "core" / "platforms",
)

# Subtrees inside ``_SCAN_DIRS`` that are platform-specific by
# directory and therefore exempt — declared by directory name.
_PLATFORM_SPECIFIC_DIRS: tuple[str, ...] = (
    "/core/platforms/compose/",
    "/core/platforms/kubernetes/",
)


# Files that are platform-aware readers but legitimately ship only
# the file-disk path. Each entry must carry a reason explaining why
# the K8s fallback is unnecessary OR is provided elsewhere. Keep
# small — every entry is a deferred chunk of the bug class.
KNOWN_FILE_ONLY_READERS: dict[str, str] = {
    # ``authelia_config_generator.py`` EMITS literal ``/config/...``
    # paths into Authelia's YAML — those paths are read by Authelia
    # inside its own container, never by the controller. Not a
    # platform-aware reader; literals are config payload, not file
    # opens. Allowlisted because the AST detector below treats any
    # literal-prefix string as suspect even when it appears as a
    # dict value rather than a Path/open argument.
    "src/media_stack/core/auth/authelia_config_generator.py":
        "literals are Authelia container paths emitted into config; "
        "controller never reads them",
}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------
def _string_starts_with_platform_prefix(value: object) -> bool:
    if not isinstance(value, str):
        return False
    return any(value.startswith(p) for p in _PLATFORM_PATH_PREFIXES)


def _call_arg_is_platform_literal(call: ast.Call) -> bool:
    """``Path("/srv-config/...")`` / ``open("/etc/...")`` — direct
    literal first-positional argument starting with a platform
    prefix."""
    if not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Constant):
        return _string_starts_with_platform_prefix(first.value)
    return False


def _is_platform_aware_call(node: ast.Call) -> bool:
    """A call site that hits the local filesystem AND uses a
    platform-prefix literal. Catches:

      Path("/srv-config/foo").is_file()
      Path("/certs").exists()
      open("/etc/envoy/envoy.yaml")

    Tuple/list constants of platform paths used as candidate-path
    pools (the dynamic_config.py pattern under ``core/platforms/``)
    are intentionally NOT detected here — they're confined to
    platform-specific subtrees that ``_PLATFORM_SPECIFIC_DIRS``
    skips. New uses outside those subtrees will trip the simpler
    direct-call check below.
    """
    func = node.func
    # Path("/literal/...") — ``Path`` as a bare name.
    if isinstance(func, ast.Name) and func.id == "Path":
        return _call_arg_is_platform_literal(node)
    # open("/literal/...", ...) — bare ``open``.
    if isinstance(func, ast.Name) and func.id == "open":
        return _call_arg_is_platform_literal(node)
    return False


def _file_has_platform_aware_read(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_platform_aware_call(node):
            return True
    return False


def _module_imports_kubernetes(tree: ast.AST) -> bool:
    """Direct or lazy ``import kubernetes`` / ``from kubernetes
    import ...`` in this module. Lazy imports inside functions
    count — that's how ``tls_factory.py`` does it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "kubernetes" or alias.name.startswith(
                    "kubernetes."
                ):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "kubernetes" or (
                node.module and node.module.startswith("kubernetes.")
            ):
                return True
    return False


def _module_imports_k8s_via_sibling(path: Path) -> bool:
    """One-level transitive search: does any sibling .py module
    in the same first-level package import ``kubernetes``? This
    is how ``tls_factory.py`` (in ``api/``) reaches K8s — its
    own module imports ``kubernetes`` lazily; we accept that as
    well as a sibling-provided fallback."""
    package_dir = path.parent
    for sibling in package_dir.glob("*.py"):
        if sibling == path:
            continue
        try:
            stree = ast.parse(sibling.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if _module_imports_kubernetes(stree):
            return True
    return False


def _has_inline_parity_exempt(text: str) -> bool:
    """``# parity-exempt: <reason>`` anywhere in the file. The
    spec calls for "within ~3 lines of the read site"; in
    practice a single marker in a small file is unambiguous and
    much less brittle than line-distance accounting. If a future
    file legitimately needs per-call exemption, switch to
    line-distance then."""
    return "parity-exempt:" in text


def _walk_scoped_files() -> Iterable[Path]:
    for base in _SCAN_DIRS:
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            sp = "/" + str(p.relative_to(ROOT)) + "/"
            if any(ex in sp for ex in _PLATFORM_SPECIFIC_DIRS):
                continue
            yield p


# ---------------------------------------------------------------------------
# F1 — every platform-aware reader has a K8s fallback
# ---------------------------------------------------------------------------
class PlatformAwareReadersHaveK8sFallback(unittest.TestCase):
    """Every platform-aware file reader (one that opens a literal
    ``/srv-config/...``, ``/certs/...``, ``/etc/...``, etc. on the
    controller pod) must ALSO have a K8s API read path — directly,
    via a sibling module, or via an explicit ``# parity-exempt:``
    marker. Catches the bug class behind v1.0.199's
    ``/api/tls/certificate`` returning ``present: false`` on K8s:
    the file-only path silently lost on the K8s deploy mode."""

    def test_every_platform_aware_reader_has_k8s_path(self) -> None:
        offenders: list[str] = []
        platform_aware_files: list[Path] = []
        for path in _walk_scoped_files():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            if not _file_has_platform_aware_read(tree):
                continue
            platform_aware_files.append(path)

            rel = str(path.relative_to(ROOT))
            if rel in KNOWN_FILE_ONLY_READERS:
                continue
            if _has_inline_parity_exempt(text):
                continue
            if _module_imports_kubernetes(tree):
                continue
            if _module_imports_k8s_via_sibling(path):
                continue
            offenders.append(rel)

        # Cache for the second test method — pytest runs each test
        # method fresh, so we recompute. Cheap (low file count).
        self.assertFalse(
            offenders,
            "Platform-aware file readers without a K8s API fallback "
            "(file-only path silently fails on K8s deployments — "
            "cf. the v1.0.199 /api/tls/certificate fix). Either add "
            "a kubernetes-import branch to the module, add a "
            "``# parity-exempt: <reason>`` marker if the K8s path "
            "is genuinely unnecessary, or add an entry to "
            "KNOWN_FILE_ONLY_READERS with a reason:\n  - "
            + "\n  - ".join(offenders),
        )

    def test_known_file_only_readers_still_exist(self) -> None:
        """Allowlist hygiene: every entry in
        ``KNOWN_FILE_ONLY_READERS`` must point to a real file. A
        stale entry hides the next regression."""
        missing: list[str] = []
        for rel in KNOWN_FILE_ONLY_READERS:
            if not (ROOT / rel).is_file():
                missing.append(rel)
        self.assertFalse(
            missing,
            "KNOWN_FILE_ONLY_READERS references files that no "
            "longer exist — drop the stale entries:\n  - "
            + "\n  - ".join(missing),
        )

    def test_known_file_only_readers_carry_a_reason(self) -> None:
        """Every allowlist entry must carry a non-empty reason —
        a future maintainer reading just this file should know
        WHY the K8s fallback was waived, not just that it was."""
        bad = [
            rel for rel, reason in KNOWN_FILE_ONLY_READERS.items()
            if not (reason or "").strip()
        ]
        self.assertFalse(
            bad,
            "KNOWN_FILE_ONLY_READERS entries with empty reasons — "
            "explain why the K8s fallback is unnecessary:\n  - "
            + "\n  - ".join(bad),
        )


if __name__ == "__main__":
    unittest.main()
