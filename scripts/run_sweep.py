"""Run the 162-case gateway study, with resumable results and measured animations."""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def experiment_name(n, placement, protocol, run):
    return f"n{n:02d}_{placement}_{protocol}_run{run:02d}"


def run_experiment(job):
    name = experiment_name(job["n"], job["placement"], job["protocol"], job["run"])
    root = Path(job["root"])
    destination = root / name
    # Only complete, validated runs are renamed into their final folder.
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
    raw = staging / "raw"
    command = [job["binary"], f"--gridSize={job['n']}", f"--gateway={job['placement']}",
               f"--routing={job['protocol']}", f"--run={job['run']}",
               *[f"--{key}={value}" for key, value in job["parameters"].items()], f"--output={raw}"]
    started = time.monotonic()
    try:
        with (staging / "simulation.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        for file in raw.iterdir():
            file.rename(staging / file.name)
        raw.rmdir()
        with (staging / "summary.csv").open(newline="") as file:
            summary, = list(csv.DictReader(file))
        if job["reports"]:
            from report_results import render_experiment
            render_experiment(staging)
        (staging / "experiment.json").write_text(json.dumps(
            {"name": name, "command": command, "parameters": job["parameters"],
             "binary_sha256": job["binary_sha256"], "elapsed_seconds": time.monotonic() - started,
             "complete": True}, indent=2), encoding="utf-8")
        staging.rename(destination)
        return name, summary
    except Exception as error:
        raise RuntimeError(f"{name} failed; diagnostic files retained in {staging}: {error}") from error


def write_combined(root, summaries):
    temporary = root / ".combined-summary.csv.tmp"
    with temporary.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(sorted(summaries, key=lambda r:(int(r["grid_size"]), r["gateway"], r["routing"], int(r["run"]))))
    temporary.replace(root / "combined-summary.csv")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("build/gateway-bottleneck"))
    parser.add_argument("--sizes", type=int, nargs="+", default=list(range(2, 11)))
    parser.add_argument("--protocols", nargs="+", choices=["AODV", "OLSR", "DSDV"], default=["AODV", "OLSR", "DSDV"])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--rate-kbps", type=float, default=128)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--warmup", type=float, default=60)
    parser.add_argument("--drain", type=float, default=15)
    parser.add_argument("--uplink-mbps", type=float, default=1)
    parser.add_argument("--uplink-delay-ms", type=float, default=5)
    parser.add_argument("--uplink-queue-packets", type=int, default=100)
    parser.add_argument("--wifi-queue-packets", type=int, default=100)
    parser.add_argument("--sample-interval", type=float, default=1)
    parser.add_argument("--spacing", type=float, default=100)
    parser.add_argument("--range", type=float, default=0)
    parser.add_argument("--packet-size", type=int, default=1024)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", type=Path, default=Path("results/gateway-study"))
    parser.add_argument("--resume", action="store_true", help="Continue a matching existing study without rerunning complete experiments")
    parser.add_argument("--no-reports", action="store_true", help="Collect raw data only")
    args = parser.parse_args()
    positive = [args.rate_kbps, args.duration, args.warmup, args.drain, args.uplink_mbps,
                args.sample_interval, args.spacing]
    if (args.runs < 1 or args.workers < 1 or not args.sizes or
            any(n < 2 or n > 255 for n in args.sizes) or len(set(args.sizes)) != len(args.sizes) or
            len(set(args.protocols)) != len(args.protocols) or
            any(not math.isfinite(value) or value <= 0 for value in positive) or
            not math.isfinite(args.uplink_delay_ms) or args.uplink_delay_ms < 0 or
            not math.isfinite(args.range) or args.range < 0 or args.uplink_queue_packets < 1 or
            args.wifi_queue_packets < 1 or not 12 <= args.packet_size <= 1472 or
            not 1 <= args.seed < 2147483647):
        parser.error("Use valid positive parameters, unique grid sizes >= 2, and unique protocols.")
    if not args.no_reports:
        try:
            import report_results  # Check dependencies before starting any simulation.
        except ImportError:
            parser.error("Plotting dependency missing. Install requirements.txt with this Python interpreter.")
    binary = args.binary.resolve(strict=True)
    if not os.access(binary, os.X_OK):
        parser.error(f"Binary is not executable: {binary}")
    binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
    root = args.output.resolve()
    parameters = {"rateKbps": args.rate_kbps, "duration": args.duration, "warmup": args.warmup,
                  "drain": args.drain, "uplinkMbps": args.uplink_mbps, "uplinkDelayMs": args.uplink_delay_ms,
                  "uplinkQueuePackets": args.uplink_queue_packets, "wifiQueuePackets": args.wifi_queue_packets,
                  "sampleInterval": args.sample_interval, "spacing": args.spacing, "range": args.range,
                  "packetSize": args.packet_size, "seed": args.seed}
    manifest = {"schema": 1, "ns3_version": "3.45", "binary_sha256": binary_hash,
                "sizes": args.sizes, "placements": ["center", "corner"],
                "protocols": args.protocols, "runs_per_protocol": args.runs,
                "parameters": parameters, "reports": not args.no_reports,
                "center_rule": "nearest existing grid node; lower-right tie",
                "external_server_routing": "same selected protocol over point-to-point link",
                "node_heatmap": "time-weighted Wi-Fi MAC queue occupancy per interval"}
    if root.exists():
        if not args.resume:
            parser.error("Output already exists; use a new root or --resume.")
        if not (root / "manifest.json").is_file() or json.loads((root / "manifest.json").read_text()) != manifest:
            parser.error("Resume parameters or simulator binary differ from this study's manifest.")
    else:
        if args.resume:
            parser.error("The requested study does not exist.")
        root.mkdir(parents=True)
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    jobs, summaries = [], []
    for n in args.sizes:
        for placement in ("center", "corner"):
            for protocol in args.protocols:
                for run in range(1, args.runs + 1):
                    name = experiment_name(n, placement, protocol, run)
                    existing = root / name
                    if existing.exists():
                        if not (existing / "experiment.json").is_file() or not json.loads((existing / "experiment.json").read_text()).get("complete"):
                            parser.error(f"Unrecognized/incomplete folder: {existing}; preserve it and use a fresh output root.")
                        with (existing / "summary.csv").open(newline="") as file:
                            summary, = list(csv.DictReader(file))
                            summaries.append(summary)
                        continue
                    jobs.append({"n": n, "placement": placement, "protocol": protocol, "run": run,
                                 "binary": str(binary), "root": str(root), "parameters": parameters,
                                 "reports": not args.no_reports, "binary_sha256": binary_hash})
    total = len(jobs) + len(summaries)
    print(f"Study: {total} experiments; {len(summaries)} already complete; {args.workers} workers.", flush=True)
    failures = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_experiment, job) for job in jobs]
        for future in as_completed(futures):
            try:
                name, summary = future.result()
                summaries.append(summary)
                write_combined(root, summaries)
                print(f"[{len(summaries)}/{total}] {name}: {float(summary['gateway_throughput_mbps']):.4f} Mbps, "
                      f"delivery {float(summary['delivery_ratio']):.3f}, uplink utilization {float(summary['uplink_utilization']):.3f}", flush=True)
            except Exception as error:
                failures.append(str(error))
                print(str(error), file=sys.stderr, flush=True)
            (root / "progress.json").write_text(json.dumps({"complete": len(summaries), "total": total,
                                                           "failures": failures}, indent=2), encoding="utf-8")
    if failures:
        raise SystemExit(f"{len(failures)} experiments failed. Resolve the cause and resume with matching parameters.")
    write_combined(root, summaries)
    (root / "progress.json").write_text(json.dumps({"complete": len(summaries), "total": total,
                                                   "failures": []}, indent=2), encoding="utf-8")
    if not args.no_reports:
        from report_results import render_comparison
        render_comparison(root, summaries)
    print(f"Completed {len(summaries)} experiments. Open {root / 'index.html' if not args.no_reports else root / 'combined-summary.csv'}", flush=True)


if __name__ == "__main__":
    main()
