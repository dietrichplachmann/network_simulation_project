"""Audit completed study coverage, accounting, and deliverable completeness."""

import argparse
import csv
import json
import math
from pathlib import Path


def read(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def validate(root, partial=False):
    manifest = json.loads((root / "manifest.json").read_text())
    expected = {(n, placement, protocol, run) for n in manifest["sizes"]
                for placement in manifest["placements"] for protocol in manifest["protocols"]
                for run in range(1, manifest["runs_per_protocol"] + 1)}
    records = read(root / "combined-summary.csv")
    actual = set()
    for summary in records:
        n, run = int(summary["grid_size"]), int(summary["run"])
        key = (n, summary["gateway"], summary["routing"], run)
        assert key in expected and key not in actual, f"Unexpected or duplicate run: {key}"
        actual.add(key)
        folder = root / f"n{n:02d}_{key[1]}_{key[2]}_run{run:02d}"
        metadata = json.loads((folder / "experiment.json").read_text())
        assert metadata["complete"] and metadata["binary_sha256"] == manifest["binary_sha256"]
        sources, times = read(folder / "per-source.csv"), read(folder / "timeseries.csv")
        nodes, topology = read(folder / "node-timeseries.csv"), read(folder / "topology.csv")
        assert len(sources) == n * n - 1 and len(topology) == n * n + 1
        gateway = 0 if summary["gateway"] == "corner" else (n // 2) * n + n // 2
        assert int(summary["gateway_id"]) == gateway
        assert [int(r["node_id"]) for r in topology if r["role"] == "gateway"] == [gateway]
        assert len([r for r in topology if r["role"] == "server"]) == 1
        assert len(nodes) == n * n * len(times)
        for source in sources:
            tx, rx = int(source["tx_packets"]), int(source["rx_packets"])
            assert tx > 0 and 0 <= rx <= tx
            assert int(source["undelivered_packets"]) == tx - rx
            assert math.isclose(float(source["delivery_ratio"]), rx / tx, abs_tol=1e-8)
        assert int(summary["tx_packets"]) == sum(int(r["tx_packets"]) for r in sources)
        assert int(summary["rx_packets"]) == sum(int(r["rx_packets"]) for r in sources)
        assert int(summary["undelivered_packets"]) == int(summary["tx_packets"]) - int(summary["rx_packets"])
        assert math.isclose(float(summary["gateway_throughput_mbps"]),
                            sum(float(r["throughput_mbps"]) for r in sources), abs_tol=1e-8)
        assert 0 <= float(summary["delivery_ratio"]) <= 1
        assert 0 <= float(summary["uplink_utilization"]) <= 1 + 1e-8
        assert sum(int(r["rx_packets"]) for r in times) == int(summary["rx_packets"])
        assert sum(int(r["uplink_queue_drops"]) for r in times) == int(summary["uplink_queue_drops"])
        duration, drain = float(summary["duration_s"]), float(summary["drain_s"])
        assert math.isclose(sum(float(r["interval_s"]) for r in times), duration + drain, abs_tol=1e-7)
        active_area = sum(float(r["interval_s"]) * float(r["uplink_queue_mean_packets"]) for r in times if r["phase"] == "traffic")
        assert math.isclose(active_area / duration, float(summary["uplink_queue_mean_packets"]), abs_tol=1e-7)
        if summary["jain_fairness"]:
            rates = [float(r["throughput_mbps"]) for r in sources]
            fairness = sum(rates)**2 / (len(rates) * sum(rate**2 for rate in rates))
            assert math.isclose(fairness, float(summary["jain_fairness"]), abs_tol=1e-8)
        if manifest["reports"]:
            for name in ("report.html", "animation.html", "figures/traffic-and-bottlenecks.png",
                         "figures/traffic-and-bottlenecks.svg", "figures/sources-and-queues.png",
                         "figures/sources-and-queues.svg"):
                assert (folder / name).stat().st_size > 100, f"Missing or empty {folder / name}"
            animation = (folder / "animation.html").read_text(encoding="utf-8")
            payload = json.loads(animation.split("const data=", 1)[1].split(";const canvas", 1)[0])
            assert payload["n"] == n and payload["gateway"] == gateway
            assert len(payload["frames"]) == len(times)
            for frame, interval in zip(payload["frames"], times):
                assert len(frame["queue"]) == n * n and len(frame["forward"]) == n * n
                assert math.isclose(frame["t"], float(interval["traffic_time_s"]), abs_tol=1e-8)
                assert math.isclose(frame["wireQueue"], float(interval["uplink_queue_mean_packets"]), abs_tol=1e-8)
    if not partial:
        assert actual == expected, f"Missing {len(expected - actual)} experiments"
        progress = json.loads((root / "progress.json").read_text())
        assert progress["complete"] == len(expected) and not progress["failures"]
        if manifest["reports"]:
            assert (root / "index.html").is_file()
            groups = read(root / "comparisons" / "group-summary.csv")
            assert {(int(r["grid_size"]), r["gateway"], r["routing"]) for r in groups} == {key[:3] for key in expected}
            assert len(groups) == len(expected) // manifest["runs_per_protocol"]
            assert all(int(r["runs"]) == manifest["runs_per_protocol"] for r in groups)
            for name in ("center-comparison", "corner-comparison", "placement-comparison"):
                for extension in ("png", "svg"):
                    assert (root / "comparisons" / f"{name}.{extension}").stat().st_size > 100
    return len(actual), len(expected)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--partial", action="store_true", help="Audit completed runs while the rest are still running")
    args = parser.parse_args()
    complete, expected = validate(args.folder, args.partial)
    print(f"Validated {complete}/{expected} experiments: coverage, packet accounting, queues, time windows, and artifacts.")
