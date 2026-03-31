"""Profile default resolution for rebuild/bootstrap."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RebuildProfileDefaultsResult:
    include_optional: str
    enable_unpackerr: str
    run_bootstrap: str


@dataclass
class RebuildProfileDefaultsService:
    def apply(
        self,
        *,
        profile: str,
        include_optional: str,
        enable_unpackerr: str,
        run_bootstrap: str,
    ) -> RebuildProfileDefaultsResult:
        if profile == "minimal":
            return RebuildProfileDefaultsResult(
                include_optional=include_optional or "0",
                enable_unpackerr=enable_unpackerr or "0",
                run_bootstrap=run_bootstrap or "1",
            )
        if profile == "full":
            return RebuildProfileDefaultsResult(
                include_optional=include_optional or "1",
                enable_unpackerr=enable_unpackerr or "1",
                run_bootstrap=run_bootstrap or "1",
            )
        if profile == "public-demo":
            return RebuildProfileDefaultsResult(
                include_optional=include_optional or "1",
                enable_unpackerr=enable_unpackerr or "0",
                run_bootstrap=run_bootstrap or "0",
            )
        if profile == "power-user":
            return RebuildProfileDefaultsResult(
                include_optional=include_optional or "1",
                enable_unpackerr=enable_unpackerr or "1",
                run_bootstrap=run_bootstrap or "1",
            )
        raise RuntimeError(f"Unsupported profile: {profile}")
