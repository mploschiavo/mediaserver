"""Profile default resolution for deploy/bootstrap."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeployProfileDefaultsResult:
    include_optional: str
    enable_components: str
    run_bootstrap: str


@dataclass
class DeployProfileDefaultsService:
    def apply(
        self,
        *,
        profile: str,
        include_optional: str,
        enable_components: str,
        run_bootstrap: str,
    ) -> DeployProfileDefaultsResult:
        if profile == "minimal":
            return DeployProfileDefaultsResult(
                include_optional=include_optional or "0",
                enable_components=enable_components or "0",
                run_bootstrap=run_bootstrap or "1",
            )
        if profile == "full":
            return DeployProfileDefaultsResult(
                include_optional=include_optional or "1",
                enable_components=enable_components or "1",
                run_bootstrap=run_bootstrap or "1",
            )
        if profile == "public-demo":
            return DeployProfileDefaultsResult(
                include_optional=include_optional or "1",
                enable_components=enable_components or "0",
                run_bootstrap=run_bootstrap or "0",
            )
        if profile == "power-user":
            return DeployProfileDefaultsResult(
                include_optional=include_optional or "1",
                enable_components=enable_components or "1",
                run_bootstrap=run_bootstrap or "1",
            )
        raise RuntimeError(f"Unsupported profile: {profile}")
