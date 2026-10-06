"""Metric calculations and the impairment proxy, end to end over loopback."""
import time

import pytest

from streamtest.impair import Impairment, ImpairmentProxy
from streamtest.metrics import network_metrics, percentile, rfc3550_jitter_ms
from streamtest.probe import Arrival, ProbeReceiver, send_probes
from streamtest.runner import check


def arrivals(pairs):
    """Build arrivals from (seq, sent_ms, recv_ms)."""
    return [Arrival(s, int(a * 1e6), int(b * 1e6)) for s, a, b in pairs]


def test_constant_delay_has_zero_jitter():
    a = arrivals([(i, i * 2, i * 2 + 30) for i in range(50)])
    assert rfc3550_jitter_ms(a) == pytest.approx(0)


def test_jitter_follows_rfc3550_smoothing():
    # one packet arrives 16 ms late relative to the previous spacing: J = 16/16 = 1 ms after that step
    a = arrivals([(0, 0, 10), (1, 2, 28)])
    assert rfc3550_jitter_ms(a) == pytest.approx(1.0)


def test_loss_duplicates_and_reordering_are_counted():
    a = arrivals([(0, 0, 1), (2, 4, 5), (1, 2, 6), (2, 4, 7), (4, 8, 9)])
    m = network_metrics(5, a)
    assert m["lost"] == 1  # seq 3
    assert m["duplicates"] == 1  # seq 2 twice
    assert m["reordered"] == 1  # only seq 1 arrives after a higher number (2); a repeated 2 is a duplicate
    assert m["latency_ms_p50"] == pytest.approx(1.0)


def test_percentile_interpolates():
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)


def test_pass_criteria():
    assert check(5, {"min": 1, "max": 6}) and not check(7, {"max": 6}) and not check(0, {"min": 1})


def _run(imp: Impairment, pps=500, secs=1.0):
    with ProbeReceiver() as rx, ImpairmentProxy(0, rx.port, imp) as proxy:
        sent = send_probes(proxy.port, pps, secs)
        proxy.stop()
        time.sleep(0.1)
        return network_metrics(sent, rx.arrivals), proxy


def test_clean_link_delivers_everything():
    m, _ = _run(Impairment())
    assert m["lost"] == 0 and m["duplicates"] == 0 and m["reordered"] == 0


def test_measured_loss_matches_what_the_proxy_dropped():
    m, proxy = _run(Impairment(loss=0.05, seed=42))
    assert proxy.dropped > 0
    assert m["lost"] == proxy.dropped


def test_same_seed_drops_the_same_number_of_packets():
    _, p1 = _run(Impairment(loss=0.05, seed=99))
    _, p2 = _run(Impairment(loss=0.05, seed=99))
    assert p1.dropped == p2.dropped


def test_added_delay_shows_up_in_latency():
    m, _ = _run(Impairment(delay_ms=30))
    assert 29 <= m["latency_ms_p50"] <= 36
