"""Generic technology lifecycle contract for bootstrap orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

PhaseFn = Callable[[Any, "TechnologyLifecycleState"], Any]
StatusFn = Callable[[Any, "TechnologyLifecycleState"], dict[str, Any] | str | None]


@dataclass
class TechnologyLifecycleState:
    key: str
    loaded: bool = False
    prechecked: bool = False
    prepared: bool = False
    configured: bool = False
    ensured: bool = False
    hygiene_cleaned: bool = False
    status: str = "pending"
    details: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class TechnologyLifecycle:
    key: str
    load_fn: PhaseFn | None = None
    precheck_fn: PhaseFn | None = None
    prepare_fn: PhaseFn | None = None
    configure_fn: PhaseFn | None = None
    ensure_fn: PhaseFn | None = None
    status_fn: StatusFn | None = None
    clean_hygiene_fn: PhaseFn | None = None
    state: TechnologyLifecycleState = field(init=False)

    def __post_init__(self) -> None:
        self.state = TechnologyLifecycleState(key=self.key)

    def _run_phase(self, label: str, fn: PhaseFn | None, runtime: Any) -> Any:
        if fn is None:
            return None
        try:
            return fn(runtime, self.state)
        except Exception as exc:
            self.state.status = "error"
            self.state.errors.append(f"{label}:{exc}")
            raise

    def load(self, runtime: Any) -> None:
        self._run_phase("load", self.load_fn, runtime)
        self.state.loaded = True

    def precheck(self, runtime: Any) -> None:
        self._run_phase("precheck", self.precheck_fn, runtime)
        self.state.prechecked = True

    def prepare(self, runtime: Any) -> None:
        self._run_phase("prepare", self.prepare_fn, runtime)
        self.state.prepared = True

    def configure(self, runtime: Any) -> None:
        self._run_phase("configure", self.configure_fn, runtime)
        self.state.configured = True

    def ensure(self, runtime: Any) -> None:
        self._run_phase("ensure", self.ensure_fn, runtime)
        self.state.ensured = True

    def status_check(self, runtime: Any) -> None:
        payload = None
        if self.status_fn is not None:
            payload = self.status_fn(runtime, self.state)
        if isinstance(payload, dict):
            self.state.details.update(payload)
        elif isinstance(payload, str) and payload.strip():
            self.state.details["message"] = payload.strip()
        if self.state.status != "error":
            self.state.status = "ok"

    def clean_hygiene(self, runtime: Any) -> None:
        self._run_phase("clean_hygiene", self.clean_hygiene_fn, runtime)
        self.state.hygiene_cleaned = True


@dataclass
class TechnologyLifecycleManager:
    lifecycles: dict[str, TechnologyLifecycle]

    def run_phase(self, phase: str, runtime: Any, keys: list[str] | tuple[str, ...] | None = None) -> None:
        target_keys = list(keys) if keys else list(self.lifecycles.keys())
        for key in target_keys:
            lifecycle = self.lifecycles.get(key)
            if not lifecycle:
                continue
            if phase == "load":
                lifecycle.load(runtime)
            elif phase == "precheck":
                lifecycle.precheck(runtime)
            elif phase == "prepare":
                lifecycle.prepare(runtime)
            elif phase == "configure":
                lifecycle.configure(runtime)
            elif phase == "ensure":
                lifecycle.ensure(runtime)
            elif phase == "status":
                lifecycle.status_check(runtime)
            elif phase == "clean_hygiene":
                lifecycle.clean_hygiene(runtime)
            else:
                raise ValueError(f"Unknown lifecycle phase: {phase}")

    def state(self, key: str) -> TechnologyLifecycleState | None:
        lifecycle = self.lifecycles.get(key)
        if lifecycle is None:
            return None
        return lifecycle.state

    def summary(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for key, lifecycle in self.lifecycles.items():
            s = lifecycle.state
            out[key] = {
                "loaded": s.loaded,
                "prechecked": s.prechecked,
                "prepared": s.prepared,
                "configured": s.configured,
                "ensured": s.ensured,
                "hygiene_cleaned": s.hygiene_cleaned,
                "status": s.status,
                "errors": list(s.errors),
                "details": dict(s.details),
            }
        return out
