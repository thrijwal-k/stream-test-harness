"""Live video test: stream a broadcast-style test signal over UDP and check what arrives.

ffmpeg generates a moving test pattern with a 1 kHz tone, encodes it as H.264/AAC in an MPEG
transport stream, and sends it in real time as 7 TS packets per UDP datagram (the standard 1316
bytes). A Python receiver captures the datagrams, then the capture is checked at two levels:
transport-stream continuity (what broadcast monitoring sees) and decoded frames (what viewers see).
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .ts import analyse_ts


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class UdpCapture:
    """Capture raw UDP datagrams in arrival order until stopped."""

    def __init__(self, port: int = 0):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
        self._sock.bind(("127.0.0.1", port))
        self._sock.settimeout(0.05)
        self.chunks: list[bytes] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def __enter__(self) -> UdpCapture:
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
            self.chunks.append(data)


def stream_test_signal(port: int, duration_s: float, fps: int = 25, size: str = "1280x720") -> None:
    """Send a real-time H.264/AAC MPEG-TS test signal to 127.0.0.1:port."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-re",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}",
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
        "-t", str(duration_s),
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", str(fps), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-f", "mpegts", f"udp://127.0.0.1:{port}?pkt_size=1316",
    ]
    subprocess.run(cmd, check=True, timeout=duration_s + 30)


def decoded_frames(path: Path) -> tuple[int, int]:
    """Return (decoded video frames, decoder error lines) for a captured stream."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    try:
        frames = int(json.loads(probe.stdout or "{}").get("streams", [{}])[0].get("nb_read_frames", 0))
    except (ValueError, IndexError):
        frames = 0
    decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                            capture_output=True, text=True, timeout=120)
    errors = len([line for line in decode.stderr.splitlines() if line.strip()])
    return frames, errors


def video_metrics(chunks: list[bytes], duration_s: float, fps: int) -> dict:
    data = b"".join(chunks)
    ts = analyse_ts(data).as_dict()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "capture.ts"
        path.write_bytes(data)
        frames, errors = decoded_frames(path) if data else (0, 0)
    expected = int(duration_s * fps)
    return {
        "datagrams": len(chunks),
        **ts,
        "frames_expected": expected,
        "frames_decoded": frames,
        "frames_decoded_pct": 100 * frames / expected if expected else 0.0,
        "decoder_errors": errors,
    }


def run_video(dest_port: int, capture: UdpCapture, duration_s: float, fps: int = 25) -> dict:
    stream_test_signal(dest_port, duration_s, fps)
    time.sleep(0.5)  # let the last datagrams arrive
    return video_metrics(list(capture.chunks), duration_s, fps)
