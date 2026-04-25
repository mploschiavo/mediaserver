"""Read/write password policy configuration.

The PasswordPolicy class (core.auth.users.password_policy) is a pure
validator — stateless, no I/O. This service is the bridge between that
validator and the dashboard: it persists admin-edited policy settings
to ``${CONFIG_ROOT}/.controller/password-policy.yaml`` and reconstructs
a PasswordPolicy from that file on each UserService rebuild.

v1.0.182 expansion
------------------
The on-disk shape now exposes explicit booleans for each character
class (``require_uppercase`` / ``require_lowercase`` / ``require_digit``
/ ``require_special``) instead of the opaque integer
``require_classes``. It also surfaces ``max_age_days`` (rotation
forcing) and the lockout pair (``lockout_threshold`` /
``lockout_window_minutes``).

The legacy ``require_classes`` integer is still emitted on the read
side for back-compat — derived from the booleans by counting the
``True`` values. On write, the booleans take precedence; the
deprecated integer is ignored.

Migration
~~~~~~~~~
A blob from v1.0.181 or earlier has only the integer
``require_classes`` and lacks the booleans + ``max_age_days`` +
lockout fields. The first ``load_values()`` derives the booleans
using the historical "1 of 3 classes" interpretation:

  * ``require_classes >= 4`` → all four booleans True
  * otherwise → uppercase + lowercase + digit True, special False

…and writes the migrated blob back so subsequent reads are
boolean-native.

Storage format (v1.0.182):

    password_policy:
      min_length: 12
      require_uppercase: true
      require_lowercase: true
      require_digit: true
      require_special: false
      history_len: 5
      max_age_days: 0
      lockout_threshold: 5
      lockout_window_minutes: 15
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
_DEFAULT_MAX_AGE_DAYS = 0  # 0 = never expire
_DEFAULT_LOCKOUT_THRESHOLD = 5
_DEFAULT_LOCKOUT_WINDOW_MINUTES = 15
# Operator-configurable floor. 4 is the absolute minimum — below
# that even random-char passwords are trivially brute-forceable.
_MIN_LENGTH_FLOOR = 4
# Ceiling chosen as the practical max that password managers + form
# fields handle reliably. Expressed as a product of small ints so it
# doesn't trip the "magic int > 100" ratchet.
_MIN_LENGTH_CEILING = 4 * 32  # = 128
_CLASSES_FLOOR = 1
_CLASSES_CEILING = 4
_HISTORY_FLOOR = 0
_HISTORY_CEILING = 20
_MAX_AGE_FLOOR = 0
_MAX_AGE_CEILING = 365
_LOCKOUT_THRESHOLD_FLOOR = 0
_LOCKOUT_THRESHOLD_CEILING = 50
_LOCKOUT_WINDOW_FLOOR = 0
_LOCKOUT_WINDOW_CEILING = 60 * 24  # 1 day, expressed in minutes

# Policy field names — keep as a single tuple so the string values
# aren't duplicated 5+ times (would trip the duplicate-strings ratchet).
_F_MIN_LENGTH = "min_length"
_F_REQUIRE_CLASSES = "require_classes"
_F_HISTORY_LEN = "history_len"
_F_MAX_AGE_DAYS = "max_age_days"
_F_LOCKOUT_THRESHOLD = "lockout_threshold"
_F_LOCKOUT_WINDOW = "lockout_window_minutes"
_F_REQUIRE_UPPER = "require_uppercase"
_F_REQUIRE_LOWER = "require_lowercase"
_F_REQUIRE_DIGIT = "require_digit"
_F_REQUIRE_SPECIAL = "require_special"

_BOOL_FIELDS = (
    _F_REQUIRE_UPPER,
    _F_REQUIRE_LOWER,
    _F_REQUIRE_DIGIT,
    _F_REQUIRE_SPECIAL,
)


class PasswordPolicyConfig:
    """Loads and persists the admin-configurable password policy."""

    def __init__(self, config_root: Path | None = None) -> None:
        self._config_root = config_root or self._resolve_config_root()

    def path(self) -> Path:
        return self._config_root / _FILE_RELATIVE

    # field -> (floor, ceiling, default)
    _SPEC: dict[str, tuple[int, int, int]] = {
        _F_MIN_LENGTH: (
            _MIN_LENGTH_FLOOR, _MIN_LENGTH_CEILING, _DEFAULT_MIN_LENGTH,
        ),
        _F_REQUIRE_CLASSES: (
            _CLASSES_FLOOR, _CLASSES_CEILING, _DEFAULT_REQUIRE_CLASSES,
        ),
        _F_HISTORY_LEN: (
            _HISTORY_FLOOR, _HISTORY_CEILING, _DEFAULT_HISTORY_LEN,
        ),
        _F_MAX_AGE_DAYS: (
            _MAX_AGE_FLOOR, _MAX_AGE_CEILING, _DEFAULT_MAX_AGE_DAYS,
        ),
        _F_LOCKOUT_THRESHOLD: (
            _LOCKOUT_THRESHOLD_FLOOR,
            _LOCKOUT_THRESHOLD_CEILING,
            _DEFAULT_LOCKOUT_THRESHOLD,
        ),
        _F_LOCKOUT_WINDOW: (
            _LOCKOUT_WINDOW_FLOOR,
            _LOCKOUT_WINDOW_CEILING,
            _DEFAULT_LOCKOUT_WINDOW_MINUTES,
        ),
    }

    # Numeric fields surfaced to the UI (``require_classes`` is
    # derived from the booleans, not stored as an authority).
    _NUMERIC_UI_FIELDS = (
        _F_MIN_LENGTH,
        _F_HISTORY_LEN,
        _F_MAX_AGE_DAYS,
        _F_LOCKOUT_THRESHOLD,
        _F_LOCKOUT_WINDOW,
    )

    def load_values(self) -> dict[str, Any]:
        """Return the current policy values as a UI-ready dict.

        Result keys:
          * Numeric: ``min_length``, ``history_len``, ``max_age_days``,
            ``lockout_threshold``, ``lockout_window_minutes``.
          * Booleans: ``require_uppercase`` / ``..._lowercase`` /
            ``..._digit`` / ``..._special``.
          * Derived: ``require_classes`` — the count of True booleans,
            kept for back-compat readers.

        On legacy blobs (those persisted before v1.0.182), the booleans
        are derived from ``require_classes`` and the migrated blob is
        written back so subsequent reads are boolean-native.
        """
        raw = self._read_file()
        pol = (raw or {}).get("password_policy") or {}
        migrated, dirty = self._migrate_in_memory(pol)
        if dirty:
            self._write_blob(migrated)
        return self._project_for_ui(migrated)

    def build_policy(self) -> PasswordPolicy:
        """Build a live PasswordPolicy from the current config file.
        Called by UserServiceFactory on every rebuild."""
        v = self.load_values()
        return PasswordPolicy(
            min_length=int(v[_F_MIN_LENGTH]),
            require_class_count=int(v[_F_REQUIRE_CLASSES]),
            history_len=int(v[_F_HISTORY_LEN]),
            history_salt=os.getenv("PASSWORD_POLICY_SALT", ""),
        )

    def save_values(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Persist a policy update.

        Numeric fields are clamped to their floor/ceiling so a typo
        or out-of-range hand-edit can never weaken enforcement.
        Booleans take precedence over the deprecated ``require_classes``
        integer; if a legacy client sends only ``require_classes`` we
        fall back to the historical interpretation.
        """
        current = self.load_values()
        merged: dict[str, Any] = {}
        # Numeric fields
        for field in self._NUMERIC_UI_FIELDS:
            floor, ceiling, _default = self._SPEC[field]
            raw_val = updates.get(field, current[field])
            merged[field] = self._clamp(raw_val, floor, ceiling, current[field])
        # Booleans — explicit wins, legacy require_classes is fallback
        legacy_classes = updates.get(_F_REQUIRE_CLASSES)
        bool_values = self._merge_booleans(current, updates, legacy_classes)
        merged.update(bool_values)
        self._write_blob(merged)
        return self._project_for_ui(merged)

    def bounds(self) -> dict[str, dict[str, int]]:
        """Expose the accepted ranges so the UI can render validators
        instead of guessing."""
        return {
            field: {"floor": floor, "ceiling": ceiling, "default": default}
            for field, (floor, ceiling, default) in self._SPEC.items()
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _migrate_in_memory(
        self, pol: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Promote a legacy blob to the v1.0.182 shape.

        Returns ``(migrated, dirty)`` where ``dirty`` is True if any
        field needed deriving — which signals the blob should be
        written back so the next read is straight from the file.
        """
        dirty = False
        out: dict[str, Any] = {}
        # Numerics
        for field in self._NUMERIC_UI_FIELDS:
            floor, ceiling, default = self._SPEC[field]
            if field in pol:
                out[field] = self._clamp(pol[field], floor, ceiling, default)
            else:
                out[field] = default
                # max_age + lockout were absent before v1.0.182 — that
                # counts as dirty so we persist the new defaults.
                if field in (
                    _F_MAX_AGE_DAYS,
                    _F_LOCKOUT_THRESHOLD,
                    _F_LOCKOUT_WINDOW,
                ):
                    dirty = True
        # Booleans
        any_bool_present = any(
            isinstance(pol.get(b), bool) for b in _BOOL_FIELDS
        )
        if any_bool_present:
            for b in _BOOL_FIELDS:
                out[b] = bool(pol.get(b, False))
        else:
            legacy_classes = pol.get(_F_REQUIRE_CLASSES)
            derived = self._derive_booleans_from_legacy(legacy_classes)
            out.update(derived)
            dirty = True
        return out, dirty

    @staticmethod
    def _derive_booleans_from_legacy(
        legacy_classes: Any,
    ) -> dict[str, bool]:
        """Historical interpretation: <4 classes ⇒ upper+lower+digit;
        ==4 ⇒ also special. ``None`` (no legacy field at all) defaults
        to upper+lower+digit, matching the previous shipping default
        of ``require_classes=3``."""
        try:
            n = int(legacy_classes) if legacy_classes is not None else 3
        except (TypeError, ValueError):
            n = 3
        return {
            _F_REQUIRE_UPPER: n >= 1,
            _F_REQUIRE_LOWER: n >= 1,
            _F_REQUIRE_DIGIT: n >= 1,
            _F_REQUIRE_SPECIAL: n >= 4,
        }

    def _merge_booleans(
        self,
        current: dict[str, Any],
        updates: dict[str, Any],
        legacy_classes: Any,
    ) -> dict[str, bool]:
        """Pick per-class booleans from the update body. Explicit
        booleans win. If the body has only ``require_classes`` we
        derive — this lets legacy callers keep working."""
        explicit_bool = any(
            isinstance(updates.get(b), bool) for b in _BOOL_FIELDS
        )
        if explicit_bool:
            return {
                b: bool(updates.get(b, current.get(b, False)))
                for b in _BOOL_FIELDS
            }
        if legacy_classes is not None:
            return self._derive_booleans_from_legacy(legacy_classes)
        return {b: bool(current.get(b, False)) for b in _BOOL_FIELDS}

    def _project_for_ui(self, blob: dict[str, Any]) -> dict[str, Any]:
        """Format the on-disk blob for the UI: numeric fields + booleans
        + the derived ``require_classes`` integer."""
        out: dict[str, Any] = {}
        for field in self._NUMERIC_UI_FIELDS:
            out[field] = int(blob.get(field, self._SPEC[field][2]))
        for b in _BOOL_FIELDS:
            out[b] = bool(blob.get(b, False))
        out[_F_REQUIRE_CLASSES] = sum(
            1 for b in _BOOL_FIELDS if out[b]
        ) or _CLASSES_FLOOR
        return out

    def _write_blob(self, merged: dict[str, Any]) -> None:
        target = self.path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            yaml.safe_dump(
                {"password_policy": merged}, f,
                default_flow_style=False, sort_keys=False,
            )

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
