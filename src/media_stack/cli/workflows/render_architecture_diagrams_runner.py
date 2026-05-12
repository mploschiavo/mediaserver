"""RenderArchitectureDiagramsRunner — render Mermaid .mmd → SVG + PNG.

ADR-0015 Phase 7l. Pre-Phase-7l this workflow lived inline in
``cli/commands/render_architecture_diagrams_main.py``. The class
shells out to ``mmdc`` / ``npx`` / ``kroki`` API and writes
output files; it's workflow material.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from media_stack.core.cli_common import run_command
from media_stack.core.exceptions import ConfigError, MediaStackError


_KROKI_RETRY_COUNT = "8"
_KROKI_RETRY_DELAY = "2"
_KROKI_CONNECT_TIMEOUT = "10"
_KROKI_MAX_TIME = "120"
_KROKI_URL_BASE = "https://kroki.io/mermaid"
_MERMAID_CLI_PACKAGE = "@mermaid-js/mermaid-cli@10.9.1"


class RenderArchitectureDiagramsRunner:
    """Workflow: find .mmd files and render each to .svg + .png."""

    def run(self, args: argparse.Namespace) -> int:
        diagram_dir = Path(args.diagram_dir).resolve()
        config_file = Path(args.mermaid_config_file).resolve()
        if not diagram_dir.exists() or not diagram_dir.is_dir():
            raise ConfigError(f"Diagram directory not found: {diagram_dir}")
        mmd_files = sorted(diagram_dir.glob("*.mmd"))
        if not mmd_files:
            raise ConfigError(f"No .mmd files found in {diagram_dir}")
        renderer = self._pick_renderer()
        for mmd_file in mmd_files:
            print(f"[INFO] Rendering {mmd_file.name}")
            renderer(mmd_file, config_file, args.width, args.height, args.scale)
        print(
            f"[OK] Rendered {len(mmd_files)} diagram(s) to SVG and PNG in "
            f"{diagram_dir}"
        )
        return 0

    def _pick_renderer(self):
        """Return a callable ``(mmd_file, config_file, w, h, s) -> None``."""
        local_mmdc = shutil.which("mmdc")
        if local_mmdc:
            return lambda f, c, w, h, s: self._render_with_mmdc(
                [local_mmdc], f, c, w, h, s,
            )
        local_npx = shutil.which("npx")
        if local_npx:
            command_prefix = [local_npx, "-y", _MERMAID_CLI_PACKAGE]
            return lambda f, c, w, h, s: self._render_with_mmdc(
                command_prefix, f, c, w, h, s,
            )
        local_curl = shutil.which("curl")
        if local_curl:
            return lambda f, _c, _w, _h, _s: self._render_with_kroki(f)
        raise MediaStackError("Neither mmdc, npx, nor curl is available.")

    def _render_with_mmdc(
        self,
        command_prefix: list[str],
        input_file: Path,
        config_file: Path,
        width: int,
        height: int,
        scale: int,
    ) -> None:
        common = [
            *command_prefix, "-i", str(input_file),
            "-w", str(width), "-H", str(height), "-s", str(scale),
        ]
        if config_file.exists():
            common.extend(["-c", str(config_file)])
        for suffix in (".svg", ".png"):
            out = input_file.with_suffix(suffix)
            run_command([*common, "-o", str(out)], check=True)

    def _render_with_kroki(self, input_file: Path) -> None:
        for output_format, suffix in (("svg", ".svg"), ("png", ".png")):
            out = input_file.with_suffix(suffix)
            run_command(
                [
                    "curl", "-fsS",
                    "--retry", _KROKI_RETRY_COUNT,
                    "--retry-delay", _KROKI_RETRY_DELAY,
                    "--retry-all-errors",
                    "--connect-timeout", _KROKI_CONNECT_TIMEOUT,
                    "--max-time", _KROKI_MAX_TIME,
                    "-H", "Content-Type: text/plain",
                    "--data-binary", f"@{input_file}",
                    "-o", str(out),
                    f"{_KROKI_URL_BASE}/{output_format}",
                ],
                check=True,
            )


__all__ = ["RenderArchitectureDiagramsRunner"]
