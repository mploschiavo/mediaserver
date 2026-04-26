"""Shim — moved to
``media_stack.infrastructure.jobs.bootstrap_config_generator`` in
ADR-0002 Phase 16-E. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so the legacy
``services.jobs.bootstrap_config_generator`` import path and the new
``infrastructure.jobs.bootstrap_config_generator`` path resolve to
the same module object. The console-script entry-point
``media-stack-generate-bootstrap-config`` in ``pyproject.toml`` still
points at this module's ``main`` callable; importing the legacy path
returns the impl module so ``main`` resolves there.

This module touches the filesystem (reads YAML, writes JSON) so it
lives in the infrastructure layer rather than application.
"""

import sys

from media_stack.infrastructure.jobs import bootstrap_config_generator as _impl

sys.modules[__name__] = _impl
