"""Ratchet — every nginx ``proxy_pass`` to a hostname MUST use the
variable form, not a literal hostname or an envsubst placeholder.

**Why this exists**

Stock nginx resolves the upstream hostname in a literal
``proxy_pass http://media-stack-controller:9100;`` exactly once,
at config-parse time, and caches the resulting IP for the
lifetime of the worker process. On Docker / Compose, every
``force-recreate`` of the upstream container gives it a fresh IP
— and nginx keeps trying the old one, returning 502 Bad Gateway
until the nginx container itself is restarted. On Kubernetes the
same trap fires when a Service is deleted+recreated or when a
headless Service's pod endpoints rotate.

The fix is the variable form:

    set $upstream "media-stack-controller:9100";
    proxy_pass http://$upstream;

Combined with a ``resolver`` directive, this defers DNS
resolution to request time and re-resolves on the configured
TTL. After every upstream recreate, the next request automatically
picks up the new IP within seconds.

The nuance that this ratchet enforces:

* ``proxy_pass http://media-stack-controller:9100;`` — BAD: literal
  hostname, resolved once.
* ``proxy_pass http://${API_UPSTREAM};`` — BAD: that's an envsubst
  placeholder, replaced with a literal at startup. nginx still
  treats the result as a literal because ``${...}`` isn't an
  nginx variable.
* ``proxy_pass http://$controller_upstream;`` — GOOD: nginx
  variable. Resolution deferred to request time.
* ``proxy_pass http://127.0.0.1:8000/healthz;`` — GOOD-by-exception:
  loopback-only paths don't go through DNS at all (kernel routing).
  Allow-listed below.

**Scope**

Walks every nginx config in the source tree (``.conf``,
``.conf.template``, ``.nginx.conf`` files under ``deploy/`` and
``src/``). Skips test fixtures and ``node_modules``. New
violations fail CI; clean configs are expected.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]

# Where to scan. Limited to source-controlled nginx configs the
# operator actually deploys; tests + vendored content are out of
# scope.
NGINX_CONFIG_GLOBS: tuple[str, ...] = (
    "deploy/**/*.conf",
    "deploy/**/*.conf.template",
    "deploy/**/*.nginx.conf",
    "deploy/**/*.nginx.conf.template",
    "src/**/*.nginx.conf",
    "src/**/*.nginx.conf.template",
)

# Skip these paths — vendored / tests / generated.
EXCLUDE_PARTS: frozenset[str] = frozenset({
    "node_modules", "__pycache__", ".git", "build", "dist",
})

# Loopback / localhost upstreams don't require resolver discipline
# because they bypass DNS entirely. Allow-list them so a
# liveness-probe-style ``proxy_pass http://127.0.0.1:8000/...`` line
# (or HTTPS variant) doesn't trip the ratchet.
LOOPBACK_PATTERN = re.compile(
    r"^\s*proxy_pass\s+https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|;|\s)",
)

# Match the variable form: proxy_pass http(s)://$<nginx-var>...
# nginx variable names: $ followed by [A-Za-z_] then [A-Za-z0-9_]*.
GOOD_VARIABLE_PATTERN = re.compile(
    r"^\s*proxy_pass\s+https?://\$[A-Za-z_][A-Za-z0-9_]*",
)

# Match any proxy_pass line; we'll check it against the good +
# loopback patterns to decide pass/fail.
ANY_PROXY_PASS_PATTERN = re.compile(r"^\s*proxy_pass\s+")


def _iter_nginx_files() -> list[Path]:
    out: list[Path] = []
    for pattern in NGINX_CONFIG_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if any(part in EXCLUDE_PARTS for part in path.parts):
                continue
            if not path.is_file():
                continue
            out.append(path)
    return sorted(set(out))


def _violations_in(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, raw_line), ...] for every offending line."""
    violations: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Strip ``# ...`` comments — a documented bad example in a
        # comment shouldn't fail the ratchet.
        code = re.sub(r"#.*$", "", line)
        if not ANY_PROXY_PASS_PATTERN.search(code):
            continue
        if GOOD_VARIABLE_PATTERN.match(code):
            continue
        if LOOPBACK_PATTERN.match(code):
            continue
        violations.append((lineno, line.rstrip()))
    return violations


def test_every_nginx_proxy_pass_uses_a_variable() -> None:
    """Catch the nginx-in-Docker stale-DNS bug class at the config
    level. Every ``proxy_pass`` to a non-loopback upstream MUST use
    the ``proxy_pass http://$variable...;`` form so resolution is
    deferred to request time."""
    failures: dict[str, list[tuple[int, str]]] = {}
    files = _iter_nginx_files()
    for path in files:
        rels = path.relative_to(REPO_ROOT)
        violations = _violations_in(path)
        if violations:
            failures[str(rels)] = violations
    if failures:
        message_lines = [
            "nginx proxy_pass directives must use the variable form to",
            "avoid stale-DNS Bad Gateway errors after upstream recreate.",
            "",
            "Bad:    proxy_pass http://media-stack-controller:9100;",
            "Bad:    proxy_pass http://${API_UPSTREAM};   # envsubst, still a literal",
            "Good:   set $upstream \"media-stack-controller:9100\";",
            "        proxy_pass http://$upstream;",
            "",
            "Plus a ``resolver $RESOLVER valid=10s ipv6=off;`` directive",
            "in the same server block. See",
            "deploy/compose/ui-nginx.conf for the canonical example.",
            "",
            "Offending lines:",
        ]
        for filename, lines in failures.items():
            message_lines.append(f"  {filename}:")
            for lineno, raw in lines:
                message_lines.append(f"    line {lineno}: {raw}")
        pytest.fail("\n".join(message_lines))


def test_ratchet_finds_at_least_one_nginx_config() -> None:
    """Sanity — if the glob pattern silently matches zero files
    (e.g. someone moved the configs), the main test would pass
    vacuously. Catch that explicitly."""
    files = _iter_nginx_files()
    assert files, (
        "ratchet found zero nginx config files to scan. Either the "
        "configs moved (update NGINX_CONFIG_GLOBS) or this ratchet's "
        "main check is silently passing."
    )
