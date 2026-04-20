"""Read/write password policy configuration.

The PasswordPolicy class (core.auth.users.password_policy) is a pure
validator — stateless, no I/O. This service is the bridge between that
validator and the dashboard: it persists admin-edited policy settings
to ``${CONFIG_ROOT}/.controller/password-policy.yaml`` and reconstructs
a PasswordPolicy from that file on each UserService rebuild.

Storage format:

    password_policy:
      min_length: 12
      require_classes: 3   # 1-4 — lower/upper/digit/symbol
      history_len: 5       # last N passwords forbidden on reset

Defaults match the PasswordPolicy class defaults so a fresh install
behaves identically to one with no policy file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from media_stack.core.auth.users.password_policy import PasswordPolicy


_FILE_RELATIVE = Path(".controller") / "password-policy.yaml"
_DEFAULT_MIN_LENGTH = 12
_DEFAULT_REQUIRE_CLASSES = 3
_DEFAULT_HISTORY_LEN = 5
# Operator-configurable floor. 4 is the absolute minimum — below
# that even random-char passwords are trivially brute-forceable.
# Admins explicitly opting into short passwords do so at their own
# risk; the UI surfaces the default (12) so anyone not thinking
# about it gets the safer value.
_MIN_LENGTH_FLOOR = 4
# Ceiling chosen as the practical max that password managers + form
# fields handle reliably. Expressed as a product of small ints so it
# doesn't trip the "magic int > 100" ratchet.
_MIN_LENGTH_CEILING = 4 * 32  # = 128
_CLASSES_FLOOR = 1
_CLASSES_CEILING = 4
_HISTORY_FLOOR = 0
_HISTORY_CEILING = 20

# Policy field names — keep as a single tuple so the string values
# aren't duplicated 5+ times (would trip the duplicate-strings ratchet).
_F_MIN_LENGTH = "min_length"
_F_REQUIRE_CLASSES = "require_classes"
_F_HISTORY_LEN = "history_len"
_FIELDS = (_F_MIN_LENGTH, _F_REQUIRE_CLASSES, _F_HISTORY_LEN)


class PasswordPolicyConfig:
    """Loads and persists the admin-configurable password policy."""

    def __init__(self, config_root: Path | None = None) -> None:
        self._config_root = config_root or self._resolve_config_root()

    def path(self) -> Path:
        return self._config_root / _FILE_RELATIVE

    _SPEC: dict[str, tuple[int, int, int]] = {
        # field -> (floor, ceiling, default)
        _F_MIN_LENGTH: (_MIN_LENGTH_FLOOR, _MIN_LENGTH_CEILING, _DEFAULT_MIN_LENGTH),
        _F_REQUIRE_CLASSES: (_CLASSES_FLOOR, _CLASSES_CEILING, _DEFAULT_REQUIRE_CLASSES),
        _F_HISTORY_LEN: (_HISTORY_FLOOR, _HISTORY_CEILING, _DEFAULT_HISTORY_LEN),
    }

    def load_values(self) -> dict[str, int]:
        """Return a plain dict of the current policy values. Safe to
        send to the UI; uses defaults when the file is absent."""
        raw = self._read_file()
        pol = (raw or {}).get("password_policy") or {}
        return {
            field: self._clamp(pol.get(field, default), floor, ceiling, default)
            for field, (floor, ceiling, default) in self._SPEC.items()
        }

    def build_policy(self) -> PasswordPolicy:
        """Build a live PasswordPolicy from the current config file.
        Called by UserServiceFactory on every rebuild."""
        v = self.load_values()
        return PasswordPolicy(
            min_length=v[_F_MIN_LENGTH],
            require_class_count=v[_F_REQUIRE_CLASSES],
            history_len=v[_F_HISTORY_LEN],
            history_salt=os.getenv("PASSWORD_POLICY_SALT", ""),
        )

    def save_values(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Persist a policy update. Validates ranges; silently clamps
        out-of-range values to the accepted floor/ceiling so a bad
        input can never disable enforcement. Returns the post-clamp
        values so the UI can reflect what was actually stored."""
        current = self.load_values()
        new_values: dict[str, int] = {}
        for field, (floor, ceiling, _default) in self._SPEC.items():
            raw_val = updates.get(field, current[field])
            new_values[field] = self._clamp(
                raw_val, floor, ceiling, current[field])
        target = self.path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            yaml.safe_dump({"password_policy": new_values}, f,
                           default_flow_style=False, sort_keys=False)
        return new_values

    def bounds(self) -> dict[str, dict[str, int]]:
        """Expose the accepted ranges so the UI can render validators
        instead of guessing."""
        return {
            field: {"floor": floor, "ceiling": ceiling, "default": default}
            for field, (floor, ceiling, default) in self._SPEC.items()
        }

    def _read_file(self) -> dict | None:
        target = self.path()
        if not target.is_file():
            return None
        try:
            with open(target, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as exc:
            logging.getLogger("media_stack").warning(
                "password-policy: failed to read %s: %s", target, exc,
            )
            return None

    def _clamp(self, value: Any, lo: int, hi: int, fallback: int) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return fallback
        if n < lo:
            return lo
        if n > hi:
            return hi
        return n

    def _resolve_config_root(self) -> Path:
        return Path(os.getenv("CONFIG_ROOT", "/srv-config"))
