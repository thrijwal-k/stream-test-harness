"""Probe traffic: numbered, timestamped UDP packets sent at a steady rate, like a constant-bitrate stream.

Each packet carries a sequence number and the time it was sent, so the receiver can work out exactly
what was lost, duplicated, reordered or delayed.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field

MAGIC = b"STH1"
HEADER = struct.Struct("!4sIQ")  # magic, sequence number, send time (monotonic ns)


@dataclass
class Arrival:
    seq: int
    sent_ns: int
    recv_ns: int


@dataclass
class ProbeResult:
    sent: int
    arrivals: list[Arrival] = field(default_factory=list)


def send_probes(port: int, rate_pps: int, duration_s: float, size: int = 188 * 7, host: str = "127.0.0.1") -> int:
    """Send packets at a fixed rate; returns how many were sent."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    padding = b"\x00" * max(0, size - HEADER.size)
    total = int(rate_pps * duration_s)
    start = time.monotonic()
    for seq in range(total):
        target = start + seq / rate_pps
        while (now := time.monotonic()) < target:
            time.sleep(min(target - now, 0.001))
        sock.sendto(HEADER.pack(MAGIC, seq, time.monotonic_ns()) + padding, (host, port))
    sock.close()
    return total


class ProbeReceiver:
    """Collect probe packets on a port in a background thread."""

    def __init__(self, port: int = 0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self._sock.bind(("127.0.0.1", port))
        self._sock.settimeout(0.05)
        self.arrivals: list[Arrival] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def __enter__(self) -> ProbeReceiver:
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self._sock.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                return
            now = time.monotonic_ns()
            if len(data) >= HEADER.size:
                magic, seq, sent = HEADER.unpack_from(data)
                if magic == MAGIC:
                    self.arrivals.append(Arrival(seq, sent, now))
