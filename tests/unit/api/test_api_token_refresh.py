"""Unit tests for access/refresh token rotation + family revocation."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.core.auth.api_token_store import ApiTokenStore


class RefreshTokenTests(unittest.TestCase):
    def _store(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        return ApiTokenStore(Path(self._tmp.name) / "tokens.json")

    def test_mint_pair_returns_access_and_refresh(self):
        s = self._store()
        (access, a_plain), (refresh, r_plain) = s.mint_pair(
            owner_username="alice", name="ci",
        )
        self.assertEqual(access.kind, "access")
        self.assertEqual(refresh.kind, "refresh")
        self.assertEqual(access.family_id, refresh.family_id)
        self.assertNotEqual(a_plain, r_plain)
        self.assertTrue(access.expires_at)
        self.assertTrue(refresh.expires_at)

    def test_verify_accepts_access_rejects_refresh(self):
        s = self._store()
        (_, a_plain), (_, r_plain) = s.mint_pair(
            owner_username="alice", name="ci",
        )
        self.assertIsNotNone(s.verify(a_plain))
        # Refresh tokens MUST NOT authenticate API calls directly.
        self.assertIsNone(s.verify(r_plain))

    def test_rotate_returns_new_pair_and_revokes_old(self):
        s = self._store()
        (_, _), (old_refresh, old_r_plain) = s.mint_pair(
            owner_username="alice", name="ci",
        )
        result = s.rotate(old_r_plain)
        self.assertIsNotNone(result)
        (new_access, new_a_plain), (new_refresh, new_r_plain) = result
        self.assertEqual(new_refresh.kind, "refresh")
        self.assertEqual(new_access.family_id, old_refresh.family_id)
        self.assertEqual(new_refresh.parent_id, old_refresh.id)
        # Old refresh is revoked — a second rotate with the same
        # token must fail (replay detection).
        self.assertIsNone(s.rotate(old_r_plain))
        # New refresh works for one more rotation.
        self.assertIsNotNone(s.rotate(new_r_plain))

    def test_rotate_on_invalid_refresh_returns_none(self):
        s = self._store()
        self.assertIsNone(s.rotate("definitely-not-a-real-token-xxxxxx"))

    def test_rotate_rejects_access_token_as_refresh(self):
        s = self._store()
        (_access, a_plain), (_, _) = s.mint_pair(
            owner_username="alice", name="ci",
        )
        # Submitting the access plaintext to the refresh endpoint is
        # not allowed.
        self.assertIsNone(s.rotate(a_plain))

    def test_revoke_family_kills_all_chain_members(self):
        s = self._store()
        (_, a1), (refresh, r1) = s.mint_pair(
            owner_username="alice", name="ci",
        )
        # Rotate once so the family has (access1+refresh1) + (access2+refresh2).
        (_new_access, a2), (_, r2) = s.rotate(r1)
        killed = s.revoke_family(refresh.family_id)
        # 3 live tokens after rotate (old refresh already revoked):
        # access1, access2, new refresh → all 3 get killed.
        self.assertEqual(killed, 3)
        self.assertIsNone(s.verify(a1))
        self.assertIsNone(s.verify(a2))
        self.assertIsNone(s.rotate(r2))

    def test_empty_family_id_revokes_nothing(self):
        s = self._store()
        s.mint_pair(owner_username="alice", name="ci")
        self.assertEqual(s.revoke_family(""), 0)

    def test_long_lived_token_path_unchanged(self):
        """The legacy create() path for long-lived tokens still works
        and its tokens still verify()."""
        s = self._store()
        tok, plain = s.create(
            owner_username="bob", name="legacy", scope="admin",
        )
        self.assertEqual(tok.kind, "long_lived")
        self.assertEqual(tok.family_id, "")
        verified = s.verify(plain)
        self.assertIsNotNone(verified)
        self.assertEqual(verified.kind, "long_lived")


if __name__ == "__main__":
    unittest.main()
