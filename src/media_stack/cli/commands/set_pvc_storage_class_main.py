#!/usr/bin/env python3
"""Entry-point shim for ``bin/set-pvc-storage-class.sh``.

ADR-0015 Phase 7g. Pre-Phase-7g this module held the full
``YamlPvcDocumentTransformer`` (Strategy, 9 methods) +
``SetPvcStorageClassCommand`` (Command, 4 methods) + dataclass —
213 LoC total. Phase 7g moved the workflow logic into
:mod:`media_stack.cli.workflows.set_pvc_storage_class`; what
remains here is argparse + main + module-level aliases for the
historical test surface (``test_cli_commands_extended`` +
``test_set_pvc_storage_class`` both import the 9 transformer
helpers + dataclass by name).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from media_stack.cli.workflows.set_pvc_storage_class import (
    SetPvcStorageClassRunner,
    SetStorageClassConfig,
    YamlPvcDocumentTransformer,
)
from media_stack.core.exceptions import ConfigError, MediaStackError
from media_stack.core.filesystem import FileSystem
from media_stack.core.logging_utils import configure_logging, log_event


class SetPvcStorageClassEntryPoint:
    """Per-ADR-0012 entry-point: argv → cfg → runner.run → exit code."""

    def __init__(self) -> None:
        self._transformer = YamlPvcDocumentTransformer()
        self._runner = SetPvcStorageClassRunner(self._transformer)

    @property
    def transformer(self) -> YamlPvcDocumentTransformer:
        return self._transformer

    @property
    def runner(self) -> SetPvcStorageClassRunner:
        return self._runner

    def build_arg_parser(self, default_file: Path) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="bin/set-pvc-storage-class.sh",
            description=(
                "Adds or updates spec.storageClassName on every PVC in the target manifest."
            ),
        )
        parser.add_argument("storage_class_name", nargs="?", default="")
        parser.add_argument("--file", dest="target_file", default=str(default_file))
        parser.add_argument("--clear", action="store_true")
        return parser

    def parse_config(self, argv: list[str] | None = None) -> SetStorageClassConfig:
        # parents[4] = repo root (this file at src/media_stack/cli/commands/...).
        # Matches the parents[4] used by teardown_stack_main, release_pipeline_main,
        # apply_scale_policy_main, dup_burndown_main, run_unit_tests_main.
        root_dir = Path(__file__).resolve().parents[4]
        parser = self.build_arg_parser(root_dir / "deploy" / "k8s" / "storage-pvc.yaml")
        args = parser.parse_args(argv)

        target = Path(args.target_file).resolve()
        class_name = str(args.storage_class_name or "").strip()
        clear_mode = bool(args.clear)

        if not target.is_file():
            raise ConfigError(f"File not found: {target}")
        if not clear_mode and not class_name:
            raise ConfigError("STORAGE_CLASS_NAME is required unless --clear is used.")

        return SetStorageClassConfig(
            target_file=target, class_name=class_name, clear_mode=clear_mode,
        )

    def main(self, argv: list[str] | None = None) -> int:
        logger = configure_logging()
        fs = FileSystem()
        try:
            cfg = self.parse_config(argv)
            return self._runner.run(cfg, fs, logger)
        except (ConfigError, MediaStackError) as exc:
            log_event(logger, logging.ERROR, "pvc.storage_class.failed", error=str(exc))
            return 1


# Module-level singletons + back-compat aliases for the historical
# test-patch surface. test_cli_commands_extended imports
# ``_indent_width`` / ``_is_pvc_document`` / ``_find_spec_index`` /
# ``_remove_storage_class`` / ``split_yaml_documents`` /
# ``render_yaml_documents`` / ``transform_storage_class_manifest`` /
# ``SetStorageClassConfig``; test_set_pvc_storage_class loads the
# module via spec_from_file_location.
_INSTANCE = SetPvcStorageClassEntryPoint()
_TRANSFORMER = _INSTANCE.transformer

_indent_width = _TRANSFORMER._indent_width
_is_pvc_document = _TRANSFORMER._is_pvc_document
_find_spec_index = _TRANSFORMER._find_spec_index
_remove_storage_class = _TRANSFORMER._remove_storage_class
_find_spec_block_end = _TRANSFORMER._find_spec_block_end
_set_storage_class = _TRANSFORMER._set_storage_class
split_yaml_documents = _TRANSFORMER.split_yaml_documents
render_yaml_documents = _TRANSFORMER.render_yaml_documents
transform_storage_class_manifest = _TRANSFORMER.transform_storage_class_manifest
build_arg_parser = _INSTANCE.build_arg_parser
parse_config = _INSTANCE.parse_config
main = _INSTANCE.main


__all__ = [
    "SetPvcStorageClassEntryPoint",
    "SetStorageClassConfig",
    "_find_spec_block_end",
    "_find_spec_index",
    "_indent_width",
    "_is_pvc_document",
    "_remove_storage_class",
    "_set_storage_class",
    "build_arg_parser",
    "main",
    "parse_config",
    "render_yaml_documents",
    "split_yaml_documents",
    "transform_storage_class_manifest",
]


if __name__ == "__main__":
    sys.exit(main())
