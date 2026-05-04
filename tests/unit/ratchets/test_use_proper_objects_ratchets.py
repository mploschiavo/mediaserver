"""Ratchets that push code toward "use the proper object / named
constant" instead of hardcoding raw values.

Each test counts a specific antipattern and pins the count in
``.ratchets/<name>-baseline.txt``. The count can only go DOWN —
operators chip away at the existing offenders one PR at a time.

Why this matters
================
Hardcoded values rot:

  * **HTTP status ints** (``return 200``) bypass ``HTTPStatus.OK``;
    a typo (``201`` vs ``200``) compiles silently.
  * **Hardcoded service hostnames** (``"http://authelia:9091"``)
    bypass the service registry; renaming the service or moving its
    port means a grep-and-replace marathon.
  * **Hardcoded timeouts** (``timeout=5``) bypass any tunable —
    operators can't widen them without editing every call-site.
  * **Hardcoded paths** (``"/srv-config"``, ``"/opt/media-stack"``)
    bypass the config root resolver; tests that mount alternative
    paths break, container layouts that move the path break, etc.
  * **Repeated date/time format strings** (``"%Y-%m-%dT%H:%M:%S"``
    in five files) drift apart over time — one gets ``Z`` appended,
    another swaps to a space separator, and now timestamps don't
    sort consistently.

Each ratchet documents the proper alternative in its hint string so
the reviewer of a regression knows exactly how to fix it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src" / "media_stack"
RATCHETS_DIR = REPO_ROOT / ".ratchets"


# ---------------------------------------------------------------------------
# Burndown plumbing (mirrors test_quality_burndown_ratchets.py).
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
            f"{name}: regressed from {baseline} → {current}.\n"
            f"{hint}\n\n"
            f"To accept the new count: edit "
            f".ratchets/{name}-baseline.txt up to {current}, but the "
            f"intent of this ratchet is the OPPOSITE direction — fix "
            f"the new offenders instead."
        )


def _scan_python_lines(
    pattern: re.Pattern[str],
    *,
    skip_test_files: bool = True,
    in_comment: bool = False,
) -> int:
    """Count regex matches across non-test ``.py`` files under ``src/``.
    Skips comments + docstrings unless ``in_comment=True``."""
    count = 0
    if not SRC.is_dir():
        return 0
    for path in SRC.rglob("*.py"):
        if any(part in {"__pycache__", ".venv"} for part in path.parts):
            continue
        if skip_test_files and (
            path.name.startswith("test_") or "/tests/" in str(path)
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            stripped = line.lstrip()
            if not in_comment and (
                stripped.startswith("#")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                continue
            if pattern.search(line):
                count += 1
    return count


# ---------------------------------------------------------------------------
# 1. HTTP status code literals
# ---------------------------------------------------------------------------


# Match common HTTP status codes used as bare integers in handler
# response sites — ``_json_response(200, ...)``, ``_raw_response(404,
# ...)``, ``_json_response(500, ...)``. The HTTPStatus enum is in the
# stdlib (``from http import HTTPStatus``) and reads as
# ``HTTPStatus.OK`` / ``HTTPStatus.NOT_FOUND`` / ``HTTPStatus
# .INTERNAL_SERVER_ERROR``.
_RE_HTTP_STATUS_INT = re.compile(
    r"_(?:json|raw)_response\s*\(\s*"
    r"(?:200|201|202|204|301|302|303|400|401|403|404|405|409|410|"
    r"412|413|415|418|422|429|500|501|502|503|504)\s*,",
)


def test_burndown_http_status_int_literal_in_response() -> None:
    """``_json_response(200, ...)`` → ``_json_response(HTTPStatus.OK,
    ...)``. The enum reads as documentation; the integer doesn't."""
    count = _scan_python_lines(_RE_HTTP_STATUS_INT)
    _enforce_burndown(
        "http-status-int-literal",
        count,
        hint=(
            "Replace bare integers in ``_json_response`` / "
            "``_raw_response`` calls with ``HTTPStatus.<NAME>`` (from "
            "``http`` stdlib). The enum reads as documentation, "
            "fails fast on typos (``HTTPStatus.OK_TYPO`` is a "
            "NameError), and gives the linter something to highlight."
        ),
    )


