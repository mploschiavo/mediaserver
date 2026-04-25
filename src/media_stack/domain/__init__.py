"""Domain layer — pure business rules, no I/O, no frameworks.

This package is the heart of the application. It expresses *what
the system means* in language a domain expert would recognise: a
``User`` exists, a ``Session`` can expire, a ``MediaIntegrityIssue``
has a severity, a ``Job`` has a state machine. None of that
vocabulary should depend on whether the user-store is JSON, YAML,
SQLite, or RAM; on whether the network is HTTP or gRPC; or on
whether the runtime is Compose, Kubernetes, or a Python REPL.

Status (Phase 16-A): scaffolding only — this package is empty
except for this docstring. Real domain content lands in 16-B and
beyond, when bounded contexts (auth, media-integrity, guardrails,
jobs, content, routing, promises) migrate from their current
homes (``core/auth/``, ``services/media_integrity/`` etc.) into
sub-packages here.

Layering rules (enforced by ``tests/unit/test_architecture_layering.py``):

* ``domain/`` MUST NOT import from ``adapters/``.
* ``domain/`` MUST NOT import from ``infrastructure/``.
* ``domain/`` MUST NOT import from ``application/``.
* ``domain/`` MUST NOT import from concrete tech under
  ``services/apps/<tech>/``.
* ``domain/`` MAY import from ``interfaces/`` — Protocols /
  Abstract Base Classes that declare ports without binding to a
  concrete implementation.
* ``domain/`` MAY import from the standard library and from
  pure-data dependencies (e.g. ``pydantic``, ``attrs``,
  ``dataclasses``).

Why these rules?

1. **Testability.** A unit test for a domain rule should never
   need to mock a database, an HTTP client, or a Kubernetes API.
   If it does, the rule has leaked.
2. **Dependency-inversion.** The domain expresses what it needs
   from the outside world via an ``interfaces/`` port. The
   ``adapters/`` and ``infrastructure/`` layers implement those
   ports. Wiring happens at composition root in
   ``application/`` or ``__main__``.
3. **Replaceability.** Re-platforming from Compose to Kubernetes,
   from JSON files to SQLite, or from one auth provider to
   another touches ``adapters/`` and ``infrastructure/`` only —
   never ``domain/``.

When in doubt, ask: *"would this code change if we swapped out
the database / HTTP framework / deployment platform?"* If yes, it
does not belong in ``domain/``.
"""
