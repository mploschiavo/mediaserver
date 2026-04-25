"""Lightweight Prometheus metrics for the session-visibility feature.

A dependency-free counter/gauge/histogram registry plus a Prometheus
text-format 0.0.4 exposition renderer. We don't pull ``prometheus_client``
as a dep — the feature's surface is small enough that ~200 LOC of
stdlib buys full control over the format without another pinned package.

Public surface:
  * ``MetricLabels``   — frozen, order-independent label set with
    Prometheus-style value escaping.
  * ``Counter``        — monotonic increment, thread-safe.
  * ``Gauge``          — set / inc / dec, thread-safe, negatives ok.
  * ``Histogram``      — cumulative buckets + sum + count, thread-safe.
  * ``MetricRegistry`` — name-keyed collection + ``render()``.
  * Module-level default registry and ``get_counter`` / ``get_gauge``
    / ``get_histogram`` / ``render_default`` convenience wrappers.

The module is deliberately NOT wired into anything yet — the metric
names owned by the session-visibility feature live in the sibling
``security_metrics_contract`` module.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Sequence


class MetricsError(Exception):
    """Raised for registry-invariant violations: duplicate name with
    incompatible type/labels/buckets, label mismatch on observe, etc."""


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def _escape(value: str) -> str:
    """Escape backslash, newline and double-quote per Prom text format.
    Backslash first so we don't double-escape our replacements."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


@dataclass(frozen=True)
class MetricLabels:
    """Frozen, order-independent label-set.

    ``pairs`` is stored pre-sorted by key so two MetricLabels with the
    same keys+values are equal and hash identically regardless of
    insertion order (equality/hash come from ``@dataclass(frozen=True)``).
    """

    pairs: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @classmethod
    def from_kwargs(cls, **labels: str) -> "MetricLabels":
        """Build from kwargs, coercing both keys and values to str."""
        return cls(tuple(sorted((str(k), str(v)) for k, v in labels.items())))

    def as_prom(self) -> str:
        """Render as ``{k1="v1",k2="v2"}``, or ``""`` when empty."""
        if not self.pairs:
            return ""
        body = ",".join(f'{k}="{_escape(v)}"' for k, v in self.pairs)
        return "{" + body + "}"

    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.pairs)


def _validate(declared: tuple[str, ...], supplied: dict[str, str]) -> MetricLabels:
    """Require supplied keys == declared keys; raise otherwise."""
    if not declared and not supplied:
        return MetricLabels()
    ds, ss = set(declared), set(supplied.keys())
    if ds != ss:
        raise MetricsError(
            f"label mismatch: declared={sorted(ds)} supplied={sorted(ss)} "
            f"missing={sorted(ds - ss)} extra={sorted(ss - ds)}"
        )
    return MetricLabels.from_kwargs(**supplied)


# ---------------------------------------------------------------------------
# Metric types
# ---------------------------------------------------------------------------


class _MetricBase:
    """Shared plumbing: name, help text, declared label names, lock."""

    kind: str = "untyped"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names: tuple[str, ...] = tuple(label_names)
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str]) -> MetricLabels:
        return _validate(self.label_names, labels)


class Counter(_MetricBase):
    """Monotonic-increment counter. ``inc(amount)`` requires amount >= 0."""

    kind = "counter"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, label_names)
        self._values: dict[MetricLabels, float] = {}

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise MetricsError(f"counter {self.name!r} cannot decrease (amount={amount})")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + float(amount)

    def value(self, **labels: str) -> float:
        key = self._key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def labelsets(self) -> list[MetricLabels]:
        with self._lock:
            return list(self._values.keys())


class Gauge(_MetricBase):
    """Set / inc / dec gauge. Negatives are permitted."""

    kind = "gauge"

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> None:
        super().__init__(name, help_text, label_names)
        self._values: dict[MetricLabels, float] = {}

    def set(self, v: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(v)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + float(amount)

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) - float(amount)

    def value(self, **labels: str) -> float:
        key = self._key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def labelsets(self) -> list[MetricLabels]:
        with self._lock:
            return list(self._values.keys())


_DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10,
)


class Histogram(_MetricBase):
    """Cumulative-bucket histogram + sum + count.

    An observation of ``v`` is counted in every bucket whose upper-bound
    ``le`` is ``>= v``. ``+Inf`` is implicit and rendered by the
    registry; it always equals the full count.
    """

    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] | None = None,
    ) -> None:
        super().__init__(name, help_text, label_names)
        bs = tuple(float(b) for b in (buckets if buckets is not None else _DEFAULT_BUCKETS))
        if not bs:
            raise MetricsError(f"histogram {name!r} must have at least one bucket")
        if list(bs) != sorted(bs):
            raise MetricsError(f"histogram {name!r} buckets must be ascending: {bs}")
        self.bucket_bounds: tuple[float, ...] = bs
        self._counts: dict[MetricLabels, list[int]] = {}
        self._sums: dict[MetricLabels, float] = {}
        self._totals: dict[MetricLabels, int] = {}

    def _row(self, key: MetricLabels) -> list[int]:
        row = self._counts.get(key)
        if row is None:
            row = [0] * len(self.bucket_bounds)
            self._counts[key] = row
            self._sums[key] = 0.0
            self._totals[key] = 0
        return row

    def observe(self, v: float, **labels: str) -> None:
        key = self._key(labels)
        fv = float(v)
        with self._lock:
            row = self._row(key)
            for i, bound in enumerate(self.bucket_bounds):
                if fv <= bound:
                    row[i] += 1
            self._sums[key] = self._sums.get(key, 0.0) + fv
            self._totals[key] = self._totals.get(key, 0) + 1

    def buckets(self, **labels: str) -> list[tuple[float, int]]:
        key = self._key(labels)
        with self._lock:
            row = list(self._counts.get(key, [0] * len(self.bucket_bounds)))
        return list(zip(self.bucket_bounds, row))

    def sum(self, **labels: str) -> float:
        key = self._key(labels)
        with self._lock:
            return self._sums.get(key, 0.0)

    def count(self, **labels: str) -> int:
        key = self._key(labels)
        with self._lock:
            return self._totals.get(key, 0)

    def labelsets(self) -> list[MetricLabels]:
        with self._lock:
            return list(self._counts.keys())


