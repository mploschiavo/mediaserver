#!/usr/bin/env python3
"""Entry-point shim for ``bin/render-architecture-diagrams.sh``.

ADR-0015 Phase 7l moved :class:`RenderArchitectureDiagramsRunner`
to workflows/. What remains is argparse + back-compat aliases.
"""

from __future__ import annotations

import argparse
import os

from media_stack.cli.workflows.render_architecture_diagrams_runner import (
    RenderArchitectureDiagramsRunner,
)


class RenderArchitectureDiagramsEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner.run."""

    def __init__(self) -> None:
        self._runner = RenderArchitectureDiagramsRunner()

    @property
    def runner(self) -> RenderArchitectureDiagramsRunner:
        return self._runner

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="bin/render-architecture-diagrams.sh",
            description=(
                "Renders all .mmd files in a diagram directory to both "
                "SVG and PNG."
            ),
        )
        parser.add_argument("diagram_dir", nargs="?", default="docs/diagrams")
        parser.add_argument(
            "--mermaid-config-file",
            default=os.environ.get(
                "MERMAID_CONFIG_FILE",
                "docs/diagrams/mermaid-render-config.json",
            ),
        )
        parser.add_argument(
            "--width", type=int,
            default=int(os.environ.get("MMDC_WIDTH", "2200")),
        )
        parser.add_argument(
            "--height", type=int,
            default=int(os.environ.get("MMDC_HEIGHT", "1400")),
        )
        parser.add_argument(
            "--scale", type=int,
            default=int(os.environ.get("MMDC_SCALE", "2")),
        )
        return parser.parse_args(argv)

    def main(self, argv: list[str] | None = None) -> int:
        return self._runner.run(self.parse_args(argv))


_INSTANCE = RenderArchitectureDiagramsEntryPoint()
_RUNNER = _INSTANCE.runner
parse_args = _INSTANCE.parse_args
main = _INSTANCE.main
_render_with_mmdc = _RUNNER._render_with_mmdc
_render_with_kroki = _RUNNER._render_with_kroki


__all__ = [
    "RenderArchitectureDiagramsEntryPoint",
    "_render_with_kroki",
    "_render_with_mmdc",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    raise SystemExit(main())
