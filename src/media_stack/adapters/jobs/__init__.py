"""Jobs adapters — port implementations.

ADR-0002 Phase 16-E (cross-cutting jobs) — placeholder package. No
adapters live here yet because the job framework's runtime
dependencies (named-prereq registry, contract-driven handler
resolver) are all in-process callables, not ports waiting for
adapter implementations.

The package exists so the hexagonal layout has a consistent
``adapters/<subsystem>/`` slot even when empty — future
extractions (e.g. a port for "load contract YAMLs" so tests can
swap a fixture-backed implementation) will land here.
"""
