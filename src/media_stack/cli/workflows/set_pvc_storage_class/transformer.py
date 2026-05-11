"""YamlPvcDocumentTransformer — Strategy for editing PVC manifests.

ADR-0015 Phase 7g. Pre-Phase-7g this class lived in
``cli/commands/set_pvc_storage_class_main.py`` next to the CLI
command class. It's pure text-transformation logic — split YAML
documents, find PVC kinds, set or clear ``spec.storageClassName``
— so it belongs in workflows/.

The 9 methods on this class are all stateless string/list
manipulation; the entry point :meth:`transform_storage_class_manifest`
takes a YAML text + flags and returns the rewritten YAML.
"""

from __future__ import annotations

import re


_DOC_SEPARATOR_RE = re.compile(r"(?m)^---\s*$")
_PVC_KIND_RE = re.compile(r"^\s*kind:\s*PersistentVolumeClaim\s*$")
_SPEC_RE = re.compile(r"^\s*spec:\s*$")
_STORAGE_CLASS_RE = re.compile(r"^\s*storageClassName:\s*")
_RESOURCES_RE = re.compile(r"^\s*resources:\s*$")


class YamlPvcDocumentTransformer:
    """Strategy: split, edit, render PVC YAML manifests."""

    def _indent_width(self, line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def _is_pvc_document(self, lines: list[str]) -> bool:
        return any(_PVC_KIND_RE.match(line) for line in lines)

    def _find_spec_index(self, lines: list[str]) -> int:
        for idx, line in enumerate(lines):
            if _SPEC_RE.match(line):
                return idx
        return -1

    def _remove_storage_class(self, lines: list[str]) -> list[str]:
        return [line for line in lines if not _STORAGE_CLASS_RE.match(line)]

    def _find_spec_block_end(
        self, lines: list[str], spec_idx: int, spec_indent: int,
    ) -> int:
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
            if _RESOURCES_RE.match(updated[idx]):
                insert_idx = idx
                break

        field_indent = " " * (spec_indent + 2)
        updated.insert(insert_idx, f"{field_indent}storageClassName: {class_name}")
        return updated

    def split_yaml_documents(self, text: str) -> list[str]:
        if not text.strip():
            return [""]
        return _DOC_SEPARATOR_RE.split(text)

    def render_yaml_documents(self, parts: list[str]) -> str:
        normalized = [part.strip("\n") for part in parts]
        return "\n---\n".join(normalized).rstrip("\n") + "\n"

    def transform_storage_class_manifest(
        self, text: str, class_name: str, clear_mode: bool,
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


__all__ = ["YamlPvcDocumentTransformer"]
