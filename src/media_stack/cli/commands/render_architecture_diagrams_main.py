#!/usr/bin/env python3
"""Render Mermaid architecture diagrams to SVG and PNG."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from media_stack.core.exceptions import ConfigError, MediaStackError

from media_stack.cli.workflows.cli_common import run_command






class RenderArchitectureDiagramsCommand:
    """Wraps diagram rendering CLI entrypoint."""

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="bin/render-architecture-diagrams.sh",
            description="Renders all .mmd files in a diagram directory to both SVG and PNG.",
        )
        parser.add_argument("diagram_dir", nargs="?", default="docs/diagrams")
        parser.add_argument("--mermaid-config-file", default=os.environ.get("MERMAID_CONFIG_FILE", "docs/diagrams/mermaid-render-config.json"))
        parser.add_argument("--width", type=int, default=int(os.environ.get("MMDC_WIDTH", "2200")))
        parser.add_argument("--height", type=int, default=int(os.environ.get("MMDC_HEIGHT", "1400")))
        parser.add_argument("--scale", type=int, default=int(os.environ.get("MMDC_SCALE", "2")))
        return parser.parse_args(argv)

    def main(self, argv: list[str] | None = None) -> int:
        args = self.parse_args(argv)
        diagram_dir = Path(args.diagram_dir).resolve()
        config_file = Path(args.mermaid_config_file).resolve()
        if not diagram_dir.exists() or not diagram_dir.is_dir():
            raise ConfigError(f"Diagram directory not found: {diagram_dir}")
        mmd_files = sorted(diagram_dir.glob("*.mmd"))
        if not mmd_files:
            raise ConfigError(f"No .mmd files found in {diagram_dir}")
        local_mmdc = shutil.which("mmdc")
        local_npx = shutil.which("npx")
        local_curl = shutil.which("curl")
        if local_mmdc:
            for mmd_file in mmd_files:
                print(f"[INFO] Rendering {mmd_file.name}")
                _render_with_mmdc([local_mmdc], mmd_file, config_file, args.width, args.height, args.scale)
        elif local_npx:
            command_prefix = [local_npx, "-y", "@mermaid-js/mermaid-cli@10.9.1"]
            for mmd_file in mmd_files:
                print(f"[INFO] Rendering {mmd_file.name}")
                _render_with_mmdc(command_prefix, mmd_file, config_file, args.width, args.height, args.scale)
        elif local_curl:
            for mmd_file in mmd_files:
                print(f"[INFO] Rendering {mmd_file.name}")
                _render_with_kroki(mmd_file)
        else:
            raise MediaStackError("Neither mmdc, npx, nor curl is available.")
        print(f"[OK] Rendered {len(mmd_files)} diagram(s) to SVG and PNG in {diagram_dir}")
        return 0


    @staticmethod
    def _render_with_mmdc(command_prefix, input_file, config_file, width, height, scale):
        common = [*command_prefix, "-i", str(input_file), "-w", str(width), "-H", str(height), "-s", str(scale)]
        if config_file.exists():
            common.extend(["-c", str(config_file)])
        for suffix in (".svg", ".png"):
            out = input_file.with_suffix(suffix)
            run_command([*common, "-o", str(out)], check=True)

    @staticmethod
    def _render_with_kroki(input_file):
        for output_format, suffix in (("svg", ".svg"), ("png", ".png")):
            out = input_file.with_suffix(suffix)
            run_command(["curl", "-fsS", "--retry", "8", "--retry-delay", "2", "--retry-all-errors",
                "--connect-timeout", "10", "--max-time", "120", "-H", "Content-Type: text/plain",
                "--data-binary", f"@{input_file}", "-o", str(out),
                f"https://kroki.io/mermaid/{output_format}"], check=True)


_instance = RenderArchitectureDiagramsCommand()
parse_args = _instance.parse_args
main = _instance.main

if __name__ == "__main__":
    raise SystemExit(main())
_render_with_mmdc = _instance._render_with_mmdc
_render_with_kroki = _instance._render_with_kroki
