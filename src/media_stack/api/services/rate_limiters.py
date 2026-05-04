"""Shared rate-limiter singletons for POST routes.

Lifted from ``media_stack.api.handlers_post`` during ADR-0007 Phase 2
Phase E (legacy-handler retirement).

The buckets here back per-IP-or-per-account throttling on sensitive
mutations. Each is a ``RateLimiter`` (token bucket): ``capacity`` is
the burst budget, ``refill_per_second`` the sustained rate. Callers
key on ``client_id`` (typically the account / subject of the
operation) so credential-stuffing across many usernames still trips
the same bucket.

Buckets:

* ``_global_post_limiter`` -- per-IP rate limit applied to EVERY
  POST. 30 burst, 3/s sustained. Anyone exceeding this is clearly
  not a human.
* ``_user_mgmt_limiter`` -- narrow bucket for sensitive user-mgmt
  POSTs (invites, role edits, bulk import). 10 burst, 1/s sustained.
* ``_pw_reset_limiter`` -- per-account password-reset bucket.
  3 burst, ~1 token / 20s sustained -- slow, deliberate. Same
  bucket also gates ``GET /api/password-tickets/{ticket_id}``.
"""

from __future__ import annotations

from media_stack.core.auth.rate_limiter import RateLimiter


# Global per-IP rate limit applied to EVERY POST. Wider bucket than the
# user-mgmt one because it covers all mutating traffic, not just
# sensitive ops. Anyone exceeding this is clearly not a human.
_GLOBAL_POST_CAPACITY = 30
_GLOBAL_POST_REFILL = 3.0  # 3 tokens/sec sustained
_global_post_limiter = RateLimiter(
    capacity=_GLOBAL_POST_CAPACITY,
    refill_per_second=_GLOBAL_POST_REFILL,
)

_USER_MGMT_BUCKET_CAPACITY = 10
_USER_MGMT_REFILL_PER_SECOND = 1.0
_user_mgmt_limiter = RateLimiter(
    capacity=_USER_MGMT_BUCKET_CAPACITY,
    refill_per_second=_USER_MGMT_REFILL_PER_SECOND,
)

# Per-account rate limit for password reset -- prevents an attacker from
# brute-forcing reset endpoints by rotating IPs.
_PW_RESET_BUCKET_CAPACITY = 3
_PW_RESET_REFILL_PER_SECOND = 0.05  # ~1 token per 20s = slow, deliberate
_pw_reset_limiter = RateLimiter(
    capacity=_PW_RESET_BUCKET_CAPACITY,
    refill_per_second=_PW_RESET_REFILL_PER_SECOND,
)


__all__ = [
    "_global_post_limiter",
    "_user_mgmt_limiter",
    "_pw_reset_limiter",
]
