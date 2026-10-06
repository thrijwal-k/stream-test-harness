"""Run a test plan of network scenarios and check every measurement against its pass criteria.

Usage:
    python -m streamtest.runner                       # run scenarios.yaml
    python -m streamtest.runner --plan my_plan.yaml   # run a different plan
    python -m streamtest.runner --only clean_link     # run one scenario
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from .impair import Impairment, ImpairmentProxy
from .metrics import network_metrics
from .probe import ProbeReceiver, send_probes
from .video import UdpCapture, ffmpeg_available, run_video


def check(value: float, rule: dict) -> bool:
    if "min" in rule and value < rule["min"]:
        return False
    return not ("max" in rule and value > rule["max"])


def run_scenario(sc: dict) -> dict:
    imp = Impairment(**sc.get("impairment", {}))
    result: dict = {"name": sc["name"], "description": sc.get("description", ""), "link": imp.describe()}
    measured: dict = {}

    probe = sc.get("probe")
    if probe:
        with ProbeReceiver() as rx, ImpairmentProxy(0, rx.port, imp) as proxy:
            sent = send_probes(proxy.port, probe["rate_pps"], probe["duration_s"])
            proxy.stop()
            time.sleep(0.1)
            measured.update(network_metrics(sent, rx.arrivals))

    video = sc.get("video")
    if video:
        if not ffmpeg_available():
            result["skipped"] = "ffmpeg not installed"
        else:
            with UdpCapture() as cap, ImpairmentProxy(0, cap.port, imp) as proxy:
                v = run_video(proxy.port, cap, video["duration_s"], video.get("fps", 25))
            measured.update(v)

    checks = []
    for metric, rule in sc.get("expect", {}).items():
        if metric not in measured:
            checks.append({"metric": metric, "rule": rule, "value": None, "passed": bool(result.get("skipped"))})
            continue
        val = measured[metric]
        checks.append({"metric": metric, "rule": rule, "value": val, "passed": check(val, rule)})
    result.update(measured=measured, checks=checks, passed=all(c["passed"] for c in checks))
    return result


def fmt_rule(rule: dict) -> str:
    if "min" in rule and "max" in rule:
        return f"{rule['min']} to {rule['max']}"
    return f">= {rule['min']}" if "min" in rule else f"<= {rule['max']}"


def to_markdown(results: list[dict]) -> str:
    passed = sum(r["passed"] for r in results)
    lines = [f"# Stream test report\n\n**{passed}/{len(results)} scenarios passed**\n",
             "| Scenario | Link | Check | Expected | Measured | Result |", "|---|---|---|---|---|---|"]
    for r in results:
        for i, c in enumerate(r["checks"]):
            v = c["value"]
            val = "n/a" if v is None else (f"{v:.2f}" if isinstance(v, float) else v)
            lines.append(f"| {r['name'] if i == 0 else ''} | {r['link'] if i == 0 else ''} | {c['metric']} | "
                         f"{fmt_rule(c['rule'])} | {val} | {'PASS' if c['passed'] else '**FAIL**'} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="scenarios.yaml")
    ap.add_argument("--only")
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()
    scenarios = yaml.safe_load(Path(args.plan).read_text())["scenarios"]
    if args.only:
        scenarios = [s for s in scenarios if s["name"] == args.only]
    results = []
    for sc in scenarios:
        t0 = time.monotonic()
        r = run_scenario(sc)
        r["seconds"] = round(time.monotonic() - t0, 1)
        results.append(r)
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['name']:<28} {r['link']:<45} ({r['seconds']}s)")
        for c in r["checks"]:
            if not c["passed"]:
                print(f"      {c['metric']} = {c['value']} (expected {fmt_rule(c['rule'])})")
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "report.json").write_text(json.dumps(results, indent=2, default=float))
    (out / "report.md").write_text(to_markdown(results))
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} scenarios passed. Report: {out}/report.md")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
