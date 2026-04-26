"""Phase 16-B migration shim — moved to media_stack.adapters.auth.authelia.config_generator.

Re-exports preserve back-compat for callers that still import via
``media_stack.core.auth.authelia_config_generator``. Removed in Phase 16-F.
"""

from media_stack.adapters.auth.authelia.config_generator import *  # noqa: F401, F403
from media_stack.adapters.auth.authelia import config_generator as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