# ---------------------------------------------------------------------------
# 2. Hardcoded timeouts
# ---------------------------------------------------------------------------


# Match ``timeout=<int_or_float>`` — flags hardcoded numeric timeouts
# in urlopen / requests / SDK calls. Excludes ``timeout=None`` and
# variable references (``timeout=cfg.timeout``).
_RE_HARDCODED_TIMEOUT = re.compile(r"\btimeout\s*=\s*\d+(?:\.\d+)?\s*[,\)]")


def test_burndown_hardcoded_timeout_literals() -> None:
    """``timeout=5`` should reference a named constant or config
    value so operators can tune all upstream timeouts in one place."""
    count = _scan_python_lines(_RE_HARDCODED_TIMEOUT)
    _enforce_burndown(
        "hardcoded-timeout-literals",
        count,
        hint=(
            "Replace ``timeout=N`` with a named constant from the "
            "module's config (e.g. ``DEFAULT_HTTP_TIMEOUT_SECONDS = "
            "5`` at module top, then ``timeout=DEFAULT_HTTP_TIMEOUT_"
            "SECONDS``). For probe vs background-poll vs admin calls "
            "use distinct constants — they have different SLOs and "
            "operators want to tune them independently."
        ),
    )


# ---------------------------------------------------------------------------
# 3. Hardcoded /srv-config and /opt/media-stack paths
# ---------------------------------------------------------------------------


# Match string literals that hardcode the canonical container paths.
# These should resolve through ``resolve_config_path()`` or the
# ``CONFIG_ROOT`` env var; baking them in breaks tests that mount
# alternative paths and breaks future container layouts.
_RE_HARDCODED_PATH = re.compile(
    r'["\'](?:/srv-config|/opt/media-stack|/srv/media-stack)(?:/[^"\']*)?["\']',
)


def test_burndown_hardcoded_container_paths() -> None:
    """``"/srv-config/..."`` / ``"/opt/media-stack/..."`` should be
    resolved through ``CONFIG_ROOT`` env or the ``resolve_config_path``
    helper. Hardcoding them ties production and tests together."""
    count = _scan_python_lines(_RE_HARDCODED_PATH)
    _enforce_burndown(
        "hardcoded-container-paths",
        count,
        hint=(
            "Use the ``CONFIG_ROOT`` env var (with a sane default) or "
            "the ``resolve_config_path()`` helper from "
            "``api/services/_resolve.py``. Hardcoded paths: "
            "(a) break tests that mount alternative dirs, "
            "(b) break future container layouts (``/srv-config`` is "
            "compose-only; k8s uses different mount paths), and "
            "(c) prevent operators from running multiple stack "
            "instances side-by-side."
        ),
    )


# ---------------------------------------------------------------------------
# 4. Service URLs hardcoded outside the registry
# ---------------------------------------------------------------------------


# Match http://<service>:<port> patterns where <service> matches a
# known registry service id. The proper alternative is to read
# ``SERVICES`` and use ``s.host`` + ``s.port``. Lets the registry stay
# the single source of truth for service topology — renaming
# "authelia" to "auth" is a one-line change in the registry instead
# of a grep across 30 files.
_RE_HARDCODED_SERVICE_URL = re.compile(
    r'["\']https?://(?:'
    r"sonarr|radarr|lidarr|readarr|bazarr|prowlarr|jellyfin|"
    r"jellyseerr|qbittorrent|sabnzbd|nzbget|maintainerr|tautulli|"
    r"flaresolverr|authentik|homepage|jdownloader|emby|plex|mythtv|"
    r"unpackerr|envoy"
    r")(?::\d+)?",
)


