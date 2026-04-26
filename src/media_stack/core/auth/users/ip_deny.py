"""Phase 16-B migration shim — moved to media_stack.domain.auth.users.ip_deny."""

from media_stack.domain.auth.users.ip_deny import *  # noqa: F401, F403
from media_stack.domain.auth.users import ip_deny as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
