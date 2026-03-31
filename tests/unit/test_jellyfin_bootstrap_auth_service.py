import unittest

from scripts.cli.jellyfin_bootstrap_auth_service import JellyfinBootstrapAuthService


class JellyfinBootstrapAuthServiceTests(unittest.TestCase):
    def test_try_authenticate_jellyfin_success_and_failure(self):
        logs = {"info": [], "warn": [], "fail": []}

        def _http_request(_base_url, _path, **kwargs):
            payload = kwargs.get("payload") or {}
            if payload.get("Pw") == "good":
                return 200, {"AccessToken": "tok", "User": {"Id": "u1"}}, ""
            return 401, {"Message": "Unauthorized"}, "Unauthorized"

        svc = JellyfinBootstrapAuthService(
            http_request=_http_request,
            info=lambda m: logs["info"].append(m),
            warn=lambda m: logs["warn"].append(m),
            fail=lambda m: (_ for _ in ()).throw(RuntimeError(m)),
        )

        self.assertEqual(svc.try_authenticate_jellyfin("http://x", "admin", "good"), ("tok", "u1"))
        self.assertIsNone(svc.try_authenticate_jellyfin("http://x", "admin", "bad"))
        self.assertTrue(any("failed" in msg for msg in logs["warn"]))


if __name__ == "__main__":
    unittest.main()
