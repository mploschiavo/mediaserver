"""Jellyfin home-rails orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib import parse

LogFn = Callable[[str], None]
BoolCfgFn = Callable[[dict[str, Any], str, bool], bool]
CoerceListFn = Callable[[Any], list[Any]]
ToIntFn = Callable[[Any, Any], Any]
JellyfinRequestFn = Callable[..., tuple[int, Any, str]]
JellyfinBuildQueryPathFn = Callable[[str, dict[str, Any]], str]
JellyfinItemsFromPayloadFn = Callable[[Any], list[dict[str, Any]]]
NormalizeItemIdsFn = Callable[[list[dict[str, Any]]], list[str]]
ChunkedFn = Callable[[list[str], int], list[list[str]]]
ResolveUserIdFn = Callable[[dict[str, Any], str, str], str]


@dataclass
class JellyfinHomeRailsDependencies:
    log: LogFn
    bool_cfg: BoolCfgFn
    coerce_list: CoerceListFn
    to_int: ToIntFn
    jellyfin_request: JellyfinRequestFn
    jellyfin_build_query_path: JellyfinBuildQueryPathFn
    jellyfin_items_from_payload: JellyfinItemsFromPayloadFn
    normalize_item_ids: NormalizeItemIdsFn
    chunked: ChunkedFn
    resolve_jellyfin_user_id_value: ResolveUserIdFn


@dataclass
class JellyfinHomeRailsService:
    deps: JellyfinHomeRailsDependencies

    def default_rails(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "Trending",
                "path": "/Items",
                "query": {
                    "includeItemTypes": "Movie",
                    "recursive": "true",
                    "sortBy": "PlayCount,DatePlayed",
                    "sortOrder": "Descending",
                },
                "limit": 40,
            },
            {
                "name": "Top Rated",
                "path": "/Items",
                "query": {
                    "includeItemTypes": "Movie",
                    "recursive": "true",
                    "sortBy": "CommunityRating,CriticRating",
                    "sortOrder": "Descending",
                    "minCommunityRating": "7",
                },
                "limit": 40,
            },
            {
                "name": "New This Week",
                "path": "/Items",
                "query": {
                    "includeItemTypes": "Movie",
                    "recursive": "true",
                    "sortBy": "DateCreated,PremiereDate",
                    "sortOrder": "Descending",
                },
                "rolling_premiere_days": 7,
                "limit": 40,
            },
            {
                "name": "Because You Watched",
                "path": "/Items/Suggestions",
                "query": {
                    "mediaType": "Video",
                    "type": "Movie",
                },
                "allowed_item_types": ["Movie"],
                "fallback_query": {
                    "path": "/Items",
                    "query": {
                        "includeItemTypes": "Movie",
                        "recursive": "true",
                        "isPlayed": "true",
                        "sortBy": "DatePlayed,CommunityRating",
                        "sortOrder": "Descending",
                    },
                },
                "limit": 40,
            },
        ]

    def find_collection_by_name(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        user_id: str,
        collection_name: str,
    ) -> str:
        d = self.deps
        path = d.jellyfin_build_query_path(
            "/Items",
            {
                "userId": user_id,
                "includeItemTypes": "BoxSet",
                "recursive": "true",
                "searchTerm": collection_name,
                "limit": "200",
            },
        )
        status, data, body = d.jellyfin_request(jellyfin_url, path, jellyfin_api_key)
        if status != 200:
            raise RuntimeError(
                f"Jellyfin home rails: failed listing collections (HTTP {status}): {body}"
            )
        target = str(collection_name or "").strip().lower()
        for item in d.jellyfin_items_from_payload(data):
            if not isinstance(item, dict):
                continue
            if str(item.get("Name") or "").strip().lower() == target:
                return str(item.get("Id") or "").strip()
        return ""

    def collection_item_ids(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        user_id: str,
        collection_id: str,
    ) -> list[str]:
        d = self.deps
        if not collection_id:
            return []
        path = d.jellyfin_build_query_path(
            "/Items",
            {
                "userId": user_id,
                "parentId": collection_id,
                "recursive": "false",
                "limit": "5000",
            },
        )
        status, data, body = d.jellyfin_request(jellyfin_url, path, jellyfin_api_key)
        if status != 200:
            raise RuntimeError(
                f"Jellyfin home rails: failed listing collection items (HTTP {status}): {body}"
            )
        return d.normalize_item_ids(d.jellyfin_items_from_payload(data))

    def update_collection_items(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        collection_id: str,
        to_add: list[str],
        to_remove: list[str],
    ) -> tuple[int, int]:
        d = self.deps
        added = 0
        removed = 0

        for batch in d.chunked(to_remove, 100):
            path = d.jellyfin_build_query_path(
                f"/Collections/{parse.quote(collection_id, safe='')}/Items",
                {"ids": ",".join(batch)},
            )
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                path,
                jellyfin_api_key,
                method="DELETE",
            )
            if status not in (200, 201, 202, 204):
                raise RuntimeError(
                    f"Jellyfin home rails: failed removing collection items (HTTP {status}): {body}"
                )
            removed += len(batch)

        for batch in d.chunked(to_add, 100):
            path = d.jellyfin_build_query_path(
                f"/Collections/{parse.quote(collection_id, safe='')}/Items",
                {"ids": ",".join(batch)},
            )
            status, _, body = d.jellyfin_request(
                jellyfin_url,
                path,
                jellyfin_api_key,
                method="POST",
            )
            if status not in (200, 201, 202, 204):
                raise RuntimeError(
                    f"Jellyfin home rails: failed adding collection items (HTTP {status}): {body}"
                )
            added += len(batch)

        return added, removed

    def ensure_collection_membership(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        user_id: str,
        collection_name: str,
        desired_ids: list[str],
        *,
        clear_when_empty: bool = False,
    ) -> dict[str, Any]:
        desired_ids = [str(v).strip() for v in desired_ids if str(v).strip()]
        if not desired_ids and not clear_when_empty:
            return {"created": False, "added": 0, "removed": 0}

        collection_id = self.find_collection_by_name(
            jellyfin_url, jellyfin_api_key, user_id, collection_name
        )
        created = False
        if not collection_id:
            create_path = self.deps.jellyfin_build_query_path(
                "/Collections",
                {
                    "name": collection_name,
                    "ids": ",".join(desired_ids) if desired_ids else "",
                },
            )
            status, create_data, body = self.deps.jellyfin_request(
                jellyfin_url,
                create_path,
                jellyfin_api_key,
                method="POST",
            )
            if status not in (200, 201, 202):
                raise RuntimeError(
                    f"Jellyfin home rails: failed creating collection '{collection_name}' "
                    f"(HTTP {status}): {body}"
                )
            created = True
            collection_id = str(
                (create_data or {}).get("Id")
                or (create_data or {}).get("CollectionId")
                or ""
            ).strip()
            if not collection_id:
                collection_id = self.find_collection_by_name(
                    jellyfin_url,
                    jellyfin_api_key,
                    user_id,
                    collection_name,
                )

        if collection_id:
            collection_id_norm = collection_id.lower()
            desired_ids = [item for item in desired_ids if item.lower() != collection_id_norm]

        current_ids = self.collection_item_ids(
            jellyfin_url,
            jellyfin_api_key,
            user_id,
            collection_id,
        )
        current_set = {item.lower() for item in current_ids}
        desired_set = {item.lower() for item in desired_ids}

        to_add = [item for item in desired_ids if item.lower() not in current_set]
        to_remove = [item for item in current_ids if item.lower() not in desired_set]
        added, removed = self.update_collection_items(
            jellyfin_url,
            jellyfin_api_key,
            collection_id,
            to_add,
            to_remove,
        )

        return {"created": created, "added": added, "removed": removed}

    def delete_collection_by_name(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        user_id: str,
        collection_name: str,
    ) -> bool:
        collection_id = self.find_collection_by_name(
            jellyfin_url,
            jellyfin_api_key,
            user_id,
            collection_name,
        )
        if not collection_id:
            return False

        status, _, body = self.deps.jellyfin_request(
            jellyfin_url,
            f"/Items/{parse.quote(collection_id, safe='')}",
            jellyfin_api_key,
            method="DELETE",
        )
        if status not in (200, 202, 204):
            raise RuntimeError(
                f"Jellyfin home rails: failed deleting collection '{collection_name}' "
                f"(HTTP {status}): {body}"
            )
        return True

    def run_rail_query(
        self,
        jellyfin_url: str,
        jellyfin_api_key: str,
        user_id: str,
        rail_cfg: dict[str, Any],
        max_items: int,
    ) -> list[str]:
        d = self.deps

        def split_type_values(raw_value: Any) -> list[str]:
            values: list[str] = []
            for value in d.coerce_list(raw_value):
                text = str(value or "").strip()
                if not text:
                    continue
                if "," in text:
                    values.extend([part.strip() for part in text.split(",") if part.strip()])
                else:
                    values.append(text)
            return values

        path = str(rail_cfg.get("path") or "/Items").strip()
        query = rail_cfg.get("query") if isinstance(rail_cfg.get("query"), dict) else {}
        query = dict(query)
        query.setdefault("userId", user_id)

        limit = d.to_int(rail_cfg.get("limit"), max_items)
        if limit and "limit" not in query:
            query["limit"] = str(limit)

        rolling_days = d.to_int(rail_cfg.get("rolling_premiere_days"))
        if rolling_days and "minPremiereDate" not in query:
            min_premiere = (
                (datetime.now(timezone.utc) - timedelta(days=int(rolling_days)))
                .isoformat()
                .replace("+00:00", "Z")
            )
            query["minPremiereDate"] = min_premiere

        full_path = d.jellyfin_build_query_path(path, query)
        status, data, body = d.jellyfin_request(jellyfin_url, full_path, jellyfin_api_key)
        if status != 200:
            raise RuntimeError(
                f"Jellyfin home rails: failed querying '{rail_cfg.get('name', path)}' "
                f"(HTTP {status}): {body}"
            )

        items = d.jellyfin_items_from_payload(data)
        allowed_types = {
            str(v).strip().lower()
            for v in d.coerce_list(rail_cfg.get("allowed_item_types"))
            if str(v).strip()
        }
        if not allowed_types:
            inferred = split_type_values(query.get("includeItemTypes"))
            inferred.extend(split_type_values(query.get("type")))
            allowed_types = {str(v).strip().lower() for v in inferred if str(v).strip()}
        if allowed_types:
            items = [
                item
                for item in items
                if str((item or {}).get("Type") or "").strip().lower() in allowed_types
            ]

        ids = d.normalize_item_ids(items)
        if ids:
            return ids

        fallback = rail_cfg.get("fallback_query")
        if not isinstance(fallback, dict):
            return []

        fallback_cfg = {
            "name": str(rail_cfg.get("name") or "rail"),
            "path": str(fallback.get("path") or "/Items"),
            "query": fallback.get("query") if isinstance(fallback.get("query"), dict) else {},
            "limit": fallback.get("limit", limit),
            "rolling_premiere_days": fallback.get("rolling_premiere_days"),
            "allowed_item_types": fallback.get("allowed_item_types")
            or rail_cfg.get("allowed_item_types"),
        }
        return self.run_rail_query(
            jellyfin_url,
            jellyfin_api_key,
            user_id,
            fallback_cfg,
            max_items,
        )

    def ensure_home_rails(
        self,
        cfg: dict[str, Any],
        config_root: str,
        wait_timeout: int,
        *,
        normalize_url: Callable[[str], str],
        wait_for_service: Callable[[str, str, str, int], None],
        resolve_jellyfin_api_key: Callable[[dict[str, Any], str], str],
    ) -> None:
        d = self.deps
        rails_cfg = cfg.get("jellyfin_home_rails") or {}
        rails_enabled = d.bool_cfg(rails_cfg, "enabled", False)
        cleanup_when_disabled = d.bool_cfg(rails_cfg, "cleanup_collections_when_disabled", False)
        if not rails_enabled and not cleanup_when_disabled:
            return

        jellyfin_url = normalize_url(rails_cfg.get("url", "http://jellyfin:8096"))
        wait_for_service("Jellyfin", jellyfin_url, "/System/Info/Public", wait_timeout)

        jellyfin_api_key = resolve_jellyfin_api_key(rails_cfg, config_root)
        if not jellyfin_api_key:
            raise RuntimeError(
                "Jellyfin home rails: API key unavailable. Set JELLYFIN_API_KEY or keep "
                "jellyfin_home_rails.auto_discover_api_key_from_db=true."
            )

        user_id = d.resolve_jellyfin_user_id_value(rails_cfg, jellyfin_url, jellyfin_api_key)
        if not user_id:
            raise RuntimeError(
                "Jellyfin home rails: no Jellyfin user id could be resolved. Set JELLYFIN_USER_ID "
                "or keep jellyfin_home_rails.auto_discover_user_id=true."
            )

        if not rails_enabled:
            cleanup_names = [
                str(name or "").strip()
                for name in d.coerce_list(rails_cfg.get("cleanup_collection_names"))
                if str(name or "").strip()
            ]
            if not cleanup_names:
                cleanup_names = [
                    str(item.get("name") or "").strip()
                    for item in self.default_rails()
                    if str(item.get("name") or "").strip()
                ]

            removed = 0
            for name in cleanup_names:
                if self.delete_collection_by_name(jellyfin_url, jellyfin_api_key, user_id, name):
                    removed += 1

            d.log(
                "[OK] Jellyfin home rails: disabled; cleaned up synthetic collections "
                f"(removed={removed}, checked={len(cleanup_names)})"
            )
            return

        rails = d.coerce_list(rails_cfg.get("rails"))
        if not rails:
            rails = self.default_rails()

        max_items = d.to_int(rails_cfg.get("max_items_per_rail"), 40) or 40
        max_items = max(1, max_items)
        processed = 0
        total_items = 0

        for rail in rails:
            if not isinstance(rail, dict):
                continue
            name = str(rail.get("name") or "").strip()
            if not name:
                continue

            item_ids = self.run_rail_query(jellyfin_url, jellyfin_api_key, user_id, rail, max_items)
            if not item_ids:
                d.log(
                    f"[WARN] Jellyfin home rails: no items matched for '{name}'. "
                    "Leaving existing collection unchanged."
                )
                continue

            result = self.ensure_collection_membership(
                jellyfin_url,
                jellyfin_api_key,
                user_id,
                name,
                item_ids,
                clear_when_empty=d.bool_cfg(rail, "clear_when_empty", False),
            )
            processed += 1
            total_items += len(item_ids)
            d.log(
                f"[OK] Jellyfin home rails: reconciled '{name}' "
                f"(items={len(item_ids)}, created={result['created']}, "
                f"added={result['added']}, removed={result['removed']})"
            )

        d.log(
            "[OK] Jellyfin home rails: reconcile complete "
            f"(rails={processed}, total_items={total_items})"
        )