def test_burndown_hardcoded_service_urls() -> None:
    """``http://sonarr:8989`` should come from the SERVICES registry
    (via ``s.host``/``s.port``). Hardcoded URLs duplicate topology
    knowledge — renaming a service requires touching every
    occurrence."""
    count = _scan_python_lines(_RE_HARDCODED_SERVICE_URL)
    _enforce_burndown(
        "hardcoded-service-urls",
        count,
        hint=(
            "Read the service from ``media_stack.api.services."
            "registry.SERVICES`` (or the per-service helper) and "
            "build the URL from ``s.host`` + ``s.port``. The "
            "registry is the single source of truth for which port "
            "each service runs on; hardcoded literals drift the "
            "moment someone overrides a port."
        ),
    )


# Note on ``authelia`` exclusion: it appears in many templates that
# emit Envoy YAML, and those literals are correct (the rendered yaml
# is what Envoy reads). Treat as part of the burndown — the count is
# pinned, no new ones allowed, and existing template-string ones can
# be moved to a constant on a future PR.


# ---------------------------------------------------------------------------
# 5. Repeated date/time format strings
# ---------------------------------------------------------------------------


# Common ISO-like format strings that appear multiple times across
# the codebase. Each duplicate is a future bug class — one gets a Z
# appended, another swaps space for T, timestamps stop sorting.
_RE_ISO_FORMAT_STRINGS = re.compile(
    r'["\']%Y-%m-%dT%H:%M:%S(?:Z|\+00:00|\.%f)?["\']|'
    r'["\']%Y-%m-%d %H:%M:%S["\']',
)


def test_burndown_duplicate_iso_format_strings() -> None:
    """Same ISO-8601 format string repeated across files should
    move to a single shared constant."""
    count = _scan_python_lines(_RE_ISO_FORMAT_STRINGS)
    _enforce_burndown(
        "duplicate-iso-format-strings",
        count,
        hint=(
            "Add a single shared format constant (``ISO_8601_UTC = "
            "\"%Y-%m-%dT%H:%M:%SZ\"`` etc.) in a common module and "
            "import it. Better yet, prefer "
            "``datetime.isoformat(timespec='seconds')`` which "
            "handles tz-aware datetimes correctly without a format "
            "string at all."
        ),
    )


# ---------------------------------------------------------------------------
# 6. ``os.environ.get(..., default)`` where the default is a hardcoded value
# ---------------------------------------------------------------------------


# A common smell: every module reads its own env var with its own
# default, and the defaults disagree. Pin the count; new code routes
# through the central config helper instead of inlining.
_RE_OS_ENVIRON_INLINE_DEFAULT = re.compile(
    r'os\.environ\.get\([^,)]+,\s*["\'][^"\']+["\']\s*\)',
)


def test_burndown_os_environ_inline_defaults() -> None:
    """``os.environ.get("FOO", "literal-default")`` scatters config
    knowledge. Centralize defaults in a config helper module."""
    count = _scan_python_lines(_RE_OS_ENVIRON_INLINE_DEFAULT)
    _enforce_burndown(
        "os-environ-inline-defaults",
        count,
        hint=(
            "Move the default to a single ``DEFAULTS`` mapping or a "
            "``Config`` dataclass. When two modules disagree on the "
            "default for ``BOOTSTRAP_PROFILE_FILE``, they will "
            "silently load different files — this happened in v1.0.149."
        ),
    )


# ---------------------------------------------------------------------------
# 7. Direct env access outside the config layer
# ---------------------------------------------------------------------------


# Modules allowed to read ``os.environ`` directly. Everything else
# should go through a config helper (``defaults.py``,
# ``runtime_platform.py``, app-config services, etc.) so env-var
# names exist in one place and the lookup path is testable.
_CONFIG_ALLOWED_PATH_FRAGMENTS = (
    "/core/defaults.py",
    "/core/cli_common.py",
    "/services/runtime_platform.py",
    "/api/services/_resolve.py",
    "/cli/",
    "/version.py",
    # Adapters are the layer that bridges env config into runtime
    # decisions; they're allowed to read env directly.
    "/adapters/",
    # Service registry reads env for port overrides at import time.
    "/api/services/registry.py",
    # Bootstrap CLI entrypoints orchestrate env into CLI flags.
    "/cli/commands/",
)

