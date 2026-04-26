"""Media-integrity application layer — orchestrators + use-cases.

ADR-0002 Phase 16-E (cross-cutting media-integrity) — the
orchestration half of the media-integrity subsystem. The pure
protocol types and the policy dataclass live in
``media_stack.domain.media_integrity``; the per-*arr adapter
implementations live in ``media_stack.adapters.media_integrity``;
the production factory lives in
``media_stack.infrastructure.media_integrity``. Everything that
applies the policy, walks adapters, picks duplicate winners, or
emits audit / event-bus signals lives here.

Modules:
- ``service.py``             — the top-level ``MediaIntegrityService``.
- ``enforcer.py``            — the Servarr config enforcer.
- ``reconciler.py``          — the Servarr duplicate reconciler.
- ``subtitle_reconciler.py`` — Bazarr settings enforcer + subtitle
                                duplicate reconciler.
- ``job_handlers.py``        — ``JobRunner.run`` entry points used by
                                ``contracts/services/media_integrity.yaml``.

Importing this package does NOT side-effect-register anything on a
singleton — the service is constructed by ``controller_serve`` at
boot via the infrastructure factory and stashed on
``api.services.media_integrity_handlers._instance``.
"""
