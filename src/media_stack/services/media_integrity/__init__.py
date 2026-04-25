"""Media-integrity subsystem.

Enforces the stack's "one video file per release" invariant across
every configured *arr by:

- ``policy.py``      — canonical ``ServarrPolicy`` dataclass +
                       YAML loader for ``contracts/servarr-policy.yaml``.
- ``arr_protocol.py`` — media-type-neutral ``ArrApp`` protocol +
                       ``MediaRelease``, ``MediaFile``, ``QualityProfile``
                       dataclasses that every adapter returns.
- ``adapters/``      — per-*arr adapters (Radarr/Sonarr/Lidarr/Readarr)
                       all inheriting from ``_ServarrBaseAdapter`` which
                       collapses the shared Servarr HTTP shape.
- ``enforcer.py``    — (turn 2) applies the policy to every adapter.
- ``reconciler.py``  — (turn 2) walks each adapter and heals
                       duplicates using each *arr's own quality score.

The design goal is that non-technical users NEVER see the underlying
duplication problem:

1. Boot-time enforcement applies the policy before users hit a knob.
2. Steady-state reconciler heals any drift within 15 min of it
   appearing, silently.
3. Only the (rare) indecisive case surfaces in the Security tab —
   and even then as a calm "needs review" chip, not a panic alert.

See ``docs/roadmap/session-visibility-followups.md`` for the
rationale trail.
"""
