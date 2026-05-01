"""Confirmation policies for destructive workflows."""

from __future__ import annotations


class InteractiveConfirmationPolicy:
    """Interactive confirmation with an assume-yes mode."""

    def __init__(self, assume_yes: bool = False) -> None:
        self.assume_yes = assume_yes

    def approve(self, prompt: str, *, requires_double_confirm: bool = False) -> bool:
        if requires_double_confirm:
            answer = input(f"{prompt} [type YES to proceed] ")
            return answer.strip() == "YES"
        if self.assume_yes:
            return True
        answer = input(f"{prompt} [y/N] ")
        return answer.strip().lower() in {"y", "yes"}


class DenyAllConfirmationPolicy:
    """Confirmation policy used by dry-runs and tests."""

    def approve(self, prompt: str, *, requires_double_confirm: bool = False) -> bool:
        return False
