"""Phase 16-B migration shim — moved to media_stack.application.auth.users.provider_self_heal."""

from media_stack.application.auth.users.provider_self_heal import *  # noqa: F401, F403
from media_stack.application.auth.users import provider_self_heal as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
