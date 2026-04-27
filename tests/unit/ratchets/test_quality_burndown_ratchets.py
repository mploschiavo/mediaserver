"""Burndown ratchets for the operator-facing code-quality wishlist.

Each test compares a current count against a pinned baseline at
``.ratchets/<name>-baseline.txt``. The baseline is "current state when
the ratchet landed"; the test fails if the count goes UP, never if it
goes down. A separate ``test_baseline_does_not_overshoot_*`` companion
tightens the baseline whenever a wave drives the count well below it.

Hard-gate variants (counts that MUST be 0) live alongside the burndowns
in this file but assert against zero rather than a baseline file.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
UI_SRC = REPO_ROOT / "ui" / "src"
SRC = REPO_ROOT / "src" / "media_stack"
RATCHETS_DIR = REPO_ROOT / ".ratchets"


# ---------------------------------------------------------------------------
# Helpers
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


def _scan_files(
    root: Path,
    suffixes: tuple[str, ...],
    pattern: re.Pattern[str],
    *,
    skip_dirs: tuple[str, ...] = ("__pycache__", "node_modules", ".venv"),
    skip_test_files: bool = False,
) -> int:
    count = 0
    if not root.is_dir():
        return 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in suffixes:
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if skip_test_files and (
            ".test." in path.name or path.name.startswith("test_")
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        count += len(pattern.findall(text))
    return count


def _enforce_burndown(name: str, current: int, *, hint: str) -> None:
    baseline = _load_baseline(name)
    if baseline is None:
        _seed_baseline(name, current)
        return
    if current > baseline:
        raise AssertionError(
            f"{name}: regressed from {baseline} → {current}.\n"
            f"{hint}\n\n"
            f"To accept the new count: edit "
            f".ratchets/{name}-baseline.txt up to {current}, but the "
            f"intent of this ratchet is the OPPOSITE direction — fix "
            f"the new offenders instead."
        )


def _tighten_baseline(name: str, current: int) -> None:
    baseline = _load_baseline(name)
    if baseline is None or baseline <= 0:
        return
    if baseline - current > 2:
        raise AssertionError(
            f"{name}: baseline {baseline} overshoots current count "
            f"{current} by {baseline - current}. Tighten by editing "
            f".ratchets/{name}-baseline.txt down to {current}."
        )


# ---------------------------------------------------------------------------
# Hard gates — these MUST be 0 in the codebase.
# ---------------------------------------------------------------------------


_RE_TS_IGNORE = re.compile(r"//\s*@ts-ignore\b")


def test_hard_gate_ts_ignore_zero() -> None:
    """``@ts-ignore`` is a hammer — every use papers over a real type
    error. We allow ``@ts-expect-error`` (which fails build if the
    error goes away) but not ``@ts-ignore``. New code: fix the type."""
    count = _scan_files(UI_SRC, (".ts", ".tsx"), _RE_TS_IGNORE)
    if count > 0:
        raise AssertionError(
            f"@ts-ignore is a hard-banned hammer; found {count} use(s). "
            f"Use ``@ts-expect-error <reason>`` instead — it fails the "
            f"build when the underlying type problem is fixed, so the "
            f"silencer doesn't outlive the bug."
        )


def test_burndown_actions_not_pinned_to_sha() -> None:
    """Every ``uses: <action>@<ref>`` in GitHub workflows should pin
    to a commit SHA, not a tag/branch. Pinning by tag means a
    malicious push to that tag silently runs in CI with our secrets;
    SHAs are immutable. Currently a burndown — once at zero, promote
    to a hard gate by editing the baseline file to 0 and assert it."""
    workflows = REPO_ROOT / ".github" / "workflows"
    if not workflows.is_dir():
        return
    pat = re.compile(r"uses:\s*([^\s#]+)")
    count = 0
    for path in sorted(workflows.glob("*.y*ml")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in pat.finditer(text):
            ref = m.group(1)
            if "@" not in ref:
                continue
            after_at = ref.rsplit("@", 1)[1]
            if not re.fullmatch(r"[a-f0-9]{40}", after_at):
                count += 1
    _enforce_burndown(
        "actions-not-pinned-sha",
        count,
        hint=(
            "Replace ``@v3`` (etc.) with the full 40-char SHA from "
            "that release, and add a comment with the version name. "
            "Renovate/Dependabot keeps them current. Promote this "
            "to a hard gate (assert == 0) once the baseline hits 0."
        ),
    )


# ---------------------------------------------------------------------------
# Soft burndowns — current count is the baseline; can only go DOWN.
# ---------------------------------------------------------------------------


_RE_TS_EXPECT_ERROR = re.compile(r"//\s*@ts-expect-error\b")
_RE_TS_ANY_TYPE = re.compile(r":\s*any\b|<any>|as\s+any\b")
_RE_TS_NON_NULL_ASSERT = re.compile(r"[A-Za-z_$\]\)]\![\.\(]")
_RE_TS_CONSOLE = re.compile(r"\bconsole\.(log|info|debug|warn|error)\(")
_RE_PY_TYPE_IGNORE = re.compile(r"#\s*type:\s*ignore\b")
_RE_PY_BROAD_EXCEPT = re.compile(r"except\s+Exception\s*[:\(]")
_RE_PY_DATETIME_NAIVE = re.compile(
    r"datetime\.(?:datetime\.)?(?:now|utcnow)\(\s*\)",
)


def test_burndown_ts_expect_error() -> None:
    """``@ts-expect-error`` is allowed but tracked. Each use is a
    silent bypass of the type system — the reason should be in the
    inline comment and the count should trend toward zero."""
    count = _scan_files(UI_SRC, (".ts", ".tsx"), _RE_TS_EXPECT_ERROR)
    _enforce_burndown(
        "ts-expect-error",
        count,
        hint=(
            "Each ``@ts-expect-error`` silences a type error. New "
            "additions must come with a comment explaining the bug "
            "class — and ideally a tracking ticket. Remove or fix to "
            "decrement the count."
        ),
    )


def test_burndown_ts_any_usage() -> None:
    """Counts ``: any``, ``as any``, ``<any>`` patterns. Each is a
    hole in the type system. The codebase started at this count when
    the ratchet landed; new code must not introduce more."""
    count = _scan_files(
        UI_SRC, (".ts", ".tsx"), _RE_TS_ANY_TYPE, skip_test_files=False,
    )
    _enforce_burndown(
        "ts-any-usage",
        count,
        hint=(
            "Replace ``: any`` with a real type, an ``unknown`` plus "
            "narrowing, or a generic. Test files are NOT exempt — "
            "they should mock with proper types too."
        ),
    )


def test_burndown_ts_non_null_assertion() -> None:
    """Counts non-null assertions (``foo!.bar``, ``arr![0]``). Each
    is a hand-wave — the type system thinks the value might be
    null/undefined and we're overriding it. Burndown encourages
    proper narrowing or optional chaining."""
    count = _scan_files(UI_SRC, (".ts", ".tsx"), _RE_TS_NON_NULL_ASSERT)
    _enforce_burndown(
        "ts-non-null-assertion",
        count,
        hint=(
            "Use a proper narrow (``if (foo) ...``) or optional "
            "chaining (``foo?.bar``) instead of ``foo!``. Each ``!`` "
            "is one runtime crash you'll someday hit."
        ),
    )


def test_burndown_ts_console_statements() -> None:
    """Counts ``console.log/info/debug/warn/error`` calls. Production
    code should use the structured logger; ``console`` calls are
    debug leftovers. Test files are exempt."""
    count = _scan_files(
        UI_SRC, (".ts", ".tsx"), _RE_TS_CONSOLE, skip_test_files=True,
    )
    _enforce_burndown(
        "ts-console-statements",
        count,
        hint=(
            "Replace ``console.log`` with the structured logger so "
            "log output is consistent and filterable. Test files are "
            "exempt; only production source counts."
        ),
    )


def test_burndown_python_type_ignore() -> None:
    """Counts ``# type: ignore`` lines in Python source. Each is a
    silent bypass; we want them dropping toward zero or carrying an
    inline reason."""
    count = _scan_files(SRC, (".py",), _RE_PY_TYPE_IGNORE)
    _enforce_burndown(
        "python-type-ignore",
        count,
        hint=(
            "Use ``# type: ignore[<rule>]  # reason: ...`` so the "
            "reviewer can tell what's being silenced. Better: fix "
            "the underlying typing problem."
        ),
    )


def test_burndown_python_broad_except() -> None:
    """Counts ``except Exception:`` clauses. Broad excepts swallow
    bugs; named exception classes are almost always better."""
    count = _scan_files(SRC, (".py",), _RE_PY_BROAD_EXCEPT)
    _enforce_burndown(
        "python-broad-except",
        count,
        hint=(
            "Replace ``except Exception:`` with the specific class "
            "you mean (``OSError``, ``json.JSONDecodeError``, etc.). "
            "If you really need a catch-all (e.g. third-party iface "
            "throws ``BaseException`` subclasses), add an inline "
            "comment explaining why and consider ``except Exception "
            "as exc`` + ``log_swallowed(exc)`` so it surfaces."
        ),
    )


def test_burndown_python_datetime_naive() -> None:
    """Counts ``datetime.now()``/``datetime.utcnow()`` without an
    explicit ``tz=`` argument. Naive datetimes silently break in
    cross-tz comparisons."""
    count = _scan_files(SRC, (".py",), _RE_PY_DATETIME_NAIVE)
    _enforce_burndown(
        "python-datetime-naive",
        count,
        hint=(
            "Always pass ``tz=timezone.utc`` (or another explicit tz). "
            "Naive datetimes silently mis-compare against tz-aware "
            "ones and break across daylight saving."
        ),
    )


# ---------------------------------------------------------------------------
# K8s deployment ratchets
# ---------------------------------------------------------------------------


def _scan_k8s_yaml() -> list[Path]:
    base = REPO_ROOT / "deploy" / "k8s"
    if not base.is_dir():
        return []
    return [
        p for p in base.rglob("*.yaml")
        if p.is_file() and not any(part == "_archive" for part in p.parts)
    ]


def test_burndown_k8s_missing_resources() -> None:
    """Counts container specs that lack ``resources.limits`` or
    ``resources.requests``. Without resource hints, k8s schedules
    blindly and one runaway pod can starve the node."""
    paths = _scan_k8s_yaml()
    if not paths:
        return
    import yaml as _yaml
    bad = 0
    for path in paths:
        try:
            for doc in _yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if not isinstance(doc, dict):
                    continue
                kind = doc.get("kind")
                if kind not in ("Deployment", "StatefulSet", "DaemonSet"):
                    continue
                spec = (doc.get("spec") or {}).get("template", {}).get("spec", {})
                for c in (spec.get("containers") or []):
                    res = c.get("resources") or {}
                    if not res.get("limits") and not res.get("requests"):
                        bad += 1
        except Exception:
            continue
    _enforce_burndown(
        "k8s-missing-resources",
        bad,
        hint=(
            "Every container needs at least ``resources.requests`` so "
            "the scheduler knows how to bin-pack. Limits prevent one "
            "pod from starving a neighbor."
        ),
    )


def test_burndown_k8s_missing_probes() -> None:
    """Counts containers without a readiness OR liveness probe.
    Without probes, k8s can't tell when a pod is unhealthy and rolls
    crash-looping pods into the load balancer."""
    paths = _scan_k8s_yaml()
    if not paths:
        return
    import yaml as _yaml
    bad = 0
    for path in paths:
        try:
            for doc in _yaml.safe_load_all(path.read_text(encoding="utf-8")):
                if not isinstance(doc, dict):
                    continue
                if doc.get("kind") not in ("Deployment", "StatefulSet"):
                    continue
                spec = (doc.get("spec") or {}).get("template", {}).get("spec", {})
                for c in (spec.get("containers") or []):
                    if not c.get("readinessProbe") and not c.get("livenessProbe"):
                        bad += 1
        except Exception:
            continue
    _enforce_burndown(
        "k8s-missing-probes",
        bad,
        hint=(
            "Add a ``readinessProbe`` (gate traffic on health) or "
            "``livenessProbe`` (restart on hang). Use the ``/healthz`` "
            "or ``/readyz`` endpoint each container exposes."
        ),
    )


# ---------------------------------------------------------------------------
# Docker ratchets
# ---------------------------------------------------------------------------


def test_burndown_docker_latest_tag_in_compose() -> None:
    """Counts ``image: ...:latest`` references in compose YAML.
    ``latest`` is a moving target — operators get inconsistent stacks
    on docker pull. Pin a version (or a SHA) instead."""
    base = REPO_ROOT / "deploy" / "compose"
    if not base.is_dir():
        return
    pat = re.compile(r"image:\s*[^\s#]+:latest\b")
    count = 0
    for path in base.rglob("*.y*ml"):
        try:
            count += len(pat.findall(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    _enforce_burndown(
        "docker-latest-tag-compose",
        count,
        hint=(
            "Replace ``:latest`` with a specific version tag or pin "
            "by digest. The controller and UI images already version "
            "their tags via VERSION/VERSION-UI; sister apps should "
            "follow the same pattern (or use ``${VERSION:-latest}`` "
            "with a real default)."
        ),
    )


# ---------------------------------------------------------------------------
# Test-suite hygiene
# ---------------------------------------------------------------------------


_RE_PY_SKIP = re.compile(
    r"@(?:pytest\.)?mark\.(?:skip|skipif|xfail)\b|pytest\.skip\(",
)


def test_burndown_skipped_tests() -> None:
    """Counts ``@pytest.mark.skip``/``skipif``/``xfail`` annotations
    AND inline ``pytest.skip()`` calls. A skipped test is dead code
    that pretends to be live coverage."""
    tests_root = REPO_ROOT / "tests"
    if not tests_root.is_dir():
        return
    count = _scan_files(tests_root, (".py",), _RE_PY_SKIP)
    _enforce_burndown(
        "skipped-tests",
        count,
        hint=(
            "Either run the test or delete it. Skipping forever is a "
            "lie about coverage. Conditional skip-on-OS / "
            "skip-on-missing-binary should be exempt — add the file "
            "to a permanent allowlist if so."
        ),
    )