_RE_OS_ENVIRON_ACCESS = re.compile(r"\bos\.environ(?:\.get)?\b")


def _scan_python_lines_with_path_filter(
    pattern: re.Pattern[str],
    *,
    skip_path_fragments: tuple[str, ...] = (),
) -> int:
    count = 0
    if not SRC.is_dir():
        return 0
    for path in SRC.rglob("*.py"):
        if any(part in {"__pycache__", ".venv"} for part in path.parts):
            continue
        if path.name.startswith("test_"):
            continue
        rel = str(path)
        if any(frag in rel for frag in skip_path_fragments):
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
            if pattern.search(line):
                count += 1
    return count


def test_burndown_no_direct_env_access_outside_config() -> None:
    """``os.environ`` access must be confined to the config layer.
    Scattered direct reads mean env-var names live in dozens of
    modules; renaming or deprecating one is a grep-marathon and
    silently leaves stragglers behind."""
    count = _scan_python_lines_with_path_filter(
        _RE_OS_ENVIRON_ACCESS,
        skip_path_fragments=_CONFIG_ALLOWED_PATH_FRAGMENTS,
    )
    _enforce_burndown(
        "no-direct-env-access-outside-config",
        count,
        hint=(
            "Read environment variables through the config helper "
            "(``media_stack.core.defaults`` or "
            "``media_stack.services.runtime_platform``) instead of "
            "calling ``os.environ`` directly. The helper centralizes "
            "the var name, default, and parser — when ops renames "
            "an env var, they only update one place."
        ),
    )


# ---------------------------------------------------------------------------
# 8. Hardcoded URLs (general — not service registry)
# ---------------------------------------------------------------------------


# Match string literals that look like URLs to non-registry
# destinations: documentation links, third-party APIs (GitHub
# release feeds, jsdelivr, PayPal), CDNs. The proper alternative is
# a constants module so white-label deploys can override them.
_RE_HARDCODED_URL = re.compile(
    r'["\']https?://(?!'
    # Exclude registry-known hostnames (already covered by
    # ``hardcoded-service-urls`` ratchet)
    r"sonarr|radarr|lidarr|readarr|bazarr|prowlarr|jellyfin|"
    r"jellyseerr|qbittorrent|sabnzbd|nzbget|maintainerr|tautulli|"
    r"flaresolverr|authentik|authelia|homepage|jdownloader|emby|plex|"
    r"mythtv|unpackerr|envoy|"
    # Exclude localhost/loopback, Authelia portal hostname, and the
    # ``%s``/``${...}`` template placeholders so generated config
    # strings aren't flagged.
    r"localhost|127\.0\.0\.1|0\.0\.0\.0|"
    r"\$\{|%s|"
    # Exclude OpenAPI / W3C / RFC schema URLs (these are constants in
    # the spec, not deployment config).
    r"json-schema\.org|schema\.googleapis\.com|"
    r"www\.w3\.org|tools\.ietf\.org|datatracker\.ietf\.org|"
    r"type\.googleapis\.com"
    r')[^"\']*["\']',
)

_URL_ALLOWED_PATH_FRAGMENTS = (
    # Branding + onboarding constants modules — central place for
    # external URLs is exactly what we want.
    "/api/services/branding.py",
    "/services/branding/",
    # Docs, examples, vendored fixtures.
    "/docs/",
    # OpenAPI spec embedding — references to schema URIs are fine.
    # ADR-0007 Phase E cleanup: handlers_get.py deleted; openapi
    # service module hosts the spec-embedding logic.
    "/api/services/openapi.py",
    "/api/routes/",
    "/api/routing/",
)


