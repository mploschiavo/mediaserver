#!/usr/bin/env python3
"""Set or clear storageClassName for all PVC manifests in a YAML file."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from media_stack.core.exceptions import ConfigError, MediaStackError
from media_stack.core.filesystem import FileSystem
from media_stack.core.logging_utils import configure_logging, log_event

DOC_SEPARATOR_RE = re.compile(r"(?m)^---\s*$")
PVC_KIND_RE = re.compile(r"^\s*kind:\s*PersistentVolumeClaim\s*$")
SPEC_RE = re.compile(r"^\s*spec:\s*$")
STORAGE_CLASS_RE = re.compile(r"^\s*storageClassName:\s*")
RESOURCES_RE = re.compile(r"^\s*resources:\s*$")


@dataclass(frozen=True)
class SetStorageClassConfig:
    target_file: Path
    class_name: str
    clear_mode: bool


class YamlPvcDocumentTransformer:
    """Splits, edits, and renders PVC YAML manifests for storageClassName updates."""

    def _indent_width(self, line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _is_pvc_document(self, lines: list[str]) -> bool:
        return any(PVC_KIND_RE.match(line) for line in lines)

    def _find_spec_index(self, lines: list[str]) -> int:
        for idx, line in enumerate(lines):
            if SPEC_RE.match(line):
                return idx
        return -1

    def _remove_storage_class(self, lines: list[str]) -> list[str]:
        return [line for line in lines if not STORAGE_CLASS_RE.match(line)]

    def _find_spec_block_end(self, lines: list[str], spec_idx: int, spec_indent: int) -> int:
        idx = spec_idx + 1
        while idx < len(lines):
            stripped = lines[idx].strip()
            if stripped and self._indent_width(lines[idx]) <= spec_indent:
                break
            idx += 1
        return idx

    def _set_storage_class(self, lines: list[str], class_name: str) -> list[str]:
        updated = self._remove_storage_class(lines)
        spec_idx = self._find_spec_index(updated)
        if spec_idx < 0:
            return updated

        spec_indent = self._indent_width(updated[spec_idx])
        block_end = self._find_spec_block_end(updated, spec_idx, spec_indent)

        insert_idx = block_end
        for idx in range(spec_idx + 1, block_end):
            if RESOURCES_RE.match(updated[idx]):
                insert_idx = idx
                break

        field_indent = " " * (spec_indent + 2)
        updated.insert(insert_idx, f"{field_indent}storageClassName: {class_name}")
        return updated

    def split_yaml_documents(self, text: str) -> list[str]:
        if not text.strip():
            return [""]
        return DOC_SEPARATOR_RE.split(text)

    def render_yaml_documents(self, parts: list[str]) -> str:
        normalized = [part.strip("\n") for part in parts]
        return "\n---\n".join(normalized).rstrip("\n") + "\n"

    def transform_storage_class_manifest(
        self, text: str, class_name: str, clear_mode: bool
    ) -> str:
        transformed_docs: list[str] = []
        for doc in self.split_yaml_documents(text):
            lines = doc.splitlines()
            if not self._is_pvc_document(lines):
                transformed_docs.append(doc.strip("\n"))
                continue

            if clear_mode:
                result_lines = self._remove_storage_class(lines)
            else:
                result_lines = self._set_storage_class(lines, class_name)
            transformed_docs.append("\n".join(result_lines).strip("\n"))

        return self.render_yaml_documents(transformed_docs)


class SetPvcStorageClassCommand:
    """CLI entrypoint for set-pvc-storage-class.sh."""

    def __init__(self, transformer: YamlPvcDocumentTransformer | None = None) -> None:
        self._transformer = transformer or YamlPvcDocumentTransformer()

    def build_arg_parser(self, default_file: Path) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="bin/set-pvc-storage-class.sh",
            description=(
                "Adds or updates spec.storageClassName on every PVC in the target " "manifest."
            ),
        )
        parser.add_argument("storage_class_name", nargs="?", default="")
        parser.add_argument("--file", dest="target_file", default=str(default_file))
        parser.add_argument("--clear", action="store_true")
        return parser

    def parse_config(self, argv: list[str] | None = None) -> SetStorageClassConfig:
        root_dir = Path(__file__).resolve().parents[2]
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
            target_file=target, class_name=class_name, clear_mode=clear_mode
        )

    def run(
        self, cfg: SetStorageClassConfig, fs: FileSystem, logger: logging.Logger
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

    def main(self, argv: list[str] | None = None) -> int:
        logger = configure_logging()
        fs = FileSystem()
        try:
            cfg = self.parse_config(argv)
            return self.run(cfg, fs, logger)
        except (ConfigError, MediaStackError) as exc:
            log_event(logger, logging.ERROR, "pvc.storage_class.failed", error=str(exc))
            return 1


_TRANSFORMER = YamlPvcDocumentTransformer()
_COMMAND = SetPvcStorageClassCommand(transformer=_TRANSFORMER)


def _indent_width(line: str) -> int:
    return _TRANSFORMER._indent_width(line)


def _is_pvc_document(lines: list[str]) -> bool:
    return _TRANSFORMER._is_pvc_document(lines)


def _find_spec_index(lines: list[str]) -> int:
    return _TRANSFORMER._find_spec_index(lines)


def _remove_storage_class(lines: list[str]) -> list[str]:
    return _TRANSFORMER._remove_storage_class(lines)


def _find_spec_block_end(lines: list[str], spec_idx: int, spec_indent: int) -> int:
    return _TRANSFORMER._find_spec_block_end(lines, spec_idx, spec_indent)


def _set_storage_class(lines: list[str], class_name: str) -> list[str]:
    return _TRANSFORMER._set_storage_class(lines, class_name)


def split_yaml_documents(text: str) -> list[str]:
    return _TRANSFORMER.split_yaml_documents(text)


def render_yaml_documents(parts: list[str]) -> str:
    return _TRANSFORMER.render_yaml_documents(parts)


def transform_storage_class_manifest(text: str, class_name: str, clear_mode: bool) -> str:
    return _TRANSFORMER.transform_storage_class_manifest(text, class_name, clear_mode)


def build_arg_parser(default_file: Path) -> argparse.ArgumentParser:
    return _COMMAND.build_arg_parser(default_file)


def parse_config(argv: list[str] | None = None) -> SetStorageClassConfig:
    return _COMMAND.parse_config(argv)


def run(cfg: SetStorageClassConfig, fs: FileSystem, logger: logging.Logger) -> int:
    return _COMMAND.run(cfg, fs, logger)


def main(argv: list[str] | None = None) -> int:
    return _COMMAND.main(argv)


if __name__ == "__main__":
    sys.exit(main())
