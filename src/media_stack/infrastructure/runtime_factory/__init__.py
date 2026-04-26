"""Runtime factory infrastructure layer.

ADR-0002 Phase 16-E (cross-cutting runtime_factory) — I/O-bound
helpers used during bootstrap composition: config-file loading,
YAML defaults discovery, profile-YAML merging, platform-specific
adapter-hook overlays.

Anything that opens a file, reads ``os.environ``, or imports
``yaml`` belongs here, not in ``application/`` or ``domain/``.
"""
