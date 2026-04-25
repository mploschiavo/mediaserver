"""App-scoped CLI shim package.

ADR-0002 Phase 16-D batch 1 (jellyfin) moved the CLI helpers out of
this package. Each file in this directory is now a re-export shim
pointing at the new home in ``infrastructure/jellyfin/`` (controller
helpers + entry-point ``*_main.py``) or ``application/jellyfin/``
(controller hooks, plugin activation). Phase 16-F removes shims.
"""
