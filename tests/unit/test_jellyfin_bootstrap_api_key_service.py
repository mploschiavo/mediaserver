import unittest

from scripts.bootstrap_services.apps.jellyfin.cli import jellyfin_bootstrap_api_key_service as svc


class JellyfinBootstrapApiKeyServiceTests(unittest.TestCase):
    def test_validate_and_lookup_user_id(self):
        def _http_request(_base_url, path, **_kwargs):
            if "api_key=good" in path:
                return 200, [{"Name": "admin", "Id": "u1", "Policy": {"IsAdministrator": True}}], ""
            return 401, None, "unauthorized"

        self.assertTrue(svc.validate_api_key("http://x", "good", http_request=_http_request))
        self.assertFalse(svc.validate_api_key("http://x", "bad", http_request=_http_request))
        self.assertEqual(
            svc.lookup_user_id_with_api_key(
                "http://x",
                "good",
                "admin",
                http_request=_http_request,
            ),
            "u1",
        )


if __name__ == "__main__":
    unittest.main()
