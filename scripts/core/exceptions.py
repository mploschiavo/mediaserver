"""Small exception hierarchy for operational scripts."""

from __future__ import annotations


class MediaStackError(Exception):
    """Base error for script/service failures."""


class ConfigError(MediaStackError):
    """Raised when required configuration is missing or invalid."""


class CommandExecutionError(MediaStackError):
    """Raised when a subprocess command fails."""

    def __init__(self, message: str, returncode: int, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class KubernetesError(MediaStackError):
    """Raised when kubectl interactions fail."""
