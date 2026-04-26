"""Guardrails domain types — pure protocols + value objects.

ADR-0002 Phase 16-E (cross-cutting guardrails) — only the I/O-free,
framework-free parts of the guardrails subsystem live here. The
``Guardrail`` Protocol, the ``Trigger`` / ``Action`` value objects,
and the ``Severity`` / ``Domain`` literal types are the contract every
concrete rule implementation must satisfy. Rule implementations,
the registry, the evaluation loop, and the state collector live in
``media_stack.application.guardrails`` because they either drive
side-effect registration on a singleton or perform I/O at tick time.

This package may be imported from ``application/`` and ``adapters/``
freely — it depends on nothing outside the standard library.
"""
