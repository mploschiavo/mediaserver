"""Password strength + history enforcement.

Validates:
- Minimum length (default 12)
- Character class coverage (lower, upper, digit, symbol) — configurable
- Not in a small list of obvious bad defaults
- Not a recent reuse (history N, default last 5)

History is stored alongside the user's ``provider_refs`` as a rolling
list of salted hashes. Each recent password is kept hashed so we can
compare new candidates without storing the plaintext.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass


_MIN_LENGTH = 12
_HISTORY_LEN = 5
_REJECTED = {
    "password", "admin", "administrator", "media-stack", "mediastack",
    "letmein", "changeme", "12345678", "qwerty", "abc123", "passw0rd",
}


@dataclass
class PasswordCheckResult:
    ok: bool
    reason: str = ""


class PasswordPolicy:
    """Stateless validator + history manager.

    ``history_salt`` is mixed into every stored hash so two installs
    with the same password history can't cross-compare.
    """

    def __init__(
        self,
        *,
        min_length: int = _MIN_LENGTH,
        require_class_count: int = 3,
        history_len: int = _HISTORY_LEN,
        history_salt: str = "",
    ) -> None:
        self._min_length = max(4, int(min_length))
        self._require_classes = max(1, min(4, int(require_class_count)))
        self._history_len = max(0, int(history_len))
        self._history_salt = history_salt or "media-stack-default-salt"

    def check_candidate(self, password: str,
                        history_hashes: list[str] | None = None,
                        ) -> PasswordCheckResult:
        strength = self._check_strength(password)
        if not strength.ok:
            return strength
        if history_hashes:
            hashed = self._hash(password)
            if hashed in history_hashes:
                return PasswordCheckResult(
                    ok=False,
                    reason=f"password reused in last {self._history_len} changes",
                )
        return PasswordCheckResult(ok=True)

    def _check_strength(self, password: str) -> PasswordCheckResult:
        if not isinstance(password, str):
            return PasswordCheckResult(ok=False, reason="password must be a string")
        pw = password
        if len(pw) < self._min_length:
            return PasswordCheckResult(
                ok=False,
                reason=f"password too short (need {self._min_length}+ chars)",
            )
        if pw.strip().lower() in _REJECTED:
            return PasswordCheckResult(ok=False, reason="password is too common")
        classes = self._class_count(pw)
        if classes < self._require_classes:
            return PasswordCheckResult(
                ok=False,
                reason=f"need {self._require_classes}+ character classes "
                       f"(lowercase/uppercase/digit/symbol), saw {classes}",
            )
        return PasswordCheckResult(ok=True)

    def _class_count(self, pw: str) -> int:
        has_lower = any(c.islower() for c in pw)
        has_upper = any(c.isupper() for c in pw)
        has_digit = any(c.isdigit() for c in pw)
        has_symbol = any(not c.isalnum() for c in pw)
        return sum((has_lower, has_upper, has_digit, has_symbol))

    def _hash(self, password: str) -> str:
        key = self._history_salt.encode("utf-8")
        msg = password.encode("utf-8")
        return hmac.new(key, msg, hashlib.sha256).hexdigest()

    def push_history(self, existing: list[str], password: str) -> list[str]:
        """Return the new history list with ``password`` prepended, truncated
        to the configured length. Caller persists the returned list.
        """
        if self._history_len <= 0:
            return []
        hashed = self._hash(password)
        new_list = [hashed] + [h for h in (existing or []) if h != hashed]
        return new_list[: self._history_len]

    def describe_reason(self, reason: str) -> str:
        """Normalize to a concise UI message."""
        return reason or "password rejected"

    @property
    def min_length(self) -> int:
        return self._min_length

    @property
    def required_classes(self) -> int:
        return self._require_classes

    @property
    def history_len(self) -> int:
        return self._history_len
