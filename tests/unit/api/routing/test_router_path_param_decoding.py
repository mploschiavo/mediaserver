"""Path-param URL-decoding regression for ``Router.match``.

Path params come out of the regex's ``groupdict()`` in their raw
URL-encoded form. Without explicit decoding, an ID containing
``:`` (e.g. the guardrail rule ``storage:free_space_floor``)
arrives at the handler as ``storage%3Afree_space_floor`` and the
downstream registry lookup silently misses, producing
"unknown guardrail: storage%3Afree_space_floor" 404s on Test /
Save / Disable.

This test pins the decoding so the same class of bug can't
re-surface for any other ``{id}`` route — guardrails today,
profiles / users / job-names tomorrow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_stack.api.routing import (
    RouteModule,
    RouteModuleRegistry,
    Router,
    post,
)


@pytest.fixture
def reset_registry():
    RouteModuleRegistry.reset_for_tests()
    yield
    RouteModuleRegistry.reset_for_tests()


def _write_spec(tmp_path: Path) -> Path:
    import yaml
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "0.1"},
        "paths": {
            "/api/widgets/{id}/test": {
                "post": {"responses": {"200": {}}},
            },
        },
    }
    p = tmp_path / "openapi.yaml"
    p.write_text(yaml.safe_dump(spec))
    return p


class TestRouterDecodesPathParams:
    """Path-param values are URL-decoded before reaching the
    handler. Pins the ``storage%3Afree_space_floor`` regression."""

    def test_colon_in_id_is_decoded(
        self, tmp_path: Path, reset_registry: None,
    ) -> None:
        spec_path = _write_spec(tmp_path)

        class _WidgetsRoutes(RouteModule):
            @post("/api/widgets/{id}/test")
            def handle_test(self, handler, *, id: str) -> None:  # noqa: A002
                pass

        router = Router(openapi_path=spec_path, routes_package=None)

        match = router.match("POST", "/api/widgets/storage%3Afree_space_floor/test")
        assert match is not None, "router did not match the parameterized path"
        assert match.params == {"id": "storage:free_space_floor"}, (
            f"path param should be URL-decoded; got {match.params!r}"
        )

    def test_unencoded_colon_passes_through(
        self, tmp_path: Path, reset_registry: None,
    ) -> None:
        # Some clients send the colon literally (it's a valid URL
        # path char per RFC 3986 — only generic-delimiters need
        # encoding). Both shapes must reach the same decoded value.
        spec_path = _write_spec(tmp_path)

        class _WidgetsRoutes(RouteModule):
            @post("/api/widgets/{id}/test")
            def handle_test(self, handler, *, id: str) -> None:  # noqa: A002
                pass

        router = Router(openapi_path=spec_path, routes_package=None)

        match = router.match("POST", "/api/widgets/storage:free_space_floor/test")
        assert match is not None
        assert match.params == {"id": "storage:free_space_floor"}

    def test_percent_encoded_space_decodes(
        self, tmp_path: Path, reset_registry: None,
    ) -> None:
        # Defensive: anything else operators URL-encode (spaces,
        # slashes, special chars) decodes the same way.
        spec_path = _write_spec(tmp_path)

        class _WidgetsRoutes(RouteModule):
            @post("/api/widgets/{id}/test")
            def handle_test(self, handler, *, id: str) -> None:  # noqa: A002
                pass

        router = Router(openapi_path=spec_path, routes_package=None)

        match = router.match("POST", "/api/widgets/some%20id/test")
        assert match is not None
        assert match.params == {"id": "some id"}
