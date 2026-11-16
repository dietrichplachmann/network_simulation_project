"""Run matched center/corner experiments using only the Python standard library."""

import argparse
import csv
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("build/gateway-bottleneck"))
    parser.add_argument("--sizes", type=int, nargs="+", default=[3, 5, 7])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--rate-kbps", type=float, default=128)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--output", type=Path, default=Path("results/sweep"))
    args = parser.parse_args()
    if args.runs < 1 or any(size < 3 or size > 255 or size % 2 == 0 for size in args.sizes):
        parser.error("Use positive runs and odd grid sizes from 3 through 255.")
    binary = args.binary.resolve(strict=True)
    # A fresh root prevents silently mixing old and new experiment results.
    args.output.mkdir(parents=True, exist_ok=False)
    with (args.output / "combined-summary.csv").open("w", newline="") as combined:
        writer = None
        for size in args.sizes:
            for run in range(1, args.runs + 1):
                for gateway in ("center", "corner"):
                    output = args.output / f"grid-{size}" / gateway / f"run-{run}"
                    subprocess.run(
                        [str(binary), f"--gridSize={size}", f"--gateway={gateway}",
                         f"--run={run}", f"--rateKbps={args.rate_kbps}",
                         f"--duration={args.duration}", f"--output={output}"],
                        check=True,
                    )
                    with (output / "summary.csv").open(newline="") as file:
                        reader = csv.DictReader(file)
                        if writer is None:
                            writer = csv.DictWriter(combined, fieldnames=reader.fieldnames)
                            writer.writeheader()
                        writer.writerows(reader)
                        combined.flush()


if __name__ == "__main__":
    main()
