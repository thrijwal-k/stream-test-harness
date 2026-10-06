# Stream Test Harness

[![CI](https://github.com/thrijwal-k/stream-test-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/thrijwal-k/stream-test-harness/actions/workflows/ci.yml)

An automated test harness for **live video over IP**. It streams a broadcast-style test signal across a deliberately degraded network, measures what arrives, and checks every measurement against a written test plan.

Live broadcast over IP is unforgiving. A few lost packets in a live feed can mean a frozen picture on air, so broadcasters monitor every stream for packet loss, delay, jitter and transport-stream errors. This project automates that kind of testing:

1. **Degrade the link on purpose**: drop, delay, jitter, reorder or duplicate packets with a repeatable random seed.
2. **Send real traffic**: numbered probe packets at a steady rate, plus a real-time H.264/AAC MPEG transport stream from ffmpeg (1280x720, 25 fps, 1 kHz tone), sent as standard 1316-byte UDP datagrams.
3. **Measure what arrived**: loss, duplicates, reordering, latency percentiles, RFC 3550 jitter, MPEG-TS continuity errors (ETSI TR 101 290 priority 1) and decoded frames.
4. **Check against the test plan**: each scenario in `scenarios.yaml` says what the measurements must be. The run passes only if every check passes.

```
 sender ──UDP──▶ impairment proxy ──UDP──▶ receiver ──▶ metrics ──▶ pass/fail vs test plan
 (probes or     (loss, delay, jitter,     (captures in     (network + TS + decoded frames)
  ffmpeg TS)     reorder, duplicate)       arrival order)
```

## Results

Every push runs the full test plan in GitHub Actions and publishes this table to the run summary.

| Scenario | Link | Check | Expected | Measured | Result |
|---|---|---|---|---|---|
| clean_link | clean link | loss_pct | <= 0 | 0.00 | PASS |
|  |  | latency_ms_p99 | <= 5 | 0.94 | PASS |
|  |  | continuity_errors | <= 0 | 0 | PASS |
|  |  | frames_decoded_pct | >= 99 | 100.00 | PASS |
| packet_loss_2pct | 2.0% loss | loss_pct | 1.2 to 2.8 | 1.70 | PASS |
|  |  | continuity_errors | >= 1 | 78 | PASS |
| delay_and_jitter | 40 ms delay, +/-10 ms jitter | latency_ms_p50 | 35 to 46 | 40.39 | PASS |
|  |  | jitter_ms | >= 1 | 7.40 | PASS |
| reordering_5pct | 5.0% reordered | reordered_pct | 3.5 to 6.5 | 5.05 | PASS |
| duplication_1pct | 1.0% duplicated | duplicate_pct | 0.4 to 1.6 | 0.70 | PASS |
| video_under_light_loss | 0.5% loss | continuity_errors | >= 1 | 42 | PASS |
|  |  | frames_decoded_pct | >= 80 | 99.50 | PASS |

(Abridged. The full report has every check.)

**What the results show:**
- The harness **measures each impairment accurately**: injected 40 ms delay is measured as 40.4 ms median, and injected 5% reordering is measured as 5.05%.
- **The video-under-loss scenario shows why transport-stream monitoring matters.** At 0.5% packet loss, 99.5% of frames still decode, so a viewer might not notice. Continuity checking still flags 42 errors, so engineers know the link is damaged before it becomes visible on air.

## Testing the tester

A test harness is only useful if its own measurements are right, so the proxy and metrics have their own unit tests: hand-built transport-stream packets with known faults, the RFC 3550 jitter formula, and seeded runs that must drop identical packets.

During development, the clean-link scenario reported **1 lost packet out of 1,500**, which should be impossible. Investigation showed it was always the **last** packet: the proxy could shut down after taking a packet off its queue but before sending it. The fix tracks packets still in flight, not just the queue. The clean link now delivers every packet on every run, and a regression test guards it.

## Test plan (`scenarios.yaml`)

| Scenario | What it simulates | What must be true |
|---|---|---|
| `clean_link` | Perfect network | Nothing lost, reordered or corrupted; all frames decode |
| `packet_loss_2pct` | Random 2% loss | Measured loss 1.2–2.8%; TS monitoring detects it |
| `delay_and_jitter` | Congested internet path | Median latency 35–46 ms; jitter measured |
| `reordering_5pct` | Packets taking different routes | Reordering 3.5–6.5%, no loss |
| `duplication_1pct` | Duplicated packets | Duplicates 0.4–1.6%, no loss |
| `video_under_light_loss` | Subtle damage on a live feed | Monitoring catches it even if the picture survives |

Add a scenario by adding a block to the YAML. No code changes are needed.

## Run it

Needs Python 3.10+ and ffmpeg.

```bash
pip install -r requirements.txt
python -m pytest -q                                   # 18 unit and loopback tests
PYTHONPATH=src python -m streamtest.runner            # full test plan, about 45 seconds
PYTHONPATH=src python -m streamtest.runner --only clean_link
```

Reports are written to `reports/report.md` and `reports/report.json`.

## How it works

| File | Purpose |
|---|---|
| `src/streamtest/impair.py` | UDP impairment proxy: seeded loss, delay, jitter, reordering and duplication |
| `src/streamtest/probe.py` | Constant-rate probe sender with sequence numbers and timestamps, plus receiver |
| `src/streamtest/metrics.py` | Loss, duplicates, reordering, latency percentiles, RFC 3550 interarrival jitter |
| `src/streamtest/ts.py` | MPEG-TS checks: sync byte, per-PID continuity counters, PAT presence |
| `src/streamtest/video.py` | Streams a live ffmpeg test signal and checks decoded frames and decoder errors |
| `src/streamtest/runner.py` | Runs the test plan, applies pass criteria, writes the reports |
| `.github/workflows/ci.yml` | Lint and unit tests on Python 3.10 and 3.12, then the full test plan with ffmpeg |

## Next steps

- SRT and RTP transports, and multicast.
- Kernel-level impairment with `tc netem` as an alternative to the Python proxy.
- More TR 101 290 checks, such as PCR accuracy and PAT/PMT timing.
- Burst loss (Gilbert-Elliott model), which is closer to real network failures than random loss.
