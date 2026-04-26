"""Media-integrity domain types — pure protocols, value objects, policy.

ADR-0002 Phase 16-E (cross-cutting media-integrity) — only the I/O-free,
framework-free parts of the media-integrity subsystem live here:

- ``arr_protocol.py``      — the ``ArrApp`` typing.Protocol + the
                             ``MediaRelease`` / ``MediaFile`` /
                             ``QualityProfile`` / ``AdapterCapabilities``
                             value objects every Servarr adapter returns.
- ``bazarr_protocol.py``   — the ``BazarrApp`` typing.Protocol + the
                             ``SubtitleRelease`` / ``SubtitleFile`` /
                             ``BazarrCapabilities`` value objects.
- ``policy.py``            — the canonical ``ServarrPolicy`` dataclass
                             plus the YAML loader. The loader's path
                             candidate list is load-bearing: see
                             ``test_policy_path_candidates_ratchet``.
- ``secret_scrub.py``      — pure structural-exception scrubber that
                             strips API-key shaped query params + bodies
                             from error strings before they reach audit.

Service / adapter implementations, the reconciler / enforcer, and the
factory all live in their respective hexagonal layers
(``application.media_integrity``, ``adapters.media_integrity``,
``infrastructure.media_integrity``).

This package may be imported from ``application/``, ``adapters/`` and
``infrastructure/`` freely — it depends on nothing outside the standard
library + the third-party YAML loader (for ``policy``).
"""
