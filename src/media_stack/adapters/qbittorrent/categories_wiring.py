"""qBittorrent category wiring (movies/tv/music/books).

Lifecycle-method port of the legacy ``ensure_qbittorrent_categories``
job handler (ADR-0005 Phase 3 cutover). Lives here rather than inline
in ``qbittorrent/lifecycle.py`` so the lifecycle module stays focused
on the core ``ServiceLifecycle`` Protocol surface (probe_running /
probe_has_api_key / mint_api_key / persist_api_key).

``CategoriesWirer`` owns:

  * The cookie-jar HTTP session — qBittorrent's WebUI authenticates
    by ``POST /api/v2/auth/login`` with form-encoded credentials,
    setting an ``SID`` cookie that every subsequent call must echo.
    No static API key is issued. Each ``probe`` / ``ensure`` call
    builds a fresh per-call cookie-jar opener (stateless across
    calls — no jar surface a future probe could mutate).
  * The credential discovery contract — username + password fall
    through ``ctx.secrets`` (orchestrator-resolved) → ``ctx.config``
    (per-service contract YAML) → process env vars
    (``QBIT_USERNAME`` / ``QBIT_PASSWORD``) → constructor defaults.
  * The desired-category set (movies/tv/music/books → savePath under
    ``/data/torrents/completed/``).
  * Idempotent skip: probe lists existing categories and reports
    ``ok`` only when ALL four desired categories are present. Ensurer
    iterates and POSTs each missing category one at a time —
    qBittorrent returns 409 if a category already exists with the
    same shape, which the wirer treats as "already there" rather
    than as a failure.
  * Tri-state outcome semantics:
      * ``transient=True`` when the credential is missing OR the
        login round-trip fails on a network error (orchestrator
        retries on next tick after ``probe_has_api_key`` settles).
      * ``transient=False`` when login succeeds but a per-category
        POST returns a non-409 4xx (config-level — operator action
        needed).
      * ``Outcome.success`` when every desired category is now
        present (whether created this call or pre-existing).

Why this is *form-encoded session-cookie* rather than X-Api-Key
============================================================

Unlike the *arrs (X-Api-Key header) or Bazarr (X-API-KEY header),
qBittorrent's WebUI doesn't issue a static API key — the operator
sets a username + password on the WebUI and every authenticated
request must carry an ``SID`` cookie obtained from a successful
``POST /api/v2/auth/login`` (form-encoded ``username=…&password=…``).
The wirer wraps the legacy ``cookie-jar + opener`` pattern from
``ensure_qbittorrent_categories`` so the cookie travels automatically
on the follow-up ``createCategory`` POSTs.
"""

from __future__ import annotations

import http.cookiejar
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from media_stack.adapters._shared.lifecycle_wirer_base import (
    LifecycleWirerBase,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)
from media_stack.infrastructure.media.catalog import load_media_types
from media_stack.infrastructure.qbittorrent import (
    QBITTORRENT_FACTORY_DEFAULT_PASSWORD,
    QBITTORRENT_FACTORY_DEFAULT_USERNAME,
)


logger = logging.getLogger(__name__)


# --- HTTP timing -----------------------------------------------------

_QBIT_HTTP_LIST_TIMEOUT_SECONDS = 5
_QBIT_HTTP_LOGIN_TIMEOUT_SECONDS = 10
_QBIT_HTTP_POST_TIMEOUT_SECONDS = 10

# --- API paths -------------------------------------------------------

_LOGIN_PATH = "/api/v2/auth/login"
_LIST_CATEGORIES_PATH = "/api/v2/torrents/categories"
_CREATE_CATEGORY_PATH = "/api/v2/torrents/createCategory"

# --- Credential env vars (defaults if neither secrets nor config set) ---

_QBIT_USERNAME_ENV = "QBIT_USERNAME"
_QBIT_PASSWORD_ENV = "QBIT_PASSWORD"

# --- Factory defaults (qBit's first-boot username + password). Read
# from the source-of-truth declarations in
# ``infrastructure.qbittorrent`` (which the no-hardcoded-defaults
# ratchet allowlists as the upstream-given factory creds). Operator
# overrides flow in via env vars resolved by ``ctx.secrets`` /
# ``ctx.config`` first; the factory defaults only matter on a fresh
# install where qBit's WebUI hasn't been touched yet.

