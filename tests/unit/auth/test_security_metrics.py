"""Unit tests for ``media_stack.core.observability.security_metrics``.

Covers label canonicalisation, escaping, counter/gauge/histogram
semantics, thread-safety under contention, registry idempotence and
conflict detection, and the Prometheus text-format rendering.
"""

from __future__ import annotations

import threading

import pytest

from media_stack.core.observability.security_metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricLabels,
    MetricRegistry,
    MetricsError,
    _reset_default_registry_for_tests,
    default_registry,
    get_counter,
    get_gauge,
    get_histogram,
    render_default,
)


# ---------------------------------------------------------------------------
# MetricLabels
# ---------------------------------------------------------------------------


def test_metric_labels_equal_regardless_of_insertion_order():
    a = MetricLabels.from_kwargs(provider="authelia", kind="user")
    b = MetricLabels.from_kwargs(kind="user", provider="authelia")
    assert a == b
    assert hash(a) == hash(b)


def test_metric_labels_as_prom_sorted_and_wrapped():
    lbls = MetricLabels.from_kwargs(provider="authelia", kind="user")
    # Sorted by key, so kind comes before provider
    assert lbls.as_prom() == '{kind="user",provider="authelia"}'


def test_metric_labels_empty_as_prom_is_empty_string():
    assert MetricLabels().as_prom() == ""


def test_metric_labels_escape_backslash_newline_quote():
    lbls = MetricLabels.from_kwargs(msg='a\\b\nc"d')
    # backslash -> \\, newline -> \n, double-quote -> \"
    assert lbls.as_prom() == '{msg="a\\\\b\\nc\\"d"}'


def test_metric_labels_keys_round_trip():
    lbls = MetricLabels.from_kwargs(z="1", a="2")
    assert lbls.keys() == ("a", "z")


# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------


def test_counter_inc_default_amount_is_one():
    c = Counter("logins", "total logins", ("provider",))
    c.inc(provider="authelia")
    assert c.value(provider="authelia") == 1.0


def test_counter_inc_custom_amount():
    c = Counter("logins", "total logins", ("provider",))
    c.inc(2.5, provider="authelia")
    c.inc(0.5, provider="authelia")
    assert c.value(provider="authelia") == 3.0


def test_counter_separate_labelsets_are_independent():
    c = Counter("logins", "total", ("provider",))
    c.inc(provider="authelia")
    c.inc(3, provider="local")
    assert c.value(provider="authelia") == 1.0
    assert c.value(provider="local") == 3.0
    assert len(c.labelsets()) == 2


def test_counter_unknown_label_rejected():
    c = Counter("logins", "total", ("provider",))
    with pytest.raises(MetricsError):
        c.inc(reason="bad_password")


def test_counter_missing_label_rejected():
    c = Counter("logins", "total", ("provider",))
    with pytest.raises(MetricsError):
        c.inc()


def test_counter_negative_amount_rejected():
    c = Counter("logins", "total", ())
    with pytest.raises(MetricsError):
        c.inc(-1)


def test_counter_no_labels_metric_works():
    c = Counter("events", "total", ())
    c.inc()
    c.inc(4)
    assert c.value() == 5.0


# ---------------------------------------------------------------------------
# Gauge
# ---------------------------------------------------------------------------


def test_gauge_set_inc_dec():
    g = Gauge("active", "active sessions", ("provider",))
    g.set(5, provider="authelia")
    g.inc(provider="authelia")
    g.dec(2, provider="authelia")
    assert g.value(provider="authelia") == 4.0


def test_gauge_negative_value_allowed():
    g = Gauge("delta", "signed delta", ())
    g.set(-3.5)
    assert g.value() == -3.5
    g.dec(1)
    assert g.value() == -4.5


def test_gauge_dec_default_amount():
    g = Gauge("x", "h", ())
    g.set(10)
    g.dec()
    assert g.value() == 9.0


def test_gauge_labelsets_tracks_keys():
    g = Gauge("x", "h", ("k",))
    g.set(1, k="a")
    g.inc(k="b")
    assert len(g.labelsets()) == 2


# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------


def test_histogram_observe_falls_into_correct_buckets():
    h = Histogram("lat", "latency", (), buckets=(0.1, 0.5, 1.0))
    h.observe(0.05)  # hits all three buckets
    h.observe(0.3)   # hits 0.5 and 1.0
    h.observe(0.8)   # hits 1.0
    h.observe(2.0)   # hits nothing below +Inf
    rows = h.buckets()
    assert rows == [(0.1, 1), (0.5, 2), (1.0, 3)]


def test_histogram_sum_and_count():
    h = Histogram("lat", "latency", (), buckets=(1.0,))
    h.observe(0.25)
    h.observe(0.75)
    h.observe(3.0)
    assert h.sum() == pytest.approx(4.0)
    assert h.count() == 3


def test_histogram_default_buckets():
    h = Histogram("lat", "latency", ())
    assert h.bucket_bounds[0] == 0.005
    assert h.bucket_bounds[-1] == 10


def test_histogram_labels_independent():
    h = Histogram("lat", "latency", ("route",), buckets=(1.0,))
    h.observe(0.5, route="a")
    h.observe(2.0, route="b")
    assert h.count(route="a") == 1
    assert h.count(route="b") == 1
    assert h.buckets(route="a") == [(1.0, 1)]
    assert h.buckets(route="b") == [(1.0, 0)]


def test_histogram_empty_labelset_returns_zero_rows():
    h = Histogram("lat", "latency", (), buckets=(1.0,))
    assert h.buckets() == [(1.0, 0)]
    assert h.count() == 0
    assert h.sum() == 0.0


