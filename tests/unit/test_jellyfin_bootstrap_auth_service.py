import unittest

from media_stack.services.apps.jellyfin.cli.jellyfin_controller_auth_service import (
    JellyfinBootstrapAuthService,
)


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


    def test_rename_user_success(self):
        calls = []

        def _http_request(_base_url, path, **kwargs):
            calls.append((path, kwargs.get("method"), kwargs.get("payload")))
            if "/Users/" in path and kwargs.get("method") == "POST":
                return 204, None, ""
            return 404, None, ""

        logs = {"info": [], "warn": []}
        svc = JellyfinBootstrapAuthService(
            http_request=_http_request,
            info=lambda m: logs["info"].append(m),
            warn=lambda m: logs["warn"].append(m),
            fail=lambda m: (_ for _ in ()).throw(RuntimeError(m)),
        )

        result = svc.rename_user("http://x", "tok123", "user-id-1", "admin")
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "POST")
        self.assertEqual(calls[0][2]["Name"], "admin")
        self.assertTrue(any("renamed" in m for m in logs["info"]))

    def test_rename_user_failure(self):
        def _http_request(_base_url, path, **kwargs):
            return 400, None, "Bad request"

        logs = {"info": [], "warn": []}
        svc = JellyfinBootstrapAuthService(
            http_request=_http_request,
            info=lambda m: logs["info"].append(m),
            warn=lambda m: logs["warn"].append(m),
            fail=lambda m: (_ for _ in ()).throw(RuntimeError(m)),
        )

        result = svc.rename_user("http://x", "tok123", "user-id-1", "admin")
        self.assertFalse(result)
        self.assertTrue(any("failed to rename" in m for m in logs["warn"]))


if __name__ == "__main__":
    unittest.main()
