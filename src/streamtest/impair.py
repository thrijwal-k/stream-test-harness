"""UDP impairment proxy: sits between a sender and a receiver and degrades the link on purpose.

It can drop, delay, add jitter to, reorder and duplicate packets. A fixed random seed makes every
run repeatable, which is what lets the tests assert on exact behaviour.
"""
from __future__ import annotations

import heapq
import random
import socket
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Impairment:
    loss: float = 0.0  # probability a packet is dropped (0-1)
    delay_ms: float = 0.0  # fixed one-way delay added to every packet
    jitter_ms: float = 0.0  # extra delay drawn uniformly from +/- jitter_ms (never below zero total)
    reorder: float = 0.0  # probability a packet is held back by reorder_delay_ms
    reorder_delay_ms: float = 20.0
    duplicate: float = 0.0  # probability a packet is sent twice
    seed: int = 1234

    def describe(self) -> str:
        parts = []
        if self.loss:
            parts.append(f"{self.loss:.1%} loss")
        if self.delay_ms:
            parts.append(f"{self.delay_ms:g} ms delay")
        if self.jitter_ms:
            parts.append(f"+/-{self.jitter_ms:g} ms jitter")
        if self.reorder:
            parts.append(f"{self.reorder:.1%} reordered")
        if self.duplicate:
            parts.append(f"{self.duplicate:.1%} duplicated")
        return ", ".join(parts) or "clean link"


class ImpairmentProxy:
    """Forward UDP datagrams from listen_port to (dest_host, dest_port) through an impaired link."""

    def __init__(self, listen_port: int, dest_port: int, impairment: Impairment, dest_host: str = "127.0.0.1"):
        self.imp = impairment
        self.dest = (dest_host, dest_port)
        self._rng = random.Random(impairment.seed)
        self._rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        self._rx.bind(("127.0.0.1", listen_port))
        self._rx.settimeout(0.05)
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._queue: list[tuple[float, int, bytes]] = []
        self._lock = threading.Condition()
        self._counter = 0
        self._in_flight = 0  # scheduled but not yet sent; stop() waits for this to reach zero
        self._stop = threading.Event()
        self.received = self.dropped = self.duplicated = self.reordered = 0
        self._threads = [threading.Thread(target=self._recv_loop, daemon=True),
                         threading.Thread(target=self._send_loop, daemon=True)]

    @property
    def port(self) -> int:
        return self._rx.getsockname()[1]

    def __enter__(self) -> ImpairmentProxy:
        for t in self._threads:
            t.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def stop(self) -> None:
        # give the receive loop a moment to read the last datagrams, then let everything in flight leave
        time.sleep(0.05)
        deadline = time.monotonic() + (self.imp.delay_ms + self.imp.jitter_ms + self.imp.reorder_delay_ms) / 1000 + 0.2
        while time.monotonic() < deadline:
            with self._lock:
                if self._in_flight == 0:
                    break
            time.sleep(0.01)
        self._stop.set()
        with self._lock:
            self._lock.notify_all()
        for t in self._threads:
            t.join(timeout=1)
        self._rx.close()
        self._tx.close()

    def _schedule(self, data: bytes, now: float) -> None:
        imp = self.imp
        delay = imp.delay_ms
        if imp.jitter_ms:
            delay = max(0.0, delay + self._rng.uniform(-imp.jitter_ms, imp.jitter_ms))
        if imp.reorder and self._rng.random() < imp.reorder:
            delay += imp.reorder_delay_ms
            self.reordered += 1
        with self._lock:
            heapq.heappush(self._queue, (now + delay / 1000, self._counter, data))
            self._counter += 1
            self._in_flight += 1
            self._lock.notify()

    def _recv_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, _ = self._rx.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            now = time.monotonic()
            self.received += 1
            if self.imp.loss and self._rng.random() < self.imp.loss:
                self.dropped += 1
                continue
            self._schedule(data, now)
            if self.imp.duplicate and self._rng.random() < self.imp.duplicate:
                self.duplicated += 1
                self._schedule(data, now)

    def _send_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                while not self._queue and not self._stop.is_set():
                    self._lock.wait(timeout=0.05)
                if self._stop.is_set():
                    return
                release, _, data = self._queue[0]
                wait = release - time.monotonic()
                if wait > 0:
                    self._lock.wait(timeout=min(wait, 0.005))
                    continue
                heapq.heappop(self._queue)
            try:
                self._tx.sendto(data, self.dest)
            except OSError:
                return
            finally:
                with self._lock:
                    self._in_flight -= 1
