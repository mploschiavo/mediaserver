"""Module-singleton accessor for ``ControllerState`` (ADR-0009 Phase 6.4).

The ``application/jobs/`` framework needs to read + write deployment
state (initial-bootstrap-done flag, failed-services map) when
applying contract-declared post-completion side-effects (the
``marks_initial_bootstrap_done`` and ``retry_on_failure`` fields on
Job contracts). ``ControllerState`` is constructed once in
``cli/commands/controller_serve.py`` and lives in the
controller process; we expose it through a class-method singleton
so the framework's ``JobRunner.run`` end-of-batch hook can read it
without taking a layer-violating import on the api/ surface.

Mirrors the ``TriggerDispatcherSingleton`` pattern in
``trigger_dispatcher.py`` — set once at controller boot, ``None``
in tests that don't care.
"""

from __future__ import annotations

import threading
from typing import Any


class ControllerStateAccessor:
    """Class-method storage for the controller's ``ControllerState``.

    Tests reset between cases by calling ``set(None)`` in tearDown.
    Production code calls ``set(state)`` once during controller boot
    after ``ControllerState()`` is constructed.
    """

    _installed: Any | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def set(cls, state: Any | None) -> None:
        """Install (or clear, with ``None``) the controller-state
        reference. Called once by controller boot after
        ``ControllerState()`` is constructed and before the first
        Job dispatch."""
        with cls._lock:
            cls._installed = state

    @classmethod
    def get(cls) -> Any | None:
        """Return the installed ``ControllerState`` or ``None`` if
        none is installed yet (early boot, isolated test)."""
        return cls._installed


__all__ = ["ControllerStateAccessor"]
