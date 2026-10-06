"""Network quality metrics computed from probe arrivals."""
from __future__ import annotations

from collections.abc import Sequence

from .probe import Arrival


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def rfc3550_jitter_ms(arrivals: Sequence[Arrival]) -> float:
    """Interarrival jitter as defined in RFC 3550 (RTP), in milliseconds, processed in arrival order."""
    jitter = 0.0
    for prev, cur in zip(arrivals, arrivals[1:], strict=False):
        d = (cur.recv_ns - prev.recv_ns) - (cur.sent_ns - prev.sent_ns)
        jitter += (abs(d) / 1e6 - jitter) / 16
    return jitter


def network_metrics(sent: int, arrivals: Sequence[Arrival]) -> dict:
    unique = {a.seq for a in arrivals}
    duplicates = len(arrivals) - len(unique)
    reordered = 0
    highest = -1
    for a in arrivals:
        if a.seq < highest:
            reordered += 1
        highest = max(highest, a.seq)
    latency = [(a.recv_ns - a.sent_ns) / 1e6 for a in arrivals]
    lost = max(0, sent - len(unique))
    return {
        "sent": sent,
        "received": len(arrivals),
        "lost": lost,
        "loss_pct": 100 * lost / sent if sent else 0.0,
        "duplicates": duplicates,
        "duplicate_pct": 100 * duplicates / sent if sent else 0.0,
        "reordered": reordered,
        "reordered_pct": 100 * reordered / sent if sent else 0.0,
        "latency_ms_p50": percentile(latency, 50),
        "latency_ms_p95": percentile(latency, 95),
        "latency_ms_p99": percentile(latency, 99),
        "latency_ms_max": max(latency, default=0.0),
        "jitter_ms": rfc3550_jitter_ms(arrivals),
    }
