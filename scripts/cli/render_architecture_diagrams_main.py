#!/usr/bin/env python3
"""Render Mermaid architecture diagrams to SVG and PNG."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from core.exceptions import ConfigError, MediaStackError

from cli.cli_common import run_command


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="scripts/render-architecture-diagrams.sh",
        description=(
            "Renders all .mmd files in a diagram directory to both SVG and PNG. "
            "Renderer priority: mmdc -> npx mermaid-cli -> kroki."
        ),
    )
    parser.add_argument(
        "diagram_dir",
        nargs="?",
        default="docs/diagrams",
        help="Diagram directory containing .mmd files (default: docs/diagrams)",
    )
    parser.add_argument(
        "--mermaid-config-file",
        default=os.environ.get(
            "MERMAID_CONFIG_FILE",
            "docs/diagrams/mermaid-render-config.json",
        ),
        help="Optional Mermaid config file path",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=int(os.environ.get("MMDC_WIDTH", "2200")),
        help="Render width (default env MMDC_WIDTH or 2200)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=int(os.environ.get("MMDC_HEIGHT", "1400")),
        help="Render height (default env MMDC_HEIGHT or 1400)",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=int(os.environ.get("MMDC_SCALE", "2")),
        help="Render scale (default env MMDC_SCALE or 2)",
    )
    return parser.parse_args(argv)


def _render_with_mmdc(
    command_prefix: list[str],
    input_file: Path,
    config_file: Path,
    width: int,
    height: int,
    scale: int,
) -> None:
    common = [
        *command_prefix,
        "-i",
        str(input_file),
        "-w",
        str(width),
        "-H",
        str(height),
        "-s",
        str(scale),
    ]
    if config_file.exists():
        common.extend(["-c", str(config_file)])
    for suffix in (".svg", ".png"):
        out = input_file.with_suffix(suffix)
        run_command([*common, "-o", str(out)], check=True)


def _render_with_kroki(input_file: Path) -> None:
    for output_format, suffix in (("svg", ".svg"), ("png", ".png")):
        out = input_file.with_suffix(suffix)
        run_command(
            [
                "curl",
                "-fsS",
                "--retry",
                "8",
                "--retry-delay",
                "2",
                "--retry-all-errors",
                "--connect-timeout",
                "10",
                "--max-time",
                "120",
                "-H",
                "Content-Type: text/plain",
                "--data-binary",
                f"@{input_file}",
                "-o",
                str(out),
                f"https://kroki.io/mermaid/{output_format}",
            ],
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
        command_prefix = [local_mmdc]
        print("[INFO] Using local renderer: mmdc")
        print(f"[INFO] Render dimensions: {args.width}x{args.height} @ scale {args.scale}")
        if config_file.exists():
            print(f"[INFO] Mermaid config: {config_file}")
        for mmd_file in mmd_files:
            print(f"[INFO] Rendering {mmd_file.name}")
            _render_with_mmdc(
                command_prefix, mmd_file, config_file, args.width, args.height, args.scale
            )
    elif local_npx:
        command_prefix = [local_npx, "-y", "@mermaid-js/mermaid-cli@10.9.1"]
        print("[INFO] Using local renderer: npx @mermaid-js/mermaid-cli")
        print(f"[INFO] Render dimensions: {args.width}x{args.height} @ scale {args.scale}")
        if config_file.exists():
            print(f"[INFO] Mermaid config: {config_file}")
        for mmd_file in mmd_files:
            print(f"[INFO] Rendering {mmd_file.name}")
            _render_with_mmdc(
                command_prefix, mmd_file, config_file, args.width, args.height, args.scale
            )
    elif local_curl:
        print("[INFO] Using remote renderer: kroki.io")
        for mmd_file in mmd_files:
            print(f"[INFO] Rendering {mmd_file.name}")
            _render_with_kroki(mmd_file)
    else:
        raise MediaStackError("Neither mmdc, npx, nor curl is available.")

    print(f"[OK] Rendered {len(mmd_files)} diagram(s) to SVG and PNG in {diagram_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
