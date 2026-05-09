"""Jellyfin-specific extensions to :class:`LifecycleApiKeyHelpers`.

ADR-0012 Phase B — the bazarr/jellyseerr/sabnzbd/*arr family share
the three-helper surface (``api_key_env`` / ``config_path`` /
``classify_source``) carried by :class:`LifecycleApiKeyHelpers` in
the sibling module. Jellyfin discovers its key from a SQLite DB
co-located with the service rather than from a flat config file,
so its lifecycle module historically grew a wider helper surface:

  * ``_api_key_env``        — env-var name (covered by parent).
  * ``_api_key_db_path``    — relative path to the on-disk SQLite DB.
  * ``_config_root``        — config-root resolution that consults
                              ``ctx.config`` / ``ctx.extra`` / env.
  * ``_bool_cfg``           — coerce config values to ``bool``.
  * ``_coerce_list``        — coerce scalar / ``None`` to a list.
  * ``_resolve_path``       — join ``config_root`` + relative DB path.
  * ``_classify_source``    — same shape as the parent but the third
                              bucket is ``"db"`` (not ``"config_file"``)
                              because Jellyfin's third source IS the DB.

This class consolidates all seven onto one configured-instance so
``JellyfinLifecycle`` can hold a single ``ClassVar`` and call sites
read as ``self._API_KEY_HELPERS.foo(ctx)`` — matching the four
sibling lifecycles. The five new methods stay plain instance methods
(no ``@staticmethod``) per the OO-discipline ratchet; receiving
``self`` lets a future per-deployment override land cleanly via
subclass without touching the call sites.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from media_stack.domain.services.lifecycle import OrchestrationContext
from media_stack.domain.services.lifecycle_api_key_helpers import (
    LifecycleApiKeyHelpers,
)


class JellyfinLifecycleApiKeyHelpers(LifecycleApiKeyHelpers):
    """Configured-instance helper for Jellyfin's lifecycle.

    Inherits ``api_key_env`` / ``config_path`` from the parent;
    overrides ``classify_source`` so the third bucket reads ``"db"``
    (Jellyfin's terminology — the credential is stored in SQLite, not
    a flat file). Adds the five Jellyfin-specific helpers the SQLite
    reader needs.
    """

    _DEFAULT_API_KEY_DB_PATH = "jellyfin/data/jellyfin.db"

    def api_key_db_path(self, ctx: OrchestrationContext) -> str:
        """Resolve the relative path to Jellyfin's SQLite DB.

        Contract YAML's ``api_key_db_path`` overrides the default so
        deployments with a non-standard layout can point the reader
        at the right file without code changes.
        """
        return str(
            ctx.config.get("api_key_db_path") or self._DEFAULT_API_KEY_DB_PATH,
        )

    def config_root(self, ctx: OrchestrationContext) -> str:
        """Resolve the config-root prefix used to find the DB on disk.

        Order of precedence: ``ctx.config['config_root']`` ->
        ``ctx.extra['config_root']`` -> ``CONFIG_ROOT`` env -> ``""``.
        Empty string is the documented "treat ``api_key_db_path`` as
        already-absolute" fallback used by ``resolve_path``.
        """
        return str(
            ctx.config.get("config_root")
            or ctx.extra.get("config_root")
            or os.environ.get("CONFIG_ROOT")
            or "",
        )

    def bool_cfg(
        self, cfg: dict[str, Any], key: str, default: bool,
    ) -> bool:
        """Coerce a config value to ``bool`` with a default fallback.

        Accepts native bools verbatim; otherwise normalises strings
        with the same vocabulary as the bootstrap helpers
        (``1`` / ``true`` / ``yes`` / ``on`` -> ``True``).
        """
        raw = cfg.get(key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def coerce_list(self, value: Any) -> list[Any]:
        """Normalise a config value (list / scalar / ``None``) to a list.

        Mirrors the bootstrap-time coercion the SQLite reader expects
        for ``api_key_name_preference``: if the YAML emits a single
        string, wrap it; if it's already a list, copy it; ``None`` ->
        empty list (no preference).
        """
        if isinstance(value, list):
            return list(value)
        if value is None:
            return []
        return [value]

    def resolve_path(self, config_root: str, db_rel_path: str) -> Path:
        """Join ``config_root`` + ``db_rel_path`` into an absolute path.

        When ``config_root`` is empty, ``db_rel_path`` is treated as
        already absolute (matches the parent ``config_path`` fallback).
        """
        if not config_root:
            return Path(db_rel_path)
        return Path(config_root) / db_rel_path

    def classify_source(
        self, ctx: OrchestrationContext, key: str,
    ) -> str:
        """Classify where ``key`` was discovered (jellyfin variant).

        Same evidence shape as the parent but the third bucket is
        ``"db"`` — Jellyfin's third credential source is the SQLite
        DB, not a flat config file. Surfaces in probe evidence so
        operators reading the auto-heal trail can tell at a glance
        whether the controller pulled the key from k8s secrets, env,
        or the DB it minted into.
        """
        env_var = self.api_key_env(ctx)
        if (ctx.secrets.get(env_var) or "").strip() == key:
            return "secrets"
        if os.environ.get(env_var, "").strip() == key:
            return "env"
        return "db"


__all__ = ["JellyfinLifecycleApiKeyHelpers"]