def test_histogram_rejects_empty_buckets():
    with pytest.raises(MetricsError):
        Histogram("lat", "latency", (), buckets=())


def test_histogram_rejects_unsorted_buckets():
    with pytest.raises(MetricsError):
        Histogram("lat", "latency", (), buckets=(1.0, 0.5))


# ---------------------------------------------------------------------------
# Thread-safety
# ---------------------------------------------------------------------------


def _hammer(fn, *args, threads=10, iters=1000):
    def worker():
        for _ in range(iters):
            fn(*args)
    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()


def test_counter_thread_safety():
    c = Counter("hits", "h", ())
    _hammer(c.inc)
    assert c.value() == 10000.0


def test_gauge_thread_safety():
    g = Gauge("depth", "h", ())
    _hammer(g.inc)
    assert g.value() == 10000.0


def test_histogram_thread_safety():
    h = Histogram("lat", "h", (), buckets=(1.0,))
    _hammer(lambda: h.observe(0.5))
    assert h.count() == 10000
    assert h.buckets() == [(1.0, 10000)]


# ---------------------------------------------------------------------------
# Registry + render
# ---------------------------------------------------------------------------


def test_registry_render_includes_help_and_type_lines():
    r = MetricRegistry()
    c = r.counter("logins_total", "total logins", ("provider",))
    c.inc(provider="authelia")
    c.inc(2, provider="local")
    out = r.render()
    assert "# HELP logins_total total logins" in out
    assert "# TYPE logins_total counter" in out
    assert 'logins_total{provider="authelia"} 1' in out
    assert 'logins_total{provider="local"} 2' in out
    assert out.endswith("\n")


def test_registry_render_gauge():
    r = MetricRegistry()
    g = r.gauge("active", "active sessions", ("provider",))
    g.set(3, provider="authelia")
    out = r.render()
    assert "# TYPE active gauge" in out
    assert 'active{provider="authelia"} 3' in out


def test_registry_render_histogram_includes_bucket_sum_count_and_plus_inf():
    r = MetricRegistry()
    h = r.histogram("lat", "latency", (), buckets=(0.1, 1.0))
    h.observe(0.05)
    h.observe(0.5)
    h.observe(5.0)
    out = r.render()
    assert "# TYPE lat histogram" in out
    assert 'lat_bucket{le="0.1"} 1' in out
    assert 'lat_bucket{le="1"} 2' in out
    assert 'lat_bucket{le="+Inf"} 3' in out
    assert "lat_sum " in out
    assert "lat_count 3" in out


def test_registry_render_histogram_with_labels_merges_le():
    r = MetricRegistry()
    h = r.histogram("lat", "latency", ("route",), buckets=(1.0,))
    h.observe(0.5, route="a")
    out = r.render()
    # both route and le must appear, sorted by key -> le comes before route
    assert 'lat_bucket{le="1",route="a"} 1' in out
    assert 'lat_bucket{le="+Inf",route="a"} 1' in out
    assert 'lat_sum{route="a"}' in out
    assert 'lat_count{route="a"} 1' in out


def test_registry_reregister_same_config_is_idempotent():
    r = MetricRegistry()
    a = r.counter("x", "help", ("k",))
    b = r.counter("x", "help", ("k",))
    assert a is b


def test_registry_reregister_different_type_raises():
    r = MetricRegistry()
    r.counter("x", "h", ())
    with pytest.raises(MetricsError):
        r.gauge("x", "h", ())


def test_registry_reregister_different_labels_raises():
    r = MetricRegistry()
    r.counter("x", "h", ("a",))
    with pytest.raises(MetricsError):
        r.counter("x", "h", ("b",))


def test_registry_reregister_histogram_different_buckets_raises():
    r = MetricRegistry()
    r.histogram("x", "h", (), buckets=(1.0,))
    with pytest.raises(MetricsError):
        r.histogram("x", "h", (), buckets=(2.0,))


def test_registry_get_missing_raises():
    r = MetricRegistry()
    with pytest.raises(MetricsError):
        r.get("no_such_metric")


def test_registry_get_returns_registered():
    r = MetricRegistry()
    c = r.counter("x", "h", ())
    assert r.get("x") is c


def test_empty_registry_renders_empty_string():
    r = MetricRegistry()
    assert r.render() == ""


def test_render_preserves_unicode_label_values():
    r = MetricRegistry()
    c = r.counter("x", "h", ("who",))
    c.inc(who="åéîõü☃")
    out = r.render()
    assert 'who="åéîõü☃"' in out


def test_render_escapes_special_chars_in_label_values():
    r = MetricRegistry()
    c = r.counter("x", "h", ("msg",))
    c.inc(msg='a\\b\nc"d')
    out = r.render()
    assert 'msg="a\\\\b\\nc\\"d"' in out


def test_render_integer_vs_float_formatting():
    r = MetricRegistry()
    g = r.gauge("g", "h", ())
    g.set(1)
    assert " 1\n" in r.render()
    g.set(1.5)
    assert " 1.5\n" in r.render()


# ---------------------------------------------------------------------------
# Default registry wrappers
# ---------------------------------------------------------------------------


def test_default_registry_wrappers(monkeypatch):
    _reset_default_registry_for_tests()
    c = get_counter("c1", "help", ())
    g = get_gauge("g1", "help", ())
    h = get_histogram("h1", "help", (), buckets=(1.0,))
    c.inc()
    g.set(2)
    h.observe(0.5)
    out = render_default()
    assert "c1 1" in out
    assert "g1 2" in out
    assert 'h1_bucket{le="1"} 1' in out
    # default_registry() returns the same shared instance wrappers use
    assert default_registry().get("c1") is c
    _reset_default_registry_for_tests()
    # After reset it's empty again
    assert render_default() == ""