_FACTORY_DEFAULT_USERNAME = (
    os.environ.get(_QBIT_USERNAME_ENV)
    or QBITTORRENT_FACTORY_DEFAULT_USERNAME
)
_FACTORY_DEFAULT_PASSWORD = (
    os.environ.get(_QBIT_PASSWORD_ENV)
    or QBITTORRENT_FACTORY_DEFAULT_PASSWORD
)



# qBittorrent's createCategory returns 409 Conflict when a category
# already exists with the requested name. Treat as "already there"
# rather than as a failure — idempotent ensurer.
_CATEGORY_ALREADY_EXISTS = 409


class CategoriesWirer(LifecycleWirerBase):
    """Per-call qBittorrent category-wiring engine.

    Stateless across calls. Each ``probe`` / ``ensure`` builds a
    fresh per-call cookie-jar so a previous run's session leakage
    can't poison the next run's auth check. Constructor-injected
    HTTP timeouts + factory-default credentials so tests can swap
    them; per-call ``ctx`` carries the host / port / per-deployment
    overrides.
    """

    def __init__(
        self,
        *,
        list_timeout_seconds: float = _QBIT_HTTP_LIST_TIMEOUT_SECONDS,
        login_timeout_seconds: float = _QBIT_HTTP_LOGIN_TIMEOUT_SECONDS,
        post_timeout_seconds: float = _QBIT_HTTP_POST_TIMEOUT_SECONDS,
        default_username: str = _FACTORY_DEFAULT_USERNAME,
        default_password: str = _FACTORY_DEFAULT_PASSWORD,
        desired_categories: Mapping[str, str] | None = None,
    ) -> None:
        self._list_timeout = list_timeout_seconds
        self._login_timeout = login_timeout_seconds
        self._post_timeout = post_timeout_seconds
        self._default_username = default_username
        self._default_password = default_password
        # ``None`` means "load from media-types catalog at first use";
        # an explicit (possibly-empty) mapping means "tests pinned this
        # set — don't fall through to the catalog". Distinguishing the
        # two keeps the catalog the canonical SoT in production while
        # giving tests a deterministic injection surface.
        self._explicit_desired: Mapping[str, str] | None = (
            None if desired_categories is None
            else dict(desired_categories)
        )

    @property
    def _desired(self) -> Mapping[str, str]:
        """Lazy-resolved view of the desired-category map. Constructor
        injection wins; otherwise pull from
        ``contracts/catalog/media_types.yaml`` via the catalog
        loader."""
        if self._explicit_desired is not None:
            return self._explicit_desired
        return {
            mt.qbit_category: mt.torrents_completed_path
            for mt in load_media_types().values()
            if mt.qbit_category
        }

    # --- public API -------------------------------------------------

    def probe(self, ctx: OrchestrationContext) -> ProbeResult:
        """Check whether all desired categories are already present.

        Tri-state:
          * ``ok`` when every desired category is in the listing
          * ``failed`` when the listing succeeded but at least one
            desired category is missing (orchestrator dispatches
            the ensurer)
          * ``unknown`` when we can't reach qBit, can't authenticate,
            or the credential isn't discoverable yet
        """
        base = self._base_url(ctx)
        if not base:
            return ProbeResult.unknown(
                "no host/port in qBittorrent config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
                evaluated_at=ctx.now(),
            )
        creds = self._discover_credentials(ctx)
        if creds is None:
            return ProbeResult.unknown(
                "no qBittorrent credentials in env/secrets/config — "
                f"cannot probe (operator must set {_QBIT_PASSWORD_ENV})",
                evidence={"url": base},
                evaluated_at=ctx.now(),
            )
        opener = self._build_opener()
        login_err = self._login(opener, base, creds)
        if login_err is not None:
            return ProbeResult.unknown(
                f"qBittorrent login failed: {login_err}",
                evidence={"url": base, "username": creds[0]},
                evaluated_at=ctx.now(),
            )
        existing = self._list_categories(opener, base)
        if existing is None:
            return ProbeResult.unknown(
                f"could not list qBittorrent categories at {base}",
                evidence={"url": base},
                evaluated_at=ctx.now(),
            )
        missing = [c for c in self._desired if c not in existing]
        if missing:
            return ProbeResult.failed(
                f"qBittorrent missing categories: {sorted(missing)}",
                evidence={
                    "url": base,
                    "missing": sorted(missing),
                    "present": sorted(existing.keys()),
                },
                evaluated_at=ctx.now(),
            )
        return ProbeResult.ok(
            f"qBittorrent has all {len(self._desired)} desired categories",
            evidence={
                "url": base,
                "present": sorted(existing.keys()),
            },
            evaluated_at=ctx.now(),
        )

    def ensure(self, ctx: OrchestrationContext) -> Outcome[None]:
        """Create any missing desired categories. Idempotent."""
        base = self._base_url(ctx)
        if not base:
            return Outcome.failure(
                "no host/port in qBittorrent config — cannot ensure",
                transient=False,
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        creds = self._discover_credentials(ctx)
        if creds is None:
            return Outcome.failure(
                "no qBittorrent credentials in env/secrets/config — "
                f"orchestrator will retry after {_QBIT_PASSWORD_ENV} is "
                "set; the legacy handler logged-and-OK'd this same "
                "shape (see ADR-0003 silent-error-as-ok bug class).",
                transient=True,
                evidence={"url": base},
            )
        opener = self._build_opener()
        login_err = self._login(opener, base, creds)
        if login_err is not None:
            return Outcome.failure(
                f"qBittorrent login failed: {login_err}",
                transient=True,
                evidence={"url": base, "username": creds[0]},
            )
        existing = self._list_categories(opener, base)
        if existing is None:
            return Outcome.failure(
                f"could not list existing qBittorrent categories at {base}",
                transient=True,
                evidence={"url": base},
            )
        created: list[str] = []
        skipped: list[str] = []
        for cat, save_path in self._desired.items():
            if cat in existing:
                skipped.append(cat)
                continue
            err = self._create_category(opener, base, cat, save_path)
            if err is None:
                created.append(cat)
                continue
            # err is a (transient, message, evidence_extra) triple
            transient, message, extra = err
            return Outcome.failure(
                f"qBittorrent createCategory({cat}): {message}",
                transient=transient,
                evidence={
                    "url": base,
                    "category": cat,
                    "created": created,
                    "skipped": skipped,
                    **extra,
                },
            )
        return Outcome.success(
            None,
            evidence={
                "url": base,
                "created": created,
                "skipped": skipped,
            },
        )

    # --- helpers ----------------------------------------------------

    def _base_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}"

    def _discover_credentials(
        self, ctx: OrchestrationContext,
    ) -> tuple[str, str] | None:
        """Discover (username, password). Returns ``None`` when no
        password is available — same fall-through chain as
        ``QbittorrentLifecycle.discover_api_key`` (which reads
        ``QBITTORRENT_PASSWORD``), extended for the form-login flow
        which also wants a username.

        Fall-through order:
          1. ``ctx.secrets[QBIT_USERNAME / QBIT_PASSWORD]`` — what
             the orchestrator resolved from operator-provided secret
             material (k8s Secret or compose ``.env``).
          2. ``ctx.config[username / password]`` — per-deployment
             contract YAML overrides (uncommon but supported).
          3. ``ctx.secrets[QBITTORRENT_PASSWORD]`` — the lifecycle's
             canonical "api key" env name (``QbittorrentLifecycle``'s
             ``discover_api_key``); stays in sync so an operator who
             set ``QBITTORRENT_PASSWORD`` doesn't have to set
             ``QBIT_PASSWORD`` separately.
          4. ``self._default_username`` / ``self._default_password``
             — constructor-injected factory defaults (admin /
             adminadmin on a fresh qBit install).

        Returns ``None`` only when EVERY layer is empty — including
        the constructor default. Tests inject empty defaults to
        exercise the missing-credentials path; production code path
        always falls through to ``admin``/``adminadmin``."""
        username = self._first_nonempty(
            ctx.secrets.get(_QBIT_USERNAME_ENV),
            ctx.config.get("username"),
            self._default_username,
        )
        password = self._first_nonempty(
            ctx.secrets.get(_QBIT_PASSWORD_ENV),
            ctx.config.get("password"),
            ctx.secrets.get("QBITTORRENT_PASSWORD"),
            self._default_password,
        )
        if not password:
            return None
        return username, password

    def _first_nonempty(self, *candidates: Any) -> str:
        """Return the first non-empty stripped string in
        ``candidates``, or ``""`` if every entry is empty / None.
        Instance method (no ``@staticmethod``) so the
        STATIC_METHOD_RATCHET stays clean — the helper is
        wirer-private vocabulary and rightly hangs off the wirer
        instance."""
        for c in candidates:
            text = (str(c) if c is not None else "").strip()
            if text:
                return text
        return ""

    def _build_opener(self) -> urllib.request.OpenerDirector:
        """Build a fresh cookie-jar-equipped opener. Per-call so two
        adjacent probes can't share session state (which would mask
        a real auth regression as a stale-cookie pass)."""
        jar = http.cookiejar.CookieJar()
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar),
        )

    def _login(
        self,
        opener: urllib.request.OpenerDirector,
        base: str,
        creds: tuple[str, str],
    ) -> str | None:
        """Form-encoded login. Returns ``None`` on success, otherwise
        a short human-readable error string the caller folds into
        either a ``ProbeResult.unknown`` or an ``Outcome.failure``."""
        username, password = creds
        body = urllib.parse.urlencode(
            {"username": username, "password": password},
        ).encode()
        req = urllib.request.Request(f"{base}{_LOGIN_PATH}", data=body)
        try:
            with opener.open(req, timeout=self._login_timeout) as resp:
                # qBit returns 200 + "Ok." text on success and
                # 200 + "Fails." text on bad credentials. Some
                # versions return 403 on bad creds — collapse both
                # to a transient error so the orchestrator surfaces
                # it without retrying forever.
                payload = resp.read() or b""
                if resp.status == 200 and b"Ok" in payload:
                    return None
                return f"unexpected response: status={resp.status} body={payload[:64]!r}"
        except urllib.error.HTTPError as exc:
            return f"HTTP {exc.code}"
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return f"unreachable: {exc}"

    def _list_categories(
        self,
        opener: urllib.request.OpenerDirector,
        base: str,
    ) -> dict[str, Any] | None:
        """GET ``/api/v2/torrents/categories``. Returns the parsed
        JSON dict (``{name: {savePath, …}, …}``) or ``None`` on any
        network / parse failure."""
        try:
            with opener.open(
                urllib.request.Request(f"{base}{_LIST_CATEGORIES_PATH}"),
                timeout=self._list_timeout,
            ) as resp:
                body = resp.read() or b""
        except (
            urllib.error.HTTPError, urllib.error.URLError,
            OSError, TimeoutError,
        ):
            return None
        import json as _json
        try:
            parsed = _json.loads(body)
        except _json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else {}

    def _create_category(
        self,
        opener: urllib.request.OpenerDirector,
        base: str,
        category: str,
        save_path: str,
    ) -> tuple[bool, str, dict[str, Any]] | None:
        """POST ``/api/v2/torrents/createCategory``. Returns ``None``
        on success, otherwise ``(transient, message, evidence)``."""
        body = urllib.parse.urlencode(
            {"category": category, "savePath": save_path},
        ).encode()
        req = urllib.request.Request(
            f"{base}{_CREATE_CATEGORY_PATH}", data=body,
        )
        try:
            with opener.open(req, timeout=self._post_timeout):
                return None
        except urllib.error.HTTPError as exc:
            if exc.code == _CATEGORY_ALREADY_EXISTS:
                # Race: probe-listed pre-existing was missed (qBit
                # may pre-create movies/tv defaults on some
                # versions). Treat as success.
                return None
            return (False, f"HTTP {exc.code}", {"http_status": exc.code})
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return (True, f"unreachable: {exc}", {"error": str(exc)})


__all__ = ["CategoriesWirer"]