# ---------------------------------------------------------------------------
# Registry + rendering
# ---------------------------------------------------------------------------


def _fmt(v: float) -> str:
    """Render a number Prom-style: integers plain, floats via ``repr``."""
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        return str(int(v))
    return repr(float(v))


def _merged(base: MetricLabels, extra: tuple[tuple[str, str], ...]) -> str:
    """``{...}`` suffix merging a base labelset with extras (e.g. ``le``)."""
    combined = sorted(list(base.pairs) + list(extra), key=lambda kv: kv[0])
    if not combined:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in combined) + "}"


class MetricRegistry:
    """Name-keyed metric collection.

    Re-registration with the same name, type, label-names (and buckets,
    for histograms) is idempotent and returns the existing metric. Any
    mismatch raises ``MetricsError``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: dict[str, _MetricBase] = {}

    def _register(self, m: _MetricBase) -> _MetricBase:
        with self._lock:
            existing = self._metrics.get(m.name)
            if existing is None:
                self._metrics[m.name] = m
                return m
            if type(existing) is not type(m):
                raise MetricsError(
                    f"metric {m.name!r} already registered as {existing.kind}, "
                    f"cannot re-register as {m.kind}"
                )
            if existing.label_names != m.label_names:
                raise MetricsError(
                    f"metric {m.name!r} label_names mismatch: "
                    f"existing={existing.label_names} new={m.label_names}"
                )
            if isinstance(existing, Histogram) and isinstance(m, Histogram):
                if existing.bucket_bounds != m.bucket_bounds:
                    raise MetricsError(f"metric {m.name!r} histogram buckets mismatch")
            return existing

    def counter(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> Counter:
        got = self._register(Counter(name, help_text, label_names))
        assert isinstance(got, Counter)
        return got

    def gauge(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> Gauge:
        got = self._register(Gauge(name, help_text, label_names))
        assert isinstance(got, Gauge)
        return got

    def histogram(
        self,
        name: str,
        help_text: str,
        label_names: Sequence[str] = (),
        buckets: Sequence[float] | None = None,
    ) -> Histogram:
        got = self._register(Histogram(name, help_text, label_names, buckets))
        assert isinstance(got, Histogram)
        return got

    def get(self, name: str) -> _MetricBase:
        with self._lock:
            m = self._metrics.get(name)
        if m is None:
            raise MetricsError(f"metric {name!r} not registered")
        return m

    def render(self) -> str:
        """Render all metrics as Prometheus text-format 0.0.4.

        Each family emits ``# HELP`` then ``# TYPE`` then one sample per
        labelset. Histograms emit ``_bucket`` lines (with explicit
        ``+Inf``) plus ``_sum`` and ``_count``. Empty registry -> ``""``.
        """
        with self._lock:
            metrics = list(self._metrics.values())
        out: list[str] = []
        for m in metrics:
            out.append(f"# HELP {m.name} {m.help_text}")
            out.append(f"# TYPE {m.name} {m.kind}")
            if isinstance(m, (Counter, Gauge)):
                for lbls in sorted(m.labelsets(), key=lambda ls: ls.pairs):
                    kw = dict(lbls.pairs)
                    out.append(f"{m.name}{lbls.as_prom()} {_fmt(m.value(**kw))}")
            elif isinstance(m, Histogram):
                for lbls in sorted(m.labelsets(), key=lambda ls: ls.pairs):
                    kw = dict(lbls.pairs)
                    for bound, cnt in m.buckets(**kw):
                        out.append(f"{m.name}_bucket{_merged(lbls, (('le', _fmt(bound)),))} {cnt}")
                    total = m.count(**kw)
                    out.append(f"{m.name}_bucket{_merged(lbls, (('le', '+Inf'),))} {total}")
                    out.append(f"{m.name}_sum{lbls.as_prom()} {_fmt(m.sum(**kw))}")
                    out.append(f"{m.name}_count{lbls.as_prom()} {total}")
        return "\n".join(out) + "\n" if out else ""


# ---------------------------------------------------------------------------
# Module-level default registry + convenience wrappers
# ---------------------------------------------------------------------------


_default_registry = MetricRegistry()


def default_registry() -> MetricRegistry:
    """Return the process-wide default registry."""
    return _default_registry


def get_counter(name: str, help_text: str, label_names: Sequence[str] = ()) -> Counter:
    return _default_registry.counter(name, help_text, label_names)


def get_gauge(name: str, help_text: str, label_names: Sequence[str] = ()) -> Gauge:
    return _default_registry.gauge(name, help_text, label_names)


def get_histogram(
    name: str,
    help_text: str,
    label_names: Sequence[str] = (),
    buckets: Sequence[float] | None = None,
) -> Histogram:
    return _default_registry.histogram(name, help_text, label_names, buckets)


def render_default() -> str:
    return _default_registry.render()


def _reset_default_registry_for_tests() -> None:
    """Test-only: wipe the default registry. Not part of the public API."""
    global _default_registry
    _default_registry = MetricRegistry()
