"""Check delivery over multiple hops, accounting, and disconnected behavior."""

import csv
import math
from pathlib import Path
import subprocess
import sys
import tempfile


def read_rows(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def check(binary, root, gateway, disconnected=False):
    output = root / (gateway + ("-disconnected" if disconnected else ""))
    command = [str(binary), "--gridSize=3", f"--gateway={gateway}",
               "--warmup=30", "--duration=8", "--drain=5", "--rateKbps=8",
               f"--output={output}"]
    if disconnected:
        command.append("--range=50")
    subprocess.run(command, check=True)
    summary, = read_rows(output / "summary.csv")
    sources = read_rows(output / "per-source.csv")
    topology = read_rows(output / "topology.csv")
    assert len(sources) == 8 and len(topology) == 9
    gateway_id = 4 if gateway == "center" else 0
    assert int(summary["gateway_id"]) == gateway_id
    assert sum(int(row["is_gateway"]) for row in topology) == 1
    assert gateway_id not in {int(row["node_id"]) for row in sources}
    for row in sources:
        tx, rx = int(row["tx_packets"]), int(row["rx_packets"])
        assert tx > 0 and 0 <= rx <= tx
        assert int(row["undelivered_packets"]) == tx - rx
        assert math.isclose(float(row["delivery_ratio"]), rx / tx, abs_tol=1e-9)
        if disconnected:
            assert rx == 0 and row["mean_delay_ms"] == ""
        else:
            # In the corner case node 8 must traverse four cardinal links.
            assert rx > 0, f"Node {row['node_id']} never reached the gateway"
            assert float(row["mean_delay_ms"]) > 0
    assert int(summary["tx_packets"]) == sum(int(row["tx_packets"]) for row in sources)
    assert int(summary["rx_packets"]) == sum(int(row["rx_packets"]) for row in sources)
    throughput = sum(float(row["throughput_mbps"]) for row in sources)
    assert math.isclose(float(summary["gateway_throughput_mbps"]), throughput, abs_tol=1e-9)
    if disconnected:
        assert throughput == 0 and summary["jain_fairness"] == ""
    else:
        assert 0 < throughput <= 8 * 8 / 1000 * 1.15
        assert 1 / 8 <= float(summary["jain_fairness"]) <= 1 + 1e-9
    repeat = subprocess.run(command, capture_output=True, text=True)
    assert repeat.returncode != 0, "Existing results should be protected from overwriting"


def main():
    binary = Path(sys.argv[1]).resolve(strict=True)
    parent = Path(sys.argv[2])
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent) as temporary:
        root = Path(temporary)
        check(binary, root, "center")
        check(binary, root, "corner")
        check(binary, root, "corner", disconnected=True)
        result = subprocess.run([str(binary), "--gridSize=4", "--gateway=center"],
                                capture_output=True, text=True)
        assert result.returncode != 0, "An even grid has no unique center node"
    print("Gateway baseline checks passed.")


if __name__ == "__main__":
    main()
