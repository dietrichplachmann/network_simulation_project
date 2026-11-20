"""Validate all routing protocols across the mesh and actual external link."""

import csv
import math
from pathlib import Path
import subprocess
import sys
import tempfile


def rows(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def check(binary, root, protocol="OLSR", n=3, placement="corner", disconnected=False,
          bottleneck=False):
    output = root / f"{protocol}-{n}-{placement}-{disconnected}-{bottleneck}"
    command = [str(binary), f"--gridSize={n}", f"--gateway={placement}",
               f"--routing={protocol}", "--warmup=60", "--duration=8", "--drain=15",
               f"--rateKbps={256 if bottleneck else 8}", f"--output={output}"]
    if disconnected:
        command.append("--range=50")
    if bottleneck:
        command.extend(["--uplinkMbps=0.05", "--uplinkQueuePackets=5"])
    subprocess.run(command, check=True)
    summary, = rows(output / "summary.csv")
    sources = rows(output / "per-source.csv")
    topology = rows(output / "topology.csv")
    times = rows(output / "timeseries.csv")
    node_times = rows(output / "node-timeseries.csv")
    gateway = 0 if placement == "corner" else (n // 2) * n + n // 2
    assert len(sources) == n * n - 1 and len(topology) == n * n + 1
    assert int(summary["gateway_id"]) == gateway
    assert topology[-1]["role"] == "server" and topology[-1]["ip"] == summary["server_ip"]
    assert summary["routing"] == protocol
    assert len(node_times) == len(times) * n * n
    assert math.isclose(sum(float(r["interval_s"]) for r in times), 23, abs_tol=1e-8)
    assert math.isclose(sum(float(r["interval_s"]) for r in times if r["phase"] == "traffic"), 8, abs_tol=1e-8)
    for source in sources:
        tx, rx = int(source["tx_packets"]), int(source["rx_packets"])
        assert tx > 0 and 0 <= rx <= tx
        assert int(source["undelivered_packets"]) == tx - rx
        assert math.isclose(float(source["delivery_ratio"]), rx / tx, abs_tol=1e-9)
        if disconnected:
            assert rx == 0 and source["mean_delay_ms"] == ""
        elif not bottleneck:
            assert rx > 0, f"{protocol}: source {source['node_id']} never reached the external server"
            assert float(source["mean_delay_ms"]) >= 5
    assert int(summary["tx_packets"]) == sum(int(r["tx_packets"]) for r in sources)
    assert int(summary["rx_packets"]) == sum(int(r["rx_packets"]) for r in sources)
    throughput = sum(float(r["throughput_mbps"]) for r in sources)
    assert math.isclose(float(summary["gateway_throughput_mbps"]), throughput, abs_tol=1e-9)
    assert 0 <= float(summary["uplink_utilization"]) <= 1 + 1e-8
    capacity = int(summary["uplink_queue_capacity"])
    assert 0 <= float(summary["uplink_queue_mean_packets"]) <= capacity
    assert int(summary["uplink_queue_peak_packets"]) <= capacity
    assert sum(int(r["uplink_queue_drops"]) for r in times) == int(summary["uplink_queue_drops"])
    assert all(0 <= float(r["wifi_queue_mean_packets"]) <= int(summary["wifi_queue_capacity"]) for r in node_times)
    if bottleneck:
        assert int(summary["uplink_queue_drops"]) > 0
        assert float(summary["uplink_utilization"]) > .75
        assert 0 < throughput <= .05 * 1.05
    elif disconnected:
        assert throughput == 0 and summary["jain_fairness"] == ""
    else:
        assert throughput > 0
        assert 1 / (n * n - 1) <= float(summary["jain_fairness"]) <= 1 + 1e-9
    repeated = subprocess.run(command, capture_output=True, text=True)
    assert repeated.returncode != 0


def main():
    binary = Path(sys.argv[1]).resolve(strict=True)
    parent = Path(sys.argv[2])
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as temporary:
        root = Path(temporary)
        for protocol in ("AODV", "OLSR", "DSDV"):
            check(binary, root, protocol=protocol)
            check(binary, root, protocol=protocol, n=2, placement="center")
        check(binary, root, disconnected=True)
        check(binary, root, n=2, bottleneck=True)
        invalid = subprocess.run([str(binary), "--routing=INVALID"], capture_output=True)
        assert invalid.returncode != 0
    print("All protocols, external delivery, even centers, accounting, and bottleneck checks passed.")


if __name__ == "__main__":
    main()
