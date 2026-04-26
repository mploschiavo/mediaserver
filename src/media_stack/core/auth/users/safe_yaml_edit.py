"""Phase 16-B migration shim — moved to media_stack.infrastructure.auth.users.safe_yaml_edit."""

from media_stack.infrastructure.auth.users.safe_yaml_edit import *  # noqa: F401, F403
from media_stack.infrastructure.auth.users import safe_yaml_edit as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