def test_burndown_no_hardcoded_urls() -> None:
    """Third-party / external URLs (GitHub, jsdelivr, PayPal, docs)
    should live in a single constants module — operators running
    air-gapped or white-label deploys need one place to override
    them."""
    count = _scan_python_lines_with_path_filter(
        _RE_HARDCODED_URL,
        skip_path_fragments=_URL_ALLOWED_PATH_FRAGMENTS,
    )
    _enforce_burndown(
        "no-hardcoded-urls",
        count,
        hint=(
            "Move the URL to a named constant in a branding/links "
            "module (``GITHUB_URL``, ``DOCS_URL``, ``PAYPAL_URL``). "
            "Air-gapped deploys + white-label resellers need one "
            "place to override them; baking literals into business "
            "logic forces a code change for every override."
        ),
    )


# ---------------------------------------------------------------------------
# 9. Chained string-key dict access (JSON-style) in business logic
# ---------------------------------------------------------------------------


# ``data["foo"]["bar"]["baz"]`` — three or more levels of string-key
# dict access in a single expression. Each chain is a missing typed
# DTO; a typo or upstream rename breaks at runtime instead of at
# import.
_RE_TRIPLE_DICT_CHAIN = re.compile(
    r'\[["\'][\w\-]+["\']\]\s*\[["\'][\w\-]+["\']\]\s*\[["\'][\w\-]+["\']\]',
)

_DICT_CHAIN_ALLOWED_PATH_FRAGMENTS = (
    # Tests legitimately walk fixture structures.
    "/tests/",
    # Adapters that translate raw upstream JSON into typed shapes —
    # one expects raw dict access at the boundary; the rule applies
    # to BUSINESS LOGIC further inside.
    "/adapters/",
    # OpenAPI / contract validators read schema dicts.
    "/api/contract_validator.py",
    "/api/services/openapi_router.py",
    # Manifest loaders walk K8s API responses (which are dicts).
    "/services/edge/envoy_config_generator.py",
    "/api/services/k8s_ingress_sync.py",
    # The job framework's prereq-chain mechanism.
    "/application/jobs/",
)


def test_burndown_no_json_dict_access_in_business_logic() -> None:
    """``data["x"]["y"]["z"]`` chains in business logic should be
    typed objects (dataclass / NamedTuple / Pydantic). Each chain is
    a missing DTO."""
    count = _scan_python_lines_with_path_filter(
        _RE_TRIPLE_DICT_CHAIN,
        skip_path_fragments=_DICT_CHAIN_ALLOWED_PATH_FRAGMENTS,
    )
    _enforce_burndown(
        "no-json-dict-access-in-business-logic",
        count,
        hint=(
            "Replace ``data[\"x\"][\"y\"][\"z\"]`` with a typed "
            "shape: ``@dataclass class XPayload``, ``TypedDict``, or "
            "(at the boundary) a small parse function that returns "
            "the typed object. The chain hides a contract — a "
            "rename upstream breaks at runtime; a dataclass field "
            "rename fails at static-check time."
        ),
    )


# ---------------------------------------------------------------------------
# 10. String comparison against likely-enum fields
# ---------------------------------------------------------------------------


# Heuristic: ``<name>.status == "literal"`` / ``<name>.phase ==
# "literal"`` / similar. Catches the case where the field has a
# known set of valid values (so it should be an Enum) but business
# logic compares against the string. A typo (``"runing"``) produces
# silent dead code.
_RE_ENUM_LIKE_STRING_COMPARE = re.compile(
    r'\.(?:status|phase|state|kind|category|provider|mode|type)\s*'
    r'(?:==|!=)\s*["\'][a-z_-]+["\']',
)

_ENUM_COMPARE_ALLOWED_PATH_FRAGMENTS = (
    # Tests that exercise the very strings the enums encode.
    "/tests/",
    # Adapter boundary — translating upstream string fields into
    # typed values.
    "/adapters/",
    # Domain types module DEFINES the enum constants; comparisons
    # there are bootstrapping.
    "/domain/",
    # Migrators / validators that walk raw config dicts.
    "/api/services/config/",
)


