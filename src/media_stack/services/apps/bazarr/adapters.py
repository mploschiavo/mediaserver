"""Bazarr config helpers."""

from __future__ import annotations

import re
from typing import Dict, Tuple

_SECTION_RE = re.compile(r"^([A-Za-z0-9_]+):\s*$")


class BazarrAdapters:

    @staticmethod
    def _yaml_scalar(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value)
        if text == "":
            return "''"
        if re.search(r"[\s:#'\"]", text):
            return "'" + text.replace("'", "''") + "'"
        return text

    @staticmethod
    def _find_section_bounds(lines, section: str) -> Tuple[int, int]:
        start = -1
        for idx, line in enumerate(lines):
            if line.strip() == f"{section}:" and not line.startswith(" "):
                start = idx
                break
        if start < 0:
            return -1, -1

        end = len(lines)
        for idx in range(start + 1, len(lines)):
            if _SECTION_RE.match(lines[idx]):
                end = idx
                break
        return start, end

    @staticmethod
    def _find_key_block_bounds(lines, start: int, end: int, key: str) -> Tuple[int, int]:
        prefix = f"  {key}:"
        for idx in range(start + 1, end):
            if not lines[idx].startswith(prefix):
                continue

            block_end = idx + 1
            while block_end < end:
                line = lines[block_end]
                # Continuation lines for list/object values are indented deeper.
                # Bazarr occasionally stores sequences in "indentless" form:
                #   enabled_providers:
                #   - opensubtitlescom
                # so we must also consume sibling-indented list items (`"  -"`).
                if line.startswith("    ") or line.startswith("  -") or line.strip() == "":
                    block_end += 1
                    continue
                break
            return idx, block_end

        return -1, -1

    @staticmethod
    def _render_key_block(key: str, value) -> list[str]:
        if isinstance(value, list):
            if not value:
                return [f"  {key}: []"]
            rendered = [f"  {key}:"]
            for item in value:
                rendered.append(f"    - {_instance._yaml_scalar(item)}")
            return rendered

        return [f"  {key}: {_instance._yaml_scalar(value)}"]

    def apply_scalar_updates(self, text: str, updates: Dict[str, Dict[str, object]]):
        lines = text.splitlines()
        changed = False

        for section, section_updates in updates.items():
            start, end = self._find_section_bounds(lines, section)
            if start < 0:
                lines.append(f"{section}:")
                start = len(lines) - 1
                end = len(lines)
                changed = True

            for key, value in section_updates.items():
                desired_block = self._render_key_block(key, value)
                block_start, block_end = self._find_key_block_bounds(lines, start, end, key)
                if block_start >= 0:
                    current_block = lines[block_start:block_end]
                    if current_block != desired_block:
                        lines[block_start:block_end] = desired_block
                        changed = True
                        end += len(desired_block) - (block_end - block_start)
                else:
                    lines[end:end] = desired_block
                    end += len(desired_block)
                    changed = True

        rendered = "\n".join(lines) + "\n"
        return rendered, changed


_instance = BazarrAdapters()
apply_scalar_updates = _instance.apply_scalar_updates
_find_key_block_bounds = _instance._find_key_block_bounds
_find_section_bounds = _instance._find_section_bounds
_render_key_block = _instance._render_key_block
_yaml_scalar = _instance._yaml_scalar
