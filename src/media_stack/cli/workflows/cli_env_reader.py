"""CliEnvReader — Repository for reading the environment in CLI services.

Two `cli/workflows/` services (:class:`DeployCliConfigService`,
:class:`RunControllerJobCliConfigService`) both need to read env
vars from a constructor-injected mapping (sampled from
:data:`os.environ` at construction or passed in by tests). Phase 3
landed the env-injection pattern inline on both classes; Phase 3c
extracts the shared reading API into one Repository class so
neither Facade duplicates the helper logic.

Repository pattern: this class is the single typed point of
access to the env mapping. Callers ask for typed views
(:meth:`value`, :meth:`pick`, :meth:`boolean`,
:meth:`boolean_candidates`); they don't touch the mapping
directly. Tests construct with ``env={"FOO": "1"}`` instead of
monkey-patching :data:`os.environ`.

Why Repository and not just a function namespace: each method
returns a typed view of the SAME underlying mapping. The mapping
is mutable state shared across method calls; constructor-injecting
it captures the state once at construction so subsequent reads are
stable even if :data:`os.environ` changes during the run (e.g.
preflight handlers that `os.environ[KEY] = value` for downstream
phases).
"""

from __future__ import annotations

import os


_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


class CliEnvReader:
    """Repository: typed reads against a constructor-injected env mapping."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        # Sample os.environ once at construction; method paths read
        # from self._env so they don't re-touch the module-level
        # mapping (which the OS_ENVIRON_IN_METHODS_RATCHET counts).
        self._env = dict(env) if env is not None else dict(os.environ)

    def value(self, name: str) -> str | None:
        """Return ``self._env[name]`` stripped; ``None`` if missing/blank."""
        value = self._env.get(name)
        if value is None:
            return None
        token = str(value).strip()
        return token if token else None

    def pick(self, *values: str | None, default: str = "") -> str:
        """First non-None, non-empty value among ``values`` else ``default``.

        Used by the deploy CLI to prefer operator CLI args over env
        values over profile-driven defaults over hard-coded fallbacks.
        """
        for value in values:
            if value is not None and str(value) != "":
                return str(value)
        return default

    def boolean(self, name: str, default: bool = False) -> bool:
        """``True`` if env[name] is one of '1' / 'true' / 'yes' / 'on'.

        Case-insensitive, whitespace-tolerant. Returns ``default``
        when the var is missing.
        """
        raw = self._env.get(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in _TRUTHY_VALUES

    def boolean_candidates(
        self,
        names: tuple[str, ...],
        default: bool = False,
    ) -> bool:
        """``boolean()`` against the FIRST present env var in ``names``.

        Used for env-var aliases (the same flag accepting multiple
        env-var names — ``SKIP_FOO`` and ``DEPLOY_SKIP_FOO``, etc.).
        Returns ``default`` if none of the names is set.
        """
        for name in names:
            token = str(name).strip()
            if not token:
                continue
            if token in self._env:
                return self.boolean(token, default)
        return default

    def contains(self, name: str) -> bool:
        """``True`` if ``name`` is a key in the env mapping (any value)."""
        return name in self._env

    def keys(self) -> tuple[str, ...]:
        """Snapshot of env-var names — for callers that iterate
        for SKIP_* / FEATURE_FLAG_* patterns. Returned as a tuple so
        the iteration doesn't accidentally hold a live reference."""
        return tuple(self._env.keys())


__all__ = ["CliEnvReader"]
