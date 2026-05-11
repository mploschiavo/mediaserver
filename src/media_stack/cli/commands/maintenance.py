"""Re-export shim for the controller's maintenance background timer.

ADR-0015 Phase 5 split the original ``MaintenanceService`` god
class into two SRP workflows-tier services
(:class:`ConfigSnapshotService`, :class:`StaleFilePruner`) under
:mod:`media_stack.cli.workflows.maintenance_service`.

This module survives as the call-site surface that
:func:`controller_serve._snapshot_timer` and the unit-test suite
(:mod:`test_cli_commands_extended`) already import:

* :func:`take_config_snapshot(args)` — convert an
  :class:`argparse.Namespace` into a :class:`ConfigSnapshotService`
  call. The ``args.config_root`` field is sampled here so the
  workflows service can take a concrete :class:`Path` constructor
  arg (no ``os.environ`` reads in workflows methods).
* :func:`prune_stale_files(args, log)` — same shape for
  :class:`StaleFilePruner`.

Per ADR-0012, the module-level callables are bound to a singleton
:class:`MaintenanceShim` so this file holds no top-level
functions and trips no class-structure ratchets. Removal of
the shim is queued for Phase 6's cleanup pass.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Callable

from media_stack.cli.workflows.maintenance_service import (
    ConfigSnapshotService,
    StaleFilePruner,
)


class MaintenanceShim:
    """Adapter: bridge :class:`argparse.Namespace` to the workflows services.

    Workflows-tier classes take concrete :class:`Path` constructor
    args. The CLI entry-point hands us a :class:`Namespace` plus
    its ambient ``CONFIG_ROOT`` env-var fallback. This adapter is
    the single place that conversion happens; the workflows
    services never see ``os.environ``.
    """

    def resolve_config_root(self, args: argparse.Namespace) -> Path:
        return Path(
            getattr(args, "config_root", os.environ.get("CONFIG_ROOT", "/srv-config"))
        )

    def take_config_snapshot(self, args: argparse.Namespace) -> None:
        ConfigSnapshotService(config_root=self.resolve_config_root(args)).snapshot()

    def prune_stale_files(
        self,
        args: argparse.Namespace,
        log: Callable[[str], None],
    ) -> None:
        StaleFilePruner(
            config_root=self.resolve_config_root(args), log=log,
        ).prune()


# Singleton + module-level aliases for the historical import surface
# (controller_serve._snapshot_timer + test_cli_commands_extended).
# ADR-0012 rule 10.
_INSTANCE = MaintenanceShim()
take_config_snapshot = _INSTANCE.take_config_snapshot
prune_stale_files = _INSTANCE.prune_stale_files


__all__ = ["MaintenanceShim", "prune_stale_files", "take_config_snapshot"]
