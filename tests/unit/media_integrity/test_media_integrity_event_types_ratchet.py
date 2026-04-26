"""Ratchet test pinning every wire-contract string on the
media-integrity event/audit surface.

Why a ratchet?
--------------
Each ``EVENT_TYPE`` and audit-action constant is part of a wire
contract between the ``MediaIntegrityService`` and its subscribers
(UI, metrics dispatcher, notification dispatcher, audit-log filters).
A drive-by rename on the producer side without a coordinated update
on every consumer = silent breakage: the UI's "needs review" chip
stops appearing, metrics counters stop ticking, the notifications
dispatcher stops paging on enforce failures.

This file pins those strings by exact equality so any rename, even
a trivial casing tweak, fails loudly during ``pytest`` collection
on the producing branch — forcing the author to touch this ratchet
file and confront the contract change explicitly.

We pin three layers:

1. ``EVENT_TYPE`` constants on each event class (the bus contract).
2. The audit-action constants in ``audit_actions`` (the audit log
   contract).
3. The ``MEDIA_INTEGRITY_EVENTS`` frozenset (the action-filter
   contract — the UI builds its filter dropdown from this set).

Note: event types use dots (``media_integrity.config_enforced``)
while audit actions use underscores (``media_integrity_config_enforced``).
That difference is intentional — events live in a dot-namespaced
event bus, audit actions live as flat strings — so we pin both
separately.
"""

from __future__ import annotations

import dataclasses
from typing import ClassVar, get_type_hints

from media_stack.core.auth.users.audit_actions import (
    MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
    MEDIA_INTEGRITY_CONFIG_ENFORCED,
    MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
    MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED,
    MEDIA_INTEGRITY_EVENTS,
    MEDIA_INTEGRITY_RECONCILE_FAILED,
)
from media_stack.core.events import (
    MediaIntegrityConfigEnforced,
    MediaIntegrityConfigEnforceFailed,
    MediaIntegrityDuplicateResolved,
    MediaIntegrityDuplicateReviewNeeded,
    MediaIntegrityReconcileFailed,
)
from media_stack.core.events.bus import Event


# ---------------------------------------------------------------------------
# EVENT_TYPE strings — exact-string ratchet
# ---------------------------------------------------------------------------


def test_config_enforced_event_type_pinned() -> None:
    assert MediaIntegrityConfigEnforced.EVENT_TYPE == "media_integrity.config_enforced"


def test_config_enforce_failed_event_type_pinned() -> None:
    assert (
        MediaIntegrityConfigEnforceFailed.EVENT_TYPE
        == "media_integrity.config_enforce_failed"
    )


def test_duplicate_resolved_event_type_pinned() -> None:
    assert (
        MediaIntegrityDuplicateResolved.EVENT_TYPE
        == "media_integrity.duplicate_resolved"
    )


def test_duplicate_review_needed_event_type_pinned() -> None:
    assert (
        MediaIntegrityDuplicateReviewNeeded.EVENT_TYPE
        == "media_integrity.duplicate_review_needed"
    )


def test_reconcile_failed_event_type_pinned() -> None:
    assert MediaIntegrityReconcileFailed.EVENT_TYPE == "media_integrity.reconcile_failed"


def test_full_event_type_set_pinned() -> None:
    """Adding a new media-integrity event without updating this
    ratchet must fail. The frozenset comparison forces the author
    to come here and acknowledge the new wire contract."""
    expected: frozenset[str] = frozenset(
        {
            "media_integrity.config_enforced",
            "media_integrity.config_enforce_failed",
            "media_integrity.duplicate_resolved",
            "media_integrity.duplicate_review_needed",
            "media_integrity.reconcile_failed",
        }
    )
    actual: frozenset[str] = frozenset(
        {
            MediaIntegrityConfigEnforced.EVENT_TYPE,
            MediaIntegrityConfigEnforceFailed.EVENT_TYPE,
            MediaIntegrityDuplicateResolved.EVENT_TYPE,
            MediaIntegrityDuplicateReviewNeeded.EVENT_TYPE,
            MediaIntegrityReconcileFailed.EVENT_TYPE,
        }
    )
    assert actual == expected


# ---------------------------------------------------------------------------
# Class shape — frozen dataclass + Event subclass + ClassVar EVENT_TYPE
# ---------------------------------------------------------------------------


_EVENT_CLASSES = (
    MediaIntegrityConfigEnforced,
    MediaIntegrityConfigEnforceFailed,
    MediaIntegrityDuplicateResolved,
    MediaIntegrityDuplicateReviewNeeded,
    MediaIntegrityReconcileFailed,
)


def test_every_event_class_is_subclass_of_event() -> None:
    for cls in _EVENT_CLASSES:
        assert issubclass(cls, Event), f"{cls.__name__} must subclass Event"


def test_every_event_class_is_frozen_dataclass() -> None:
    for cls in _EVENT_CLASSES:
        assert dataclasses.is_dataclass(cls), f"{cls.__name__} must be a dataclass"
        params = getattr(cls, "__dataclass_params__", None)
        assert params is not None
        assert params.frozen is True, f"{cls.__name__} must be frozen=True"


