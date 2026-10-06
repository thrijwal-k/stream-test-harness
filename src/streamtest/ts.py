"""MPEG transport stream (TS) checks, based on the first-priority checks in ETSI TR 101 290.

Broadcasters monitor these on every live feed: a lost UDP packet carrying video shows up as a
continuity counter error long before anyone sees a glitch on screen.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TS_PACKET = 188
SYNC_BYTE = 0x47
NULL_PID = 0x1FFF
PAT_PID = 0x0000


@dataclass
class TsReport:
    packets: int = 0
    sync_byte_errors: int = 0
    continuity_errors: int = 0
    pat_packets: int = 0
    pids: dict[int, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ts_packets": self.packets,
            "sync_byte_errors": self.sync_byte_errors,
            "continuity_errors": self.continuity_errors,
            "pat_present": self.pat_packets > 0,
            "pids": len(self.pids),
        }


def analyse_ts(data: bytes) -> TsReport:
    """Check sync bytes and per-PID continuity counters across a captured transport stream."""
    report = TsReport()
    last_cc: dict[int, int] = {}
    for off in range(0, len(data) - TS_PACKET + 1, TS_PACKET):
        pkt = data[off:off + TS_PACKET]
        report.packets += 1
        if pkt[0] != SYNC_BYTE:
            report.sync_byte_errors += 1
            continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        report.pids[pid] = report.pids.get(pid, 0) + 1
        if pid == PAT_PID:
            report.pat_packets += 1
        if pid == NULL_PID:
            continue
        afc = (pkt[3] >> 4) & 0x3
        cc = pkt[3] & 0x0F
        has_payload = afc in (1, 3)
        discontinuity = afc in (2, 3) and pkt[4] > 0 and bool(pkt[5] & 0x80)
        if not has_payload:
            continue  # the counter only advances on packets that carry payload
        if pid in last_cc and not discontinuity:
            expected = (last_cc[pid] + 1) % 16
            if cc != expected and cc != last_cc[pid]:  # one repeated packet is allowed by the standard
                report.continuity_errors += 1
        last_cc[pid] = cc
    return report
