"""App-scoped CLI implementation package.

ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent)
deliberately leaves the CLI helpers in place at this legacy path
because their tests load the module via absolute file path and
patch helpers in this module's namespace. A follow-up batch will
revisit once the file-path-based test loaders are migrated to
import the new module path.
"""
