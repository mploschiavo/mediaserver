"""Application layer — use cases that orchestrate the domain.

If ``domain/`` answers *what does this concept mean*, ``application/``
answers *what does the system do when a user/operator asks for X*.
A use case here is typically a small class or function that
accepts a request, calls one or more domain operations, drives a
port (defined in ``interfaces/``) to do I/O, and returns a
response. The composition is explicit and the dependencies are
injected.

Status (Phase 16-A): scaffolding only — empty except for this
docstring. Real use-case content lands as bounded contexts migrate:

* ``application/auth/`` (Phase 16-B) — login, MFA enroll, OIDC
  exchange, session refresh.
* ``application/bootstrap/`` (Phase 16-E) — current
  ``services/runtime_factory/`` + ``services/runtime_*`` + bootstrap
  config generation.
* ``application/jobs/`` (Phase 16-E) — ``JobRunner`` orchestration
  on top of ``domain/jobs/``.
* ``application/media_integrity/`` (Phase 16-E) — enforce +
  reconcile use cases on top of ``domain/media_integrity/``.

Layering rules (enforced by ``tests/unit/test_architecture_layering.py``):

* ``application/`` MAY import from ``domain/``.
* ``application/`` MAY import from ``interfaces/`` (Protocols /
  ABCs — i.e. depend on ports, not implementations).
* ``application/`` MUST NOT import from ``adapters/``.
* ``application/`` MUST NOT import from ``infrastructure/``.

Why no imports from ``adapters/`` or ``infrastructure/``?

Use cases describe *what to do*, not *which adapter to do it
with*. The composition root (``__main__`` or a per-entry-point
factory under ``application/bootstrap/``) wires concrete adapters
to the use cases. This keeps use cases unit-testable with fake
ports — no live HTTP, no live database, no Kubernetes API.

A use case that "needs" a concrete adapter is a smell — extract
the surface it actually uses into a port under ``interfaces/`` and
have the adapter implement that port.

The composition root is the *only* place in the codebase that
references all four of {``domain``, ``application``, ``adapters``,
``infrastructure``}. Everywhere else the layering is strict.
"""
