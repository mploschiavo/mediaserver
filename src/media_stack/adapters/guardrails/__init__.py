"""Guardrails adapters — intentionally empty.

ADR-0002 Phase 16-E (cross-cutting guardrails) — the guardrails
subsystem is cross-cutting, not per-tech, so there are no port
implementations to adapt. The directory exists for symmetry with
the rest of the hexagonal layout and to give future per-tech
remediation adapters (e.g. a qBit-cleanup adapter that implements
a remediation port) a natural home.
"""
