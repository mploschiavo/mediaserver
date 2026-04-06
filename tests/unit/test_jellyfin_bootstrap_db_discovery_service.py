import unittest
from unittest.mock import patch

from media_stack.services.apps.jellyfin.cli import (
    jellyfin_bootstrap_db_discovery_service as svc,
)


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class JellyfinBootstrapDbDiscoveryServiceTests(unittest.TestCase):
    def test_returns_empty_when_pod_lookup_fails(self):
        warnings: list[str] = []
        with patch.object(
            svc,
            "run_cmd",
            return_value=_Proc(returncode=1, stdout="", stderr="not found"),
        ):
            key, user_id = svc.discover_api_key_from_jellyfin_db(
                ["kubectl"],
                "media-stack",
                "jellyfin",
                ["media-stack-bootstrap"],
                "admin",
                warn=lambda msg: warnings.append(msg),
            )

        self.assertEqual((key, user_id), ("", ""))
        self.assertTrue(any("Could not resolve Jellyfin pod" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
