"""ADR-0005 Phase 5c.4c: file emptied.

This file previously tested the ``ControllerState`` action-lifecycle
surface (``start_action`` / ``finish_action`` / ``cancel_action`` /
``add_pending`` / ``pop_pending`` / ``action_running`` / ``get_action``)
plus ``ActionRecord`` lifecycle, ``pending_actions`` queue tracking,
and the ``current_action``-tag log buffer behaviour. Phase 5c.4c
retired the entire surface — see
``tests/unit/architecture/test_no_controller_state_action_lifecycle.py``
for the architecture ratchet pinning that none of those names exist
anymore on ``ControllerState``.

The remaining covered behaviours moved to:

* ``ActionRecord`` lifecycle (the value object) — covered in
  ``tests/unit/architecture/test_state_architecture.py::TestActionRecord``.
* Log-line action tagging — covered in
  ``tests/unit/core/test_log_filtering.py`` via the
  ``runtime_platform.current_action_tag`` contextmanager.
* Action priority constants (``ACTION_PRIORITY``) — covered in
  ``tests/integration/bootstrap/test_controller_main_dispatch.py``.

The file is left in place as a tombstone so the deletion doesn't
require a separate filesystem commit. A follow-up cleanup can drop
the file once the broader phase 5 tree is stable.
"""
