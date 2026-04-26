"""Phase 16-B migration shim — moved to media_stack.domain.auth.csrf.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.csrf``. Removed in Phase 16-F.
"""

from media_stack.domain.auth.csrf import *  # noqa: F401, F403
from media_stack.domain.auth import csrf as _impl

# Re-export every public + private attribute (e.g. module-private
# helpers occasionally imported by tests) so ``from <shim> import _x``
# keeps resolving.
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
