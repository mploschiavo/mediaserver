"""Ratchets that push string literals → named constants → typed
objects. Each one targets a different angle of "this string should
not be a literal here".

Implementation notes
--------------------
For literal-length and magic-string-in-condition checks we walk the
AST rather than regex source — comments, docstrings, f-string
interpolations, and triple-quoted code samples in docs all contain
string literals that aren't operator-facing values, and regex can't
reliably exclude them. AST-walk is slower (~3x) but the count is
stable across reformatting / line-wrap changes.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src" / "media_stack"
RATCHETS_DIR = REPO_ROOT / ".ratchets"


# ---------------------------------------------------------------------------
# Burndown plumbing (same shape as test_use_proper_objects_ratchets).
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
            f"Accept the new count via "
            f".ratchets/{name}-baseline.txt — but the intent of this "
            f"ratchet is the OPPOSITE direction. Fix the new offenders."
        )


def _iter_business_logic_files(
    *,
    skip_path_fragments: tuple[str, ...] = (),
) -> list[Path]:
    """Files counted as 'business logic' for these ratchets. Tests,
    adapters, contracts validators, and the API boundary are
    excluded — those layers legitimately handle raw strings."""
    if not SRC.is_dir():
        return []
    base_skip = (
        "/__pycache__/",
        "/tests/",
        # Adapters translate raw strings at the IO boundary.
        "/adapters/",
        # The contract validators read raw OpenAPI strings.
        "/api/contract_validator.py",
        # CLI entrypoints unavoidably accept raw user-string args.
        "/cli/",
        # Version + branding constants modules ARE the place
        # constants live.
        "/version.py",
        "/services/branding/",
    )
    skip = base_skip + skip_path_fragments
    out = []
    for path in SRC.rglob("*.py"):
        rel = "/" + str(path.relative_to(SRC)).replace("\\", "/")
        if any(frag in rel or frag in str(path) for frag in skip):
            continue
        if path.name.startswith("test_"):
            continue
        out.append(path)
    return out


# ---------------------------------------------------------------------------
# 1. STRING_LITERALS_OVER_10_CHARS
# ---------------------------------------------------------------------------


def _count_string_literals_longer_than(threshold: int) -> int:
    count = 0
    for path in _iter_business_logic_files():
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        # Collect docstring node IDs so we don't count them.
        docstring_ids = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module),
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc and node.body:
                    first = node.body[0]
                    if isinstance(first, ast.Expr) and isinstance(
                        first.value, ast.Constant,
                    ):
                        docstring_ids.add(id(first.value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and len(node.value) > threshold
                and id(node) not in docstring_ids
            ):
                count += 1
    return count


def test_burndown_string_literals_over_10_chars() -> None:
    """String literals longer than 10 chars should usually be named
    constants. Reads better, tightens the surface for typo bugs,
    and lets future i18n hook in without grepping the codebase."""
    count = _count_string_literals_longer_than(10)
    _enforce_burndown(
        "string-literals-over-10-chars",
        count,
        hint=(
            "Extract the string to a module-level constant: "
            "``ERR_BOOTSTRAP_FAILED = \"bootstrap pipeline aborted\"``. "
            "Long literals embedded in expressions read as \"magic "
            "values\" and resist any future i18n / branding override."
        ),
    )


def test_burndown_string_literals_over_4_chars() -> None:
    """Stricter variant of the 10-char check. Most one-word string
    literals in business logic are also magic values that deserve a
    name. Burndown — pin baseline, only fix going forward."""
    count = _count_string_literals_longer_than(4)
    _enforce_burndown(
        "string-literals-over-4-chars",
        count,
        hint=(
            "Same playbook as the 10-char variant, but applied to "
            "shorter words too. ``\"running\"``, ``\"config\"``, "
            "``\"error\"``, ``\"failed\"`` — every one of them is a "
            "candidate for an Enum or a constant. Don't try to drive "
            "this to 0 in one pass; the baseline is huge by design."
        ),
    )


# ---------------------------------------------------------------------------
# 2. MAGIC_STRINGS_IN_CONDITIONS
# ---------------------------------------------------------------------------


def _count_magic_strings_in_conditions() -> int:
    count = 0
    for path in _iter_business_logic_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            # ``if x == "y"`` / ``elif x in ("a", "b")`` / ``while
            # state != "running"`` — count Compare nodes whose
            # comparators include a string literal.
            if isinstance(node, ast.Compare):
                for cmp in node.comparators:
                    if isinstance(cmp, ast.Constant) and isinstance(
                        cmp.value, str,
                    ):
                        count += 1
                    elif isinstance(cmp, (ast.Tuple, ast.List, ast.Set)):
                        for elt in cmp.elts:
                            if isinstance(elt, ast.Constant) and isinstance(
                                elt.value, str,
                            ):
                                count += 1
    return count


def test_burndown_magic_strings_in_conditions() -> None:
    """``if state == "running"`` and ``if mode in ("a", "b")`` both
    embed magic strings in control flow. Each is a typo waiting to
    happen — ``"runing"`` compiles fine and the dead branch is
    invisible until the bug surfaces."""
    count = _count_magic_strings_in_conditions()
    _enforce_burndown(
        "magic-strings-in-conditions",
        count,
        hint=(
            "Define an ``Enum`` or ``Literal`` type for the field "
            "and compare with the typed value: ``status is "
            "Status.RUNNING`` instead of ``status == \"running\"``. "
            "For multi-value membership use ``status in "
            "{Status.OK, Status.WARN}``. Typos become NameError at "
            "import time instead of silent dead branches at runtime."
        ),
    )


# ---------------------------------------------------------------------------
# 3. DICT_ACCESS_WITH_STRING_KEYS
# ---------------------------------------------------------------------------


def _count_string_key_subscripts() -> int:
    count = 0
    for path in _iter_business_logic_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(
                node.slice, ast.Constant,
            ):
                if isinstance(node.slice.value, str):
                    count += 1
    return count


def test_burndown_dict_access_with_string_keys() -> None:
    """``record["status"]`` is a typed-object miss. Convert the
    container to a dataclass / TypedDict and access via attribute."""
    count = _count_string_key_subscripts()
    _enforce_burndown(
        "dict-access-with-string-keys",
        count,
        hint=(
            "Replace ``record[\"foo\"]`` with ``record.foo`` on a "
            "dataclass / NamedTuple / Pydantic model. The compiler "
            "catches typos in attribute names; it can't catch "
            "``record[\"fooo\"]``. For optional access use "
            "``record.foo`` + Optional types instead of "
            "``record.get(\"foo\")``."
        ),
    )


# ---------------------------------------------------------------------------
# 4. FUNCTIONS_WITH_3PLUS_PRIMITIVES
# ---------------------------------------------------------------------------


_PRIMITIVE_ANNOTATIONS = {"str", "int", "bool", "float", "bytes"}


def _annotation_name(ann: ast.expr | None) -> str | None:
    """Try to extract a top-level type name from a parameter
    annotation. ``str``, ``Optional[str]``, ``str | None`` all
    return ``"str"``."""
    if ann is None:
        return None
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Subscript):
        # Optional[...] / List[...] — get the wrapped name.
        return _annotation_name(ann.slice)
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        # ``str | None`` → take the non-None side.
        for side in (ann.left, ann.right):
            name = _annotation_name(side)
            if name and name != "None":
                return name
    return None


def _count_functions_with_many_primitives(threshold: int) -> int:
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
            args = list(node.args.args) + list(node.args.kwonlyargs)
            primitives = sum(
                1 for a in args
                if _annotation_name(a.annotation) in _PRIMITIVE_ANNOTATIONS
            )
            if primitives >= threshold:
                count += 1
    return count


def test_burndown_functions_with_3plus_primitives() -> None:
    """Functions taking 3+ primitive params (str/int/bool/float) are
    a missing Parameter Object. Operators end up with positional
    soup at the call site (``configure(name, port, retries, True,
    "v3", 5)``) and miss adding new fields cleanly."""
    count = _count_functions_with_many_primitives(3)
    _enforce_burndown(
        "functions-with-3plus-primitives",
        count,
        hint=(
            "Wrap the primitives in a frozen dataclass or Pydantic "
            "model. ``def fetch(host: str, port: int, timeout: int, "
            "retries: int) → ...`` becomes ``def fetch(spec: "
            "FetchSpec) → ...`` — the call site becomes "
            "``fetch(FetchSpec(host=..., port=..., ...))``. Adding "
            "a new field doesn't break every caller."
        ),
    )


# ---------------------------------------------------------------------------
# 5. STRING_TYPED_IDS
# ---------------------------------------------------------------------------


# Match parameters and dataclass-style fields whose name ends with
# ``_id`` (or is ``id``) AND whose annotation is the bare ``str``.
# These are candidates for a NewType / phantom-typed alias so you
# can't accidentally pass a ``ServiceId`` where a ``UserId`` is
# expected.
_RE_STRING_TYPED_ID = re.compile(
    r"\b([a-zA-Z_]\w*_id|id)\s*:\s*(?:str|Optional\[str\]|str\s*\|\s*None|None\s*\|\s*str)\b",
)


def test_burndown_string_typed_ids() -> None:
    """``user_id: str`` should be a NewType (``UserId = NewType(
    \"UserId\", str)``). Without it, you can swap a UserId and a
    ServiceId at any call site and the type-checker is silent."""
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
            count += len(_RE_STRING_TYPED_ID.findall(line))
    _enforce_burndown(
        "string-typed-ids",
        count,
        hint=(
            "Define a NewType per identity domain: "
            "``ServiceId = NewType(\"ServiceId\", str)``, "
            "``UserId = NewType(\"UserId\", str)``. Then function "
            "signatures use the NewType — mypy/pyright catch the "
            "case where you pass a ``ServiceId`` to a function "
            "expecting ``UserId`` even though both are runtime ``str``."
        ),
    )


# ---------------------------------------------------------------------------
# 6. INLINE_API_PATHS
# ---------------------------------------------------------------------------


def _count_inline_api_paths() -> int:
    """Count string literals starting with ``/api/`` outside of:
      * the API handlers themselves (they OWN the routing table)
      * the OpenAPI spec / contract validator
      * the UI's API client (path strings ARE the API contract)
    """
    count = 0
    skip = (
        "/api/handlers_get.py",
        "/api/handlers_post.py",
        "/api/server.py",
        "/api/contract_validator.py",
        "/api/services/openapi_router.py",
        "/api/services/security_get_deps.py",
        # ADR-0007 Phase 2: route modules under ``api/routes/`` ARE
        # the routing table — ``@get("/api/health")`` decorators
        # carrying inline path strings is the canonical registration
        # mechanism. Same role as ``handlers_get.py``'s elif chain
        # before the migration; same exemption.
        "/api/routes/",
    )
    for path in _iter_business_logic_files(skip_path_fragments=skip):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("/api/")
            ):
                count += 1
    return count


def test_burndown_inline_api_paths() -> None:
    """``"/api/services/refresh"`` literal sprinkled across modules
    means renaming a route requires a grep marathon. Move them to a
    routes constants module."""
    count = _count_inline_api_paths()
    _enforce_burndown(
        "inline-api-paths",
        count,
        hint=(
            "Add a routes constants module (e.g. "
            "``api/routes.py``) that defines ``ROUTE_SERVICES = "
            "\"/api/services\"`` and import it. Renaming a route is "
            "then a one-line change. Bonus: a typo in the literal "
            "(``\"/api/sevices\"``) becomes catchable at static-"
            "check time once the constants are typed as Literal."
        ),
    )


# ---------------------------------------------------------------------------
# 7. RAW_JSON_USAGE
# ---------------------------------------------------------------------------


_RE_JSON_LOADS = re.compile(r"\bjson\.(?:loads|load|dumps|dump)\s*\(")


def test_burndown_raw_json_usage() -> None:
    """``json.loads`` / ``json.dumps`` calls outside the IO boundary
    indicate untyped parsing. Use a typed serializer (Pydantic /
    msgspec / a small ``parse_X(raw: bytes) → X`` function) instead.

    Tests + IO boundary modules are exempt; everywhere else, raw
    json is a missing typed shape.
    """
    count = 0
    for path in _iter_business_logic_files(
        skip_path_fragments=(
            # API boundary OWNS json (de)serialization.
            "/api/handlers_get.py",
            "/api/handlers_post.py",
            "/api/server.py",
            "/api/services/_resolve.py",
            # The framework persists job history as json — that's
            # the persistence layer's job.
            "/application/jobs/framework.py",
            "/services/jobs/",
            # Snapshot service writes JSON tarballs.
            "/api/services/snapshots.py",
            # Auto-heal / maintainerr / arr clients read upstream
            # JSON; that's the IO boundary.
            "/api/services/auto_heal.py",
            "/services/apps/maintainerr/",
            "/services/apps/servarr/",
        ),
    ):
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
            count += len(_RE_JSON_LOADS.findall(line))
    _enforce_burndown(
        "raw-json-usage",
        count,
        hint=(
            "Wrap the json call in a typed parser: ``def "
            "parse_X(raw: bytes) → X`` returning a dataclass. The "
            "parser owns the validation; the rest of the codebase "
            "deals in typed objects. Reduces the surface where a "
            "schema change crashes downstream code."
        ),
    )


# ---------------------------------------------------------------------------
# 8. JSON_KEYS_OUTSIDE_SERIALIZER_LAYER
# ---------------------------------------------------------------------------


# Common JSON-payload keys that appear in handler responses /
# upstream parsing. When they show up in business-logic files
# (outside the API boundary), it means the layer dipped into the
# wire shape directly instead of through a typed model.
_JSON_KEY_NAMES = frozenset({
    "request_id", "task_id", "session_id", "trace_id", "correlation_id",
    "access_token", "refresh_token", "id_token",
    "client_ip", "remote_addr",
    "started_at", "completed_at", "elapsed_seconds", "last_seen_at",
    "user_agent", "x_forwarded_for",
    # Servarr / Bazarr / qBit response shapes.
    "movieFile", "episodeFile", "downloadId", "indexerId", "languageProfile",
    "artistName", "albumTitle",
})

_SERIALIZER_LAYER_PATHS = (
    "/api/handlers_get.py",
    "/api/handlers_post.py",
    "/api/server.py",
    "/api/services/_resolve.py",
    "/api/services/security_get_deps.py",
    # Adapters translate upstream JSON.
    "/adapters/",
    # Servarr / arr clients OWN their wire shapes.
    "/services/apps/servarr/",
    "/services/apps/maintainerr/",
    "/services/apps/jellyseerr/",
    "/services/apps/qbittorrent/",
    "/services/apps/sabnzbd/",
    # Audit-log writer reads/writes raw shape from disk.
    "/api/services/audit_log.py",
)


def _count_json_keys_outside_serializer() -> int:
    count = 0
    for path in _iter_business_logic_files(
        skip_path_fragments=_SERIALIZER_LAYER_PATHS,
    ):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in _JSON_KEY_NAMES
            ):
                count += 1
    return count


def test_burndown_json_keys_outside_serializer_layer() -> None:
    """Known JSON-payload keys (``"request_id"``, ``"started_at"``,
    ``"movieFile"``, ``"access_token"``…) embedded in business
    logic mean the layer reached past the typed model into the wire
    shape. Wrap the parsing in an adapter that returns a dataclass."""
    count = _count_json_keys_outside_serializer()
    _enforce_burndown(
        "json-keys-outside-serializer-layer",
        count,
        hint=(
            "Extend or build the adapter that converts the upstream "
            "JSON into a typed object, and have the business logic "
            "consume the typed object. The serializer layer owns "
            "the wire-key vocabulary; nothing downstream should need "
            "to know whether the upstream uses ``access_token`` or "
            "``accessToken``."
        ),
    )


# ---------------------------------------------------------------------------
# 9. HTTP_FIELD_NAMES_OUTSIDE_CLIENT_LAYER
# ---------------------------------------------------------------------------


# HTTP header names + auth-related fields that should only appear
# inside an HTTP client / server module. Anywhere else, the layer
# is reaching into the transport vocabulary.
_HTTP_FIELD_LITERALS = frozenset({
    "Authorization", "X-Api-Key", "X-Emby-Token", "X-CSRF-Token",
    "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
    "X-Forwarded-Uri", "X-Forwarded-Method",
    "X-Original-URL", "X-Original-Method",
    "X-Real-IP", "CF-Connecting-IP", "X-Trace-Id",
    "Content-Type", "Content-Length", "Set-Cookie", "Cookie",
    "Cache-Control", "Strict-Transport-Security",
    "Bearer ", "Basic ",  # auth scheme prefixes
})

_HTTP_CLIENT_LAYER_PATHS = (
    "/api/handlers_get.py",
    "/api/handlers_post.py",
    "/api/server.py",
    "/api/services/security_get_deps.py",
    "/api/services/_security.py",
    "/api/services/auth_config.py",
    "/api/services/csrf.py",
    "/api/services/k8s_ingress_sync.py",
    "/api/dispatch.py",
    # Auth contract + Authelia client own header vocab.
    "/core/auth/",
    "/domain/auth/",
    "/adapters/auth/",
    # Adapter layer talks raw HTTP to upstream services.
    "/adapters/",
    # Servarr/Bazarr/qBit clients are HTTP clients.
    "/services/apps/servarr/",
    "/services/apps/maintainerr/",
    "/services/apps/jellyfin/",
    "/services/apps/jellyseerr/",
    "/services/apps/qbittorrent/",
    "/services/apps/sabnzbd/",
    "/services/apps/prowlarr/",
    # Envoy config emits these as YAML payloads.
    "/services/edge/",
    "/api/services/metrics.py",
    "/api/services/envoy_access_log.py",
    "/api/services/auto_heal.py",
)


def _count_http_fields_outside_client() -> int:
    count = 0
    for path in _iter_business_logic_files(
        skip_path_fragments=_HTTP_CLIENT_LAYER_PATHS,
    ):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in _HTTP_FIELD_LITERALS
            ):
                count += 1
    return count


def test_burndown_http_field_names_outside_client_layer() -> None:
    """``"Authorization"``, ``"X-Api-Key"``, ``"X-Forwarded-For"`` —
    HTTP header literals in business logic mean the layer is doing
    its own transport plumbing. Move it behind an HTTP client class."""
    count = _count_http_fields_outside_client()
    _enforce_burndown(
        "http-field-names-outside-client-layer",
        count,
        hint=(
            "Define a small HTTP client (``class FooClient``) that "
            "owns the header vocabulary and exposes typed methods "
            "(``client.fetch_X() → X``). Business logic calls the "
            "method; only the client knows what header carries the "
            "API key. Renaming a header upstream is a one-line fix."
        ),
    )


# ---------------------------------------------------------------------------
# 10. BOOLEAN_FLAG_ARGUMENTS
# ---------------------------------------------------------------------------


def _count_boolean_flag_arguments() -> int:
    """Count function parameters annotated as ``bool`` (with or
    without a default). Booleans-in-arglists become unreadable
    positional soup at the call site (``configure(name, True, False,
    True)``) and resist clean extension."""
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
            for arg in (
                list(node.args.args)
                + list(node.args.kwonlyargs)
                + list(node.args.posonlyargs)
            ):
                if _annotation_name(arg.annotation) == "bool":
                    count += 1
    return count


def test_burndown_boolean_flag_arguments() -> None:
    """``def configure(force: bool, dry_run: bool, verbose: bool)``
    leads to ``configure(True, False, True)`` at the call site —
    unreadable. Replace bool args with an Enum (Mode.DRY_RUN /
    Mode.LIVE) or split the function (``configure_dry_run``)."""
    count = _count_boolean_flag_arguments()
    _enforce_burndown(
        "boolean-flag-arguments",
        count,
        hint=(
            "Replace ``bool`` parameters with an Enum or a typed "
            "options object. ``run(dry_run=True)`` is mildly "
            "readable, but ``run(True)`` (positional, common in "
            "tests + helpers) is opaque. Three booleans produce "
            "8 distinct behaviors none of which the type system "
            "tracks; an Enum makes the operator pick a named "
            "case."
        ),
    )


# ---------------------------------------------------------------------------
# 11. INLINE_QUERY_PARAMS
# ---------------------------------------------------------------------------


# Match string literals that look like inline query string assembly
# — ``?foo=bar`` / ``&foo=bar`` / ``f"{base}?key={val}"``.
# Building query strings by hand is the antipattern that swallows
# url-encoding bugs (a value with ``&`` in it isn't escaped, etc.).
_RE_INLINE_QUERY_PARAM = re.compile(
    r'["\'](?:\?|&)[a-zA-Z_][\w]*=[^"\']*["\']',
)


def test_burndown_inline_query_params() -> None:
    """Inline query string assembly (``f"?lines={n}&since={s}"``) is
    a url-encoding bug waiting to happen. Use ``urlencode`` or a
    typed ``HttpQuery`` builder."""
    count = 0
    for path in _iter_business_logic_files(
        skip_path_fragments=(
            # Query construction inside the API handler / dispatch
            # layer is ok — that's where wire formatting lives.
            "/api/handlers_get.py",
            "/api/handlers_post.py",
            "/api/dispatch.py",
        ),
    ):
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
            count += len(_RE_INLINE_QUERY_PARAM.findall(line))
    _enforce_burndown(
        "inline-query-params",
        count,
        hint=(
            "Use ``urllib.parse.urlencode({\"lines\": n, \"since\": "
            "s})`` or a small typed ``HttpQuery`` dataclass. Inline "
            "f-string assembly skips url-encoding so a value "
            "containing ``&`` or ``=`` corrupts the URL silently."
        ),
    )


# ---------------------------------------------------------------------------
# 12. INLINE_HTTP_HEADERS (dict assembly with header name keys)
# ---------------------------------------------------------------------------


# Catches ``{"Authorization": ..., "X-Api-Key": ...}`` literals.
# Subset of HTTP_FIELD_NAMES check, but specifically for the dict-
# literal pattern (the most common shape for "ad-hoc transport").
_RE_INLINE_HEADER_DICT = re.compile(
    r'["\'](?:Authorization|X-Api-Key|X-Emby-Token|X-CSRF-Token|'
    r'Content-Type|User-Agent|Accept|Cookie|X-Forwarded-For|'
    r'X-Original-URL|X-Real-IP)["\']\s*:',
)


def test_burndown_inline_http_headers() -> None:
    """``{"Authorization": f"Bearer {token}"}`` dict literals in
    business logic should live inside an HTTP client class. The
    client knows which header carries the auth token; business
    logic should hand off opaque credentials, not assemble them."""
    count = 0
    for path in _iter_business_logic_files(
        skip_path_fragments=_HTTP_CLIENT_LAYER_PATHS,
    ):
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
            count += len(_RE_INLINE_HEADER_DICT.findall(line))
    _enforce_burndown(
        "inline-http-headers",
        count,
        hint=(
            "Move the headers dict into an HTTP client class. The "
            "business-logic call site should look like "
            "``client.fetch(spec)``; the client builds the headers "
            "internally. Operators don't want to grep ten files to "
            "find every place the bearer token is constructed."
        ),
    )


# ---------------------------------------------------------------------------
# Notes on the wishlist gates we did NOT add (with rationale)
# ---------------------------------------------------------------------------
# * ``DB_COLUMN_NAMES_OUTSIDE_REPO_LAYER`` — this codebase has no
#   relational schema / ORM. Persistence is JSON files + SDK calls.
#   Re-evaluate when an alembic-managed DAL lands.
# * ``STRING_COMPARISONS_AGAINST_KNOWN_ENUM_FIELDS`` — already
#   covered by ``no-string-comparison-for-enum-fields`` (specific
#   field-name list) AND ``magic-strings-in-conditions`` (broader
#   AST-walked check). Either fires before this would.
# * ``INLINE_API_PATHS`` — already covered by ``inline-api-paths``
#   in this same file.
