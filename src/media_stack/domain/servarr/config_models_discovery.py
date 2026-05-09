"""Typed models for Arr discovery list contracts and provider options."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class DiscoveryListContract:
    implementation: str
    provider: str
    requires_auth: bool = False
    required_override_fields: tuple[str, ...] = field(default_factory=tuple)
    optional_override_fields: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TmdbPopularImportOptions:
    tmdb_list_type: int | None = None


@dataclass(frozen=True)
class LastFmTagOptions:
    tag_id: str = ""
    count: int | None = None


@dataclass(frozen=True)
class GoodreadsListImportOptions:
    list_id: str = ""


@dataclass(frozen=True)
class TraktPopularImportOptions:
    access_token: str = ""
    refresh_token: str = ""
    list_type: str = ""


@dataclass(frozen=True)
class GenericDiscoveryProviderOptions:
    values: dict[str, Any] = field(default_factory=dict)


DiscoveryProviderOptions = Union[
    TmdbPopularImportOptions,
    LastFmTagOptions,
    GoodreadsListImportOptions,
    TraktPopularImportOptions,
    GenericDiscoveryProviderOptions,
]


_DISCOVERY_CONTRACTS: dict[str, DiscoveryListContract] = {
    "tmdbpopularimport": DiscoveryListContract(
        implementation="TMDbPopularImport",
        provider="tmdb",
        required_override_fields=("tMDbListType",),
        optional_override_fields=(),
    ),
    "lastfmtag": DiscoveryListContract(
        implementation="LastFmTag",
        provider="lastfm",
        required_override_fields=("tagId",),
        optional_override_fields=("count",),
    ),
    "goodreadslistimportlist": DiscoveryListContract(
        implementation="GoodreadsListImportList",
        provider="goodreads",
        required_override_fields=("listId",),
        optional_override_fields=(),
    ),
    "traktpopularimport": DiscoveryListContract(
        implementation="TraktPopularImport",
        provider="trakt",
        requires_auth=True,
        required_override_fields=(),
        optional_override_fields=("listType", "accessToken", "refreshToken"),
    ),
}


class ServarrConfigModelsDiscovery:
    def _to_int(self, value: Any) -> int | None:
        try:
            if value is None or str(value).strip() == "":
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_str(self, value: Any) -> str:
        return str(value or "").strip()

    def resolve_discovery_list_contract(self, implementation: str) -> DiscoveryListContract:
        token = str(implementation or "").strip().lower()
        if token in _DISCOVERY_CONTRACTS:
            return _DISCOVERY_CONTRACTS[token]
        return DiscoveryListContract(
            implementation=str(implementation or "").strip() or "unknown",
            provider="generic",
        )

    def parse_discovery_provider_options(
        self,
        contract: DiscoveryListContract,
        field_overrides: dict[str, Any],
    ) -> DiscoveryProviderOptions:
        provider = contract.provider
        if provider == "tmdb":
            return TmdbPopularImportOptions(
                tmdb_list_type=self._to_int(field_overrides.get("tMDbListType")),
            )
        if provider == "lastfm":
            return LastFmTagOptions(
                tag_id=self._to_str(field_overrides.get("tagId")),
                count=self._to_int(field_overrides.get("count")),
            )
        if provider == "goodreads":
            return GoodreadsListImportOptions(
                list_id=self._to_str(field_overrides.get("listId")),
            )
        if provider == "trakt":
            return TraktPopularImportOptions(
                access_token=self._to_str(field_overrides.get("accessToken")),
                refresh_token=self._to_str(field_overrides.get("refreshToken")),
                list_type=self._to_str(field_overrides.get("listType")),
            )
        return GenericDiscoveryProviderOptions(values=dict(field_overrides))


_INSTANCE = ServarrConfigModelsDiscovery()
_to_int = _INSTANCE._to_int
_to_str = _INSTANCE._to_str
resolve_discovery_list_contract = _INSTANCE.resolve_discovery_list_contract
parse_discovery_provider_options = _INSTANCE.parse_discovery_provider_options
