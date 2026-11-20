"""Exercise the actual batch runner, folder structure, and resume safeguards."""

import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    binary = Path(sys.argv[1]).resolve(strict=True)
    parent = Path(sys.argv[2])
    parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_sweep.py"
    with tempfile.TemporaryDirectory(dir=parent) as temporary:
        root = Path(temporary) / "study"
        command = [sys.executable, str(script), "--binary", str(binary), "--sizes", "2",
                   "--runs", "1", "--duration", "2", "--workers", "2", "--no-reports",
                   "--output", str(root)]
        subprocess.run(command, check=True)
        with (root / "combined-summary.csv").open(newline="") as file:
            records = list(csv.DictReader(file))
        assert len(records) == 6
        assert {(r["gateway"], r["routing"]) for r in records} == {
            (p, routing) for p in ("center", "corner") for routing in ("AODV", "OLSR", "DSDV")}
        assert json.loads((root / "progress.json").read_text())["failures"] == []
        folders = [p for p in root.iterdir() if p.is_dir()]
        assert len(folders) == 6 and all(not p.name.startswith(".") for p in folders)
        stamps = {folder.name: (folder / "summary.csv").stat().st_mtime_ns for folder in folders}
        for folder in folders:
            manifest = json.loads((folder / "experiment.json").read_text())
            assert manifest["complete"] and manifest["binary_sha256"]
            assert all((folder / name).is_file() for name in
                       ("summary.csv", "per-source.csv", "timeseries.csv", "node-timeseries.csv", "topology.csv", "simulation.log"))
        subprocess.run([*command, "--resume"], check=True)
        assert stamps == {folder.name: (folder / "summary.csv").stat().st_mtime_ns for folder in folders}
        mismatch = subprocess.run([*command, "--resume", "--uplink-mbps", "2"], capture_output=True)
        assert mismatch.returncode != 0
        overwrite = subprocess.run(command, capture_output=True)
        assert overwrite.returncode != 0
    print("Batch matrix, experiment files, resume, and overwrite checks passed.")


if __name__ == "__main__":
    main()
