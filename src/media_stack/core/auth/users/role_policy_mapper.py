"""Phase 16-B migration shim — moved to media_stack.domain.auth.users.role_policy_mapper."""

from media_stack.domain.auth.users.role_policy_mapper import *  # noqa: F401, F403
from media_stack.domain.auth.users import role_policy_mapper as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
