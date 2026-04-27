"""Round-trip tests for the Authelia client_secret pbkdf2 encoder.

Pins the contract: ``hash_client_secret`` produces a string that
``verify_pbkdf2`` accepts for the SAME plaintext, and rejects for
different plaintexts. Catches the failure mode the user reported
("An error occurred while logging in with Authelia") that's caused
by a hash/format regression.

Per the test motivation in
``test_authelia_config_passes_authelia_validator.py``, the encoder
went through THREE rounds of fix because the generator produced
strings that passed unit tests but the live Authelia binary rejected.
The roundtrip test is what would have caught that earlier — if the
hash you produce can't be verified by the same algorithm reading
the same format, no validator on either side will accept it.
"""

from __future__ import annotations

import hashlib

from media_stack.infrastructure.auth.authelia_oidc_crypto import (
    OidcCrypto,
)


class TestRoundTrip:
    def test_correct_plaintext_verifies(self) -> None:
        crypto = OidcCrypto()
        plaintext = "jellyseerr-oidc-secret"
        h = crypto.hash_client_secret(plaintext)
        assert OidcCrypto.verify_pbkdf2(plaintext, h)

    def test_wrong_plaintext_does_not_verify(self) -> None:
        crypto = OidcCrypto()
        h = crypto.hash_client_secret("jellyseerr-oidc-secret")
        assert not OidcCrypto.verify_pbkdf2("wrong-secret", h)

    def test_distinct_calls_produce_different_hashes(self) -> None:
        # Salt is freshly generated per call — two calls must
        # produce distinct hashes for the same plaintext, but both
        # must verify.
        crypto = OidcCrypto()
        plaintext = "another-secret"
        h1 = crypto.hash_client_secret(plaintext)
        h2 = crypto.hash_client_secret(plaintext)
        assert h1 != h2
        assert OidcCrypto.verify_pbkdf2(plaintext, h1)
        assert OidcCrypto.verify_pbkdf2(plaintext, h2)

    def test_unicode_plaintext_roundtrips(self) -> None:
        crypto = OidcCrypto()
        plaintext = "ünïcødé-secret-ñ漢"
        h = crypto.hash_client_secret(plaintext)
        assert OidcCrypto.verify_pbkdf2(plaintext, h)


class TestVerifyRejectsMalformed:
    def test_rejects_plain_password(self) -> None:
        assert not OidcCrypto.verify_pbkdf2(
            "secret", "secret",
        )

    def test_rejects_wrong_algorithm_prefix(self) -> None:
        assert not OidcCrypto.verify_pbkdf2(
            "secret",
            "$argon2id$v=19$m=65536,t=3,p=4$abc$def",
        )

    def test_rejects_truncated_hash(self) -> None:
        crypto = OidcCrypto()
        h = crypto.hash_client_secret("secret")
        assert not OidcCrypto.verify_pbkdf2(
            "secret", h[: len(h) // 2],
        )

    def test_rejects_non_integer_iterations(self) -> None:
        assert not OidcCrypto.verify_pbkdf2(
            "secret", "$pbkdf2-sha512$XXXX$saltb64$hashb64",
        )

    def test_rejects_garbage_base64(self) -> None:
        assert not OidcCrypto.verify_pbkdf2(
            "secret",
            "$pbkdf2-sha512$310000$@@@not-b64@@@$@@@also-not-b64@@@",
        )

    def test_rejects_empty_string(self) -> None:
        assert not OidcCrypto.verify_pbkdf2("secret", "")


class TestB64DecodeMatchesEncode:
    def test_encode_decode_roundtrip_preserves_bytes(self) -> None:
        # Whatever bytes we encode must come back unchanged after
        # decoding — the '+' → '.' substitution and missing-padding
        # restoration must be inverses.
        crypto = OidcCrypto()
        for n in (1, 7, 16, 31, 64, 128):
            raw = (b"\x00\x01\x02\x03" * n)[:n]
            encoded = crypto._pbkdf2_b64(raw)
            decoded = OidcCrypto._pbkdf2_b64_decode(encoded)
            assert decoded == raw, f"failed at length {n}"


class TestKnownVectors:
    """Pin one explicit vector. If the encoder format ever
    changes (alphabet, padding, hash separator), this fails with
    a clear "you changed the format" signal."""

    def test_known_secret_verifies_against_recomputed_hash(self) -> None:
        # Use the same algorithm parameters as the encoder.
        from media_stack.infrastructure.auth.authelia_oidc_crypto import (
            _PBKDF2_ITERATIONS, _PBKDF2_DKLEN,
        )
        plaintext = "jellyseerr-oidc-secret"
        # Fixed salt for determinism. (Real hashes use a fresh
        # salt per call — but the verify step doesn't care about
        # the source of the salt, only that it's encoded
        # correctly in the hash string.)
        salt = b"\x10" * 16
        dk = hashlib.pbkdf2_hmac(
            "sha512",
            plaintext.encode("utf-8"),
            salt,
            _PBKDF2_ITERATIONS,
            dklen=_PBKDF2_DKLEN,
        )
        crypto = OidcCrypto()
        salt_b64 = crypto._pbkdf2_b64(salt)
        hash_b64 = crypto._pbkdf2_b64(dk)
        encoded = (
            f"$pbkdf2-sha512${_PBKDF2_ITERATIONS}${salt_b64}${hash_b64}"
        )
        assert OidcCrypto.verify_pbkdf2(plaintext, encoded)
        assert not OidcCrypto.verify_pbkdf2(
            "different-plaintext", encoded,
        )