def test_every_event_class_has_event_type_classvar() -> None:
    """``EVENT_TYPE`` must remain a ``ClassVar[str]`` — not a regular
    dataclass field. That's what keeps the bus's auto-population in
    ``Event.__post_init__`` working without callers passing it."""
    for cls in _EVENT_CLASSES:
        hints = get_type_hints(cls, include_extras=False)
        # ClassVar hints are excluded from get_type_hints's regular
        # output, so we cross-check by raw annotation lookup:
        raw = cls.__annotations__.get("EVENT_TYPE", "")
        assert "ClassVar" in str(raw), (
            f"{cls.__name__}.EVENT_TYPE must be ClassVar[str], got {raw!r}"
        )
        # And ensure it's a non-empty str on the class.
        assert isinstance(cls.EVENT_TYPE, str) and cls.EVENT_TYPE
        # Hints sanity-check (no accidental duplicate as a dataclass
        # field): EVENT_TYPE should not appear in dataclass fields.
        field_names = {f.name for f in dataclasses.fields(cls)}
        assert "EVENT_TYPE" not in field_names


def test_every_event_type_uses_media_integrity_dot_prefix() -> None:
    """All media-integrity events live in the ``media_integrity.*``
    namespace on the bus. A bare or differently-namespaced
    ``EVENT_TYPE`` should fail this ratchet."""
    for cls in _EVENT_CLASSES:
        assert cls.EVENT_TYPE.startswith("media_integrity."), cls.EVENT_TYPE


# ---------------------------------------------------------------------------
# Audit-action constants — separate wire contract, separate ratchet
# ---------------------------------------------------------------------------


def test_audit_action_config_enforced_pinned() -> None:
    assert MEDIA_INTEGRITY_CONFIG_ENFORCED == "media_integrity_config_enforced"


def test_audit_action_config_enforce_failed_pinned() -> None:
    assert (
        MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED == "media_integrity_config_enforce_failed"
    )


def test_audit_action_duplicate_resolved_pinned() -> None:
    assert MEDIA_INTEGRITY_DUPLICATE_RESOLVED == "media_integrity_duplicate_resolved"


def test_audit_action_duplicate_review_needed_pinned() -> None:
    assert (
        MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED
        == "media_integrity_duplicate_review_needed"
    )


def test_audit_action_reconcile_failed_pinned() -> None:
    assert MEDIA_INTEGRITY_RECONCILE_FAILED == "media_integrity_reconcile_failed"


def test_media_integrity_events_frozenset_pinned() -> None:
    """The action-filter contract: ``MEDIA_INTEGRITY_EVENTS`` is the
    canonical set of audit-action strings the UI offers in its action
    picker. Adding/removing an action without updating this ratchet
    means the picker silently drifts from the producer."""
    expected: frozenset[str] = frozenset(
        {
            "media_integrity_config_enforced",
            "media_integrity_config_enforce_failed",
            "media_integrity_duplicate_resolved",
            "media_integrity_duplicate_review_needed",
            "media_integrity_reconcile_failed",
        }
    )
    assert MEDIA_INTEGRITY_EVENTS == expected
    # And it must really be a frozenset — mutability would let a
    # consumer accidentally edit the contract at import time.
    assert isinstance(MEDIA_INTEGRITY_EVENTS, frozenset)


def test_audit_actions_use_underscore_namespace_not_dot() -> None:
    """Intentional split: events use dots, audit actions use
    underscores. Pin that asymmetry so neither side accidentally
    starts mirroring the other."""
    for action in MEDIA_INTEGRITY_EVENTS:
        assert "." not in action, action
        assert action.startswith("media_integrity_"), action


def test_event_type_and_audit_action_one_to_one() -> None:
    """Each event type has a 1:1 audit-action sibling — same name,
    different separator. Pinning the mapping prevents one side from
    growing a new entry without the other."""
    expected_pairs: dict[str, str] = {
        MediaIntegrityConfigEnforced.EVENT_TYPE: MEDIA_INTEGRITY_CONFIG_ENFORCED,
        MediaIntegrityConfigEnforceFailed.EVENT_TYPE: MEDIA_INTEGRITY_CONFIG_ENFORCE_FAILED,
        MediaIntegrityDuplicateResolved.EVENT_TYPE: MEDIA_INTEGRITY_DUPLICATE_RESOLVED,
        MediaIntegrityDuplicateReviewNeeded.EVENT_TYPE: (
            MEDIA_INTEGRITY_DUPLICATE_REVIEW_NEEDED
        ),
        MediaIntegrityReconcileFailed.EVENT_TYPE: MEDIA_INTEGRITY_RECONCILE_FAILED,
    }
    for event_type, audit_action in expected_pairs.items():
        # Same identifier, different separator: replacing dots with
        # underscores in the event type produces the audit-action.
        assert event_type.replace(".", "_") == audit_action
