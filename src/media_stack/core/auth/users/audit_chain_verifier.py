"""Phase 16-B migration shim — moved to media_stack.application.auth.users.audit_chain_verifier."""

from media_stack.application.auth.users.audit_chain_verifier import *  # noqa: F401, F403
from media_stack.application.auth.users import audit_chain_verifier as _impl

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
