"""Shared bootstrap enums."""

from __future__ import annotations

from enum import Enum


class BootstrapMode(str, Enum):
    FULL = "full"
    MEDIA_SERVER_PREWARM = "media-server-prewarm"
    MEDIA_SERVER_HOME_RAILS = "media-server-home-rails"
    MEDIA_HYGIENE = "media-hygiene"

    @classmethod
    def choices(cls) -> list[str]:
        return [
            cls.FULL.value,
            cls.MEDIA_SERVER_PREWARM.value,
            cls.MEDIA_SERVER_HOME_RAILS.value,
            cls.MEDIA_HYGIENE.value,
        ]

    @classmethod
    def from_cli(cls, value: str) -> "BootstrapMode":
        text = str(value or "").strip().lower()
        for mode in cls:
            if mode.value == text:
                return mode
        raise ValueError(f"Unsupported bootstrap mode: {value}")


class RunnerEvent(str, Enum):
    INIT = "INIT"
    DISCOVER_CAPABILITIES = "DISCOVER_CAPABILITIES"
    VALIDATE = "VALIDATE"
    PLAN = "PLAN"
    PRE = "PRE"
    ACQUIRE = "ACQUIRE"
    RESERVE = "RESERVE"
    RUN = "RUN"
    COMMIT = "COMMIT"
    POST = "POST"
    ENSURE = "ENSURE"
    CHECK_STATUS = "CHECK_STATUS"
    HEALTH_CHECK = "HEALTH_CHECK"
    HEARTBEAT = "HEARTBEAT"
    REPORT = "REPORT"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    CANCEL = "CANCEL"
    ABORT = "ABORT"
    TIMEOUT = "TIMEOUT"
    RETRY = "RETRY"
    ROLLBACK = "ROLLBACK"
    COMPENSATE = "COMPENSATE"
    RECOVER = "RECOVER"
    RECONCILE = "RECONCILE"
    RELEASE = "RELEASE"
    CLEANUP = "CLEANUP"
    FINALIZE = "FINALIZE"
    UPGRADE = "UPGRADE"
    MIGRATE = "MIGRATE"
    SHUTDOWN = "SHUTDOWN"

    @classmethod
    def choices(cls) -> list[str]:
        return [event.value for event in cls]

    @classmethod
    def from_value(cls, value: str) -> "RunnerEvent":
        token = str(value or "").strip().upper()
        for event in cls:
            if event.value == token:
                return event
        raise ValueError(f"Unsupported runner event: {value}")
