"""MPEG-TS continuity checks against hand-built packets with known faults."""
from streamtest.ts import analyse_ts


def pkt(pid: int, cc: int, payload: bool = True, adaptation: bool = False, discontinuity: bool = False,
        sync: int = 0x47) -> bytes:
    afc = (2 if adaptation else 0) | (1 if payload else 0)
    header = bytes([sync, (pid >> 8) & 0x1F, pid & 0xFF, (afc << 4) | (cc & 0x0F)])
    body = bytes([1, 0x80 if discontinuity else 0x00]) if adaptation else b""
    return (header + body).ljust(188, b"\xff")


def stream(pid: int, ccs: list[int]) -> bytes:
    return b"".join(pkt(pid, c) for c in ccs)


def test_clean_stream_has_no_errors():
    r = analyse_ts(stream(0x100, [i % 16 for i in range(40)]))
    assert r.packets == 40 and r.continuity_errors == 0 and r.sync_byte_errors == 0


def test_missing_packet_is_a_continuity_error():
    r = analyse_ts(stream(0x100, [0, 1, 2, 4, 5]))
    assert r.continuity_errors == 1


def test_counter_wraps_from_15_to_0():
    assert analyse_ts(stream(0x100, [14, 15, 0, 1])).continuity_errors == 0


def test_one_repeated_packet_is_allowed():
    assert analyse_ts(stream(0x100, [3, 4, 4, 5])).continuity_errors == 0


def test_adaptation_only_packets_do_not_advance_the_counter():
    data = pkt(0x100, 5) + pkt(0x100, 5, payload=False, adaptation=True) + pkt(0x100, 6)
    assert analyse_ts(data).continuity_errors == 0


def test_discontinuity_flag_resets_checking():
    data = pkt(0x100, 5) + pkt(0x100, 11, adaptation=True, discontinuity=True) + pkt(0x100, 12)
    assert analyse_ts(data).continuity_errors == 0


def test_each_pid_is_tracked_separately():
    data = b"".join(pkt(0x100, i) + pkt(0x101, 15 - i) for i in range(5))
    assert analyse_ts(data).continuity_errors == 4  # 0x101 counts down, so every step is wrong


def test_null_packets_are_ignored_and_bad_sync_is_counted():
    data = pkt(0x1FFF, 0) + pkt(0x1FFF, 9) + pkt(0x100, 0, sync=0x00)
    r = analyse_ts(data)
    assert r.continuity_errors == 0 and r.sync_byte_errors == 1


def test_pat_is_detected():
    assert analyse_ts(pkt(0x0000, 0)).as_dict()["pat_present"] is True
