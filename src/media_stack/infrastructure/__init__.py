"""Infrastructure layer — cross-cutting technical concerns.

The infrastructure layer hosts the *non-domain* technical pieces
that every other layer leans on but that aren't tied to a
specific external system the domain talks to. Logging,
distributed tracing, metric emission, secret resolution,
event-bus plumbing, persistence primitives (a JSON store, a
SQLite store, a Redis client), notification fan-out, etc.

Status (Phase 16-A): scaffolding only — empty except for this
docstring. Real content lands in Phase 16-E, when:

* ``core/observability/`` → ``infrastructure/observability/``
* ``core/observability/logging/`` → ``infrastructure/logging/``
* ``core/events/`` → ``infrastructure/events/``
* ``core/notifications/`` → ``infrastructure/notifications/``
* a new ``infrastructure/secrets/`` package abstracts KMS / env /
  file-backed secret resolution.
* a new ``infrastructure/persistence/`` package hosts the JSON
  + SQLite stores currently scattered across ``core/`` and
  ``services/``.

Layering rules (enforced by ``tests/unit/test_architecture_layering.py``):

* ``infrastructure/`` MAY import from ``interfaces/`` (so the
  concrete logger, store, etc. can implement a declared port).
* ``infrastructure/`` MAY import from ``domain/`` *only* for
  pure-data types (entity dataclasses, value objects). It MUST
  NOT depend on domain *behaviour*.
* ``infrastructure/`` MUST NOT import from ``application/`` —
  that would be an upward dependency and would break the
  hexagon.
* ``infrastructure/`` MUST NOT import from ``adapters/`` — peers
  must not couple to each other directly. Cross-talk goes via
  ``interfaces/`` ports or via the composition root.

Why split this from ``adapters/``?

``adapters/`` hosts the per-external-system integrations:
``adapters/jellyfin/`` talks to Jellyfin, ``adapters/sonarr/``
talks to Sonarr. ``infrastructure/`` hosts the cross-cutting
plumbing every adapter and use case relies on (logger, tracer,
event bus). The split keeps "I changed how we ship logs" out of
"I changed how we talk to Jellyfin".
"""