def test_burndown_no_string_comparison_for_enum_fields() -> None:
    """``state.phase == "running"`` is a typo waiting to happen.
    Use an Enum (``state.phase is Phase.RUNNING``) so a typo at the
    call site is a NameError, not silently-false dead code."""
    count = _scan_python_lines_with_path_filter(
        _RE_ENUM_LIKE_STRING_COMPARE,
        skip_path_fragments=_ENUM_COMPARE_ALLOWED_PATH_FRAGMENTS,
    )
    _enforce_burndown(
        "no-string-comparison-for-enum-fields",
        count,
        hint=(
            "Define the field as an ``Enum`` and compare with the "
            "enum value: ``status is Status.RUNNING`` instead of "
            "``status == \"running\"``. Typos in the literal "
            "(``\"runing\"``) compile fine and the comparison "
            "silently always fails — the bug surfaces in production "
            "when the dead branch matters."
        ),
    )


# ---------------------------------------------------------------------------
# 11. Inline dict literals that look like domain objects
# ---------------------------------------------------------------------------


# Heuristic: ``{"id": ..., "name": ..., }`` literals where the keys
# match a known domain-object schema. These should be dataclass /
# NamedTuple / Pydantic instances. Counts inline ``{"id":`` dict
# literals (with at least 3 known keys) in business-logic files.
_RE_DOMAIN_OBJECT_DICT = re.compile(
    r'\{["\'](?:id|service|name|status|phase|kind)["\']\s*:'
    r'[^}]{0,200}["\'](?:name|status|phase|kind|service)["\']\s*:'
    r'[^}]{0,200}["\'](?:status|phase|kind|service|name)["\']\s*:',
)

_DOMAIN_DICT_ALLOWED_PATH_FRAGMENTS = (
    # Tests fabricate domain objects as dicts for fixture brevity.
    "/tests/",
    # Adapters: dict assembly at the IO boundary is fine; the rule
    # is "no dict-as-domain-object IN BUSINESS LOGIC".
    "/adapters/",
    # API handlers serialize to dicts at the boundary.
    # ADR-0007 Phase E cleanup: handlers_get/post.py deleted; their
    # serialize-to-dict layer lives in route + service modules.
    "/api/routes/",
    "/api/routing/",
    "/api/services/security_get_handlers.py",
    "/api/services/security_post_handlers.py",
    "/api/services/logs_handlers.py",
    "/api/services/events_sse.py",
    "/api/services/media_integrity_dispatch.py",
    "/api/services/media_integrity_handlers.py",
)


def test_burndown_no_inline_dict_domain_objects() -> None:
    """``{"id": ..., "name": ..., "status": ...}`` literals in
    business logic should be dataclass instances. Anonymous dicts
    drift in shape over time — fields appear/disappear without
    coordinator review."""
    count = _scan_python_lines_with_path_filter(
        _RE_DOMAIN_OBJECT_DICT,
        skip_path_fragments=_DOMAIN_DICT_ALLOWED_PATH_FRAGMENTS,
    )
    _enforce_burndown(
        "no-inline-dict-domain-objects",
        count,
        hint=(
            "Replace ``{\"id\": ..., \"name\": ..., \"status\": ...}`` "
            "with a frozen dataclass (``@dataclass(frozen=True) "
            "class ServiceInfo: id: str; name: str; status: "
            "Status``). The dataclass: (1) catches missing fields "
            "at construction, (2) catches typos in field names, "
            "(3) participates in static type-checking, (4) gives "
            "downstream code autocomplete."
        ),
    )


# ---------------------------------------------------------------------------
# Note on NO_DB_FIELD_REFERENCES_OUTSIDE_REPO
# ---------------------------------------------------------------------------
# Skipped — this codebase has no clear DAL/repo boundary (services
# read JSON files and call SDKs directly). The closest analogue is
# the contracts/services YAML registry, which is already gated by
# ``test_no_hardcoded_services_ratchet.py``. Re-evaluate if/when a
# proper persistence layer lands (alembic + ORM models would
# enable a clean field-name scan).
