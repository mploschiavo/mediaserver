"""Indexers + import-lists POST routes (ADR-0007 Phase 2 wave 8 group 2).

Migrates the four content-domain write endpoints off the
``handlers_post.handle()`` elif chain onto the OpenAPI Router.

Routes:

* ``POST /api/indexers/{indexer_id}``                      — DELETE
  tunneled via ``{"_method": "DELETE"}`` body. The spec declares
  the path as POST-only because the legacy chain abuses POST as a
  DELETE channel; the new RouteModule keeps the same shape so
  existing callers don't break.
* ``POST /api/indexers/{indexer_id}/toggle``               — toggle
  enabled flag. Body: ``{enable: bool}`` (default True).
* ``POST /api/import-lists/{service}/{list_id}/delete``    — delete
  an import list on a specific arr service.
* ``POST /api/import-lists/{service}/{list_id}/toggle``    — toggle
  enabled flag. Body: ``{enabled: bool}`` (default True). Note the
  spec uses ``enabled`` (vs. indexers' ``enable``) — preserved
  verbatim from the legacy chain.

OO discipline:

* ``IndexersImportListsPostRoutes`` is a ``RouteModule`` subclass
  with instance methods only. Constructor-injects ``ContentService``
  + ``IntIdResolver`` + ``PostMutationGate`` so tests can swap
  any collaborator without monkey-patching.

Anti-pattern guard rails:

* No lazy-cache resolver shape — the content service adapter
  resolves every callable fresh per call so ``mock.patch`` of the
  canonical symbol takes effect.
* Path params are typed ``str`` in the method signatures (per the
  Router contract) and coerced to ``int`` inside the dedicated
  ``IntIdResolver`` Strategy — keeps the route bodies one-liners.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Callable

from media_stack.api.routes.post_admin_ops import PostMutationGate
from media_stack.api.routing import RouteModule, post


_BODY_METHOD_DELETE = "DELETE"


class ContentService:
    """Adapter onto ``content`` service callables.

    Each adapter caches ONLY the constructor-injected callable;
    the default path does a fresh module attribute lookup per
    call so ``mock.patch`` of the canonical symbol takes effect.
    """

    def __init__(
        self,
        toggle_indexer_fn: Callable[..., dict[str, Any]] | None = None,
        delete_indexer_fn: Callable[..., dict[str, Any]] | None = None,
        toggle_import_list_fn: Callable[..., dict[str, Any]] | None = None,
        delete_import_list_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._toggle_indexer = toggle_indexer_fn
        self._delete_indexer = delete_indexer_fn
        self._toggle_import_list = toggle_import_list_fn
        self._delete_import_list = delete_import_list_fn

    def toggle_indexer(
        self, indexer_id: int, enable: bool,
    ) -> dict[str, Any]:
        if self._toggle_indexer is not None:
            return self._toggle_indexer(indexer_id, enable)
        from media_stack.api.services import content as content_svc
        return content_svc.toggle_indexer(indexer_id, enable)

    def delete_indexer(self, indexer_id: int) -> dict[str, Any]:
        if self._delete_indexer is not None:
            return self._delete_indexer(indexer_id)
        from media_stack.api.services import content as content_svc
        return content_svc.delete_indexer(indexer_id)

    def toggle_import_list(
        self, service_id: str, list_id: int, enabled: bool,
    ) -> dict[str, Any]:
        if self._toggle_import_list is not None:
            return self._toggle_import_list(service_id, list_id, enabled)
        from media_stack.api.services import content as content_svc
        return content_svc.toggle_import_list(
            service_id, list_id, enabled,
        )

    def delete_import_list(
        self, service_id: str, list_id: int,
    ) -> dict[str, Any]:
        if self._delete_import_list is not None:
            return self._delete_import_list(service_id, list_id)
        from media_stack.api.services import content as content_svc
        return content_svc.delete_import_list(service_id, list_id)


class IntIdResolver:
    """Strategy that parses + validates an integer path param.

    Returns ``(int_value, None)`` on success or
    ``(None, error_body)`` on a non-integer input. The error
    label is constructor-injected so the same Strategy serves
    both ``Invalid indexer ID`` and ``Invalid list ID`` cases.
    """

    def __init__(self, *, label: str) -> None:
        self._error_message = f"Invalid {label}"

    def parse(
        self, raw: Any,
    ) -> tuple[int | None, dict[str, Any] | None]:
        try:
            return int(raw), None
        except (TypeError, ValueError):
            return None, {"error": self._error_message}


class IndexersImportListsPostRoutes(RouteModule):
    """Indexer + import-list POST routes — toggle and delete.

    The Router auto-discovers + instantiates this class + walks
    its tagged methods at startup. Constructor defaults keep
    auto-discovery zero-arg while letting tests swap any
    collaborator.
    """

    def __init__(
        self,
        *,
        mutation_gate: PostMutationGate | None = None,
        content_service: ContentService | None = None,
        indexer_id_resolver: IntIdResolver | None = None,
        list_id_resolver: IntIdResolver | None = None,
    ) -> None:
        self._gate = mutation_gate or PostMutationGate()
        self._content = content_service or ContentService()
        self._indexer_id_resolver = (
            indexer_id_resolver or IntIdResolver(label="indexer ID")
        )
        self._list_id_resolver = (
            list_id_resolver or IntIdResolver(label="list ID")
        )

    # --- gate helper ---------------------------------------------------

    def _gated(self, handler: Any) -> bool:
        if not self._gate.verify(handler):
            self._gate.reject(handler)
            return False
        return True

    # --- routes --------------------------------------------------------

    @post("/api/indexers/{indexer_id}")
    def handle_indexer_delete_tunnel(
        self, handler: Any, *, indexer_id: str,
    ) -> None:
        """DELETE-via-POST tunnel for an indexer.

        Body must include ``{"_method": "DELETE"}`` to actually
        trigger deletion — preserves the legacy chain's behavior
        where a missing/wrong ``_method`` field falls through to
        a 404. The path itself stays POST per the OpenAPI spec.
        """
        if not self._gated(handler):
            return
        parsed_id, error = self._indexer_id_resolver.parse(indexer_id)
        if error is not None:
            handler._json_response(HTTPStatus.BAD_REQUEST, error)
            return
        assert parsed_id is not None
        body = handler._read_json_body() or {}
        if body.get("_method") != _BODY_METHOD_DELETE:
            handler._json_response(
                HTTPStatus.NOT_FOUND, {"error": "not found"},
            )
            return
        handler._json_response(
            HTTPStatus.OK, self._content.delete_indexer(parsed_id),
        )

    @post("/api/indexers/{indexer_id}/toggle")
    def handle_indexer_toggle(
        self, handler: Any, *, indexer_id: str,
    ) -> None:
        """Enable / disable an indexer.

        Body: ``{enable: bool}`` — defaults to ``True`` (legacy
        chain semantic). Returns the content service's
        ``{status: ok, indexer_id, enable}`` envelope on success.
        """
        if not self._gated(handler):
            return
        parsed_id, error = self._indexer_id_resolver.parse(indexer_id)
        if error is not None:
            handler._json_response(HTTPStatus.BAD_REQUEST, error)
            return
        assert parsed_id is not None
        body = handler._read_json_body() or {}
        enable = bool(body.get("enable", True))
        handler._json_response(
            HTTPStatus.OK,
            self._content.toggle_indexer(parsed_id, enable),
        )

    @post("/api/import-lists/{service}/{list_id}/delete")
    def handle_import_list_delete(
        self, handler: Any, *, service: str, list_id: str,
    ) -> None:
        """Delete an import list on the named arr service."""
        if not self._gated(handler):
            return
        parsed_list_id, error = self._list_id_resolver.parse(list_id)
        if error is not None:
            handler._json_response(HTTPStatus.BAD_REQUEST, error)
            return
        assert parsed_list_id is not None
        handler._json_response(
            HTTPStatus.OK,
            self._content.delete_import_list(service, parsed_list_id),
        )

    @post("/api/import-lists/{service}/{list_id}/toggle")
    def handle_import_list_toggle(
        self, handler: Any, *, service: str, list_id: str,
    ) -> None:
        """Toggle the ``enabled`` flag on an import list.

        Body: ``{enabled: bool}`` — defaults to ``True``. Note
        the body key is ``enabled`` (with an ``-d``) here vs.
        ``enable`` for indexers; preserved verbatim from the
        legacy chain.
        """
        if not self._gated(handler):
            return
        parsed_list_id, error = self._list_id_resolver.parse(list_id)
        if error is not None:
            handler._json_response(HTTPStatus.BAD_REQUEST, error)
            return
        assert parsed_list_id is not None
        body = handler._read_json_body() or {}
        enabled = bool(body.get("enabled", True))
        handler._json_response(
            HTTPStatus.OK,
            self._content.toggle_import_list(
                service, parsed_list_id, enabled,
            ),
        )


__all__ = [
    "ContentService",
    "IndexersImportListsPostRoutes",
    "IntIdResolver",
]
