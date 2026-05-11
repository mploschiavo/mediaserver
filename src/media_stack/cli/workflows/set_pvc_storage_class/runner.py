"""SetPvcStorageClassRunner — apply the YAML transformation to a target file.

ADR-0015 Phase 7g. Pre-Phase-7g the ``run`` method lived on
``SetPvcStorageClassCommand`` alongside argparse handlers in
commands/. Phase 7g moves it onto this workflows-tier class;
the commands shim shrinks to argparse + main.
"""

from __future__ import annotations

import logging

from media_stack.cli.workflows.set_pvc_storage_class.models import (
    SetStorageClassConfig,
)
from media_stack.cli.workflows.set_pvc_storage_class.transformer import (
    YamlPvcDocumentTransformer,
)
from media_stack.core.filesystem import FileSystem
from media_stack.core.logging_utils import log_event


class SetPvcStorageClassRunner:
    """Apply :class:`YamlPvcDocumentTransformer` to the target file + log the outcome."""

    def __init__(self, transformer: YamlPvcDocumentTransformer | None = None) -> None:
        self._transformer = transformer or YamlPvcDocumentTransformer()

    @property
    def transformer(self) -> YamlPvcDocumentTransformer:
        return self._transformer

    def run(
        self,
        cfg: SetStorageClassConfig,
        fs: FileSystem,
        logger: logging.Logger,
    ) -> int:
        original = fs.read_text(cfg.target_file, encoding="utf-8")
        transformed = self._transformer.transform_storage_class_manifest(
            original,
            class_name=cfg.class_name,
            clear_mode=cfg.clear_mode,
        )
        fs.write_text_atomic(cfg.target_file, transformed)

        if cfg.clear_mode:
            log_event(
                logger,
                logging.INFO,
                "pvc.storage_class.cleared",
                file=str(cfg.target_file),
            )
        else:
            log_event(
                logger,
                logging.INFO,
                "pvc.storage_class.set",
                file=str(cfg.target_file),
                storage_class=cfg.class_name,
            )
        return 0


__all__ = ["SetPvcStorageClassRunner"]
