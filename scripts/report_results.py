"""Create scientific figures and a self-contained animation from recorded ns-3 data."""

import argparse
from collections import defaultdict
import csv
import html
import json
import math
import os
from pathlib import Path
import statistics

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / ".deps" / "matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

COLORS = {"AODV": "#c75b18", "OLSR": "#1976a3", "DSDV": "#6953a6"}
plt.rcParams.update({"font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "savefig.dpi": 150})


def rows(path):
    with Path(path).open(newline="") as file:
        return list(csv.DictReader(file))


def number(row, field):
    return float(row[field]) if row[field] else math.nan


def save_figure(fig, destination):
    fig.savefig(destination.with_suffix(".png"), bbox_inches="tight")
    fig.savefig(destination.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)


def render_experiment(folder):
    folder = Path(folder)
    summary, = rows(folder / "summary.csv")
    times = rows(folder / "timeseries.csv")
    node_times = rows(folder / "node-timeseries.csv")
    topology = rows(folder / "topology.csv")
    sources = rows(folder / "per-source.csv")
    n = int(summary["grid_size"])
    gateway = int(summary["gateway_id"])
    start = float(summary["warmup_s"])
    duration = float(summary["duration_s"])
    title = f"{n}×{n} · {summary['gateway']} gateway · {summary['routing']} · run {summary['run']}"
    figures = folder / "figures"
    figures.mkdir(exist_ok=True)
    t = [float(row["traffic_time_s"]) for row in times]
    fig, axes = plt.subplots(2, 2, figsize=(12, 7), layout="constrained")
    ax = axes[0, 0]
    ax.plot(t, [number(r, "offered_mbps") for r in times], color="#777", label="Sources: UDP payload")
    ax.plot(t, [number(r, "gateway_forward_mbps") for r in times], color="#d17520", label="Gateway: IP forwarding")
    ax.plot(t, [number(r, "server_throughput_mbps") for r in times], color="#176a93", label="External server: UDP payload")
    ax.axhline(float(summary["uplink_mbps"]), ls=":", color="#b33", label="External wire bit rate")
    ax.set(ylabel="Rate (Mbps)", title="Offered and delivered traffic (byte layers differ)")
    ax.legend(fontsize=8)
    ax = axes[0, 1]
    ax.plot(t, [number(r, "uplink_queue_mean_packets") for r in times], color="#b33", label="Wired egress")
    by_time = defaultdict(list)
    for row in node_times:
        by_time[float(row["time_s"])].append(row)
    max_wifi = [max(number(r, "wifi_queue_mean_packets") for r in by_time[float(row["time_s"])] ) for row in times]
    ax.plot(t, max_wifi, color="#1976a3", label="Largest Wi-Fi MAC queue")
    ax.set(ylabel="Time-weighted mean packets", title="Measured queue buildup")
    ax.legend(fontsize=8)
    ax = axes[1, 0]
    ax.plot(t, [number(r, "mean_delay_ms") for r in times], color="#6953a6")
    ax.set(ylabel="Mean delivered-packet delay (ms)", title="End-to-end delay by receive interval")
    ax = axes[1, 1]
    ax.step(t, [number(r, "uplink_queue_drops") for r in times], where="mid", color="#b33", label="Wired egress queue")
    ax.step(t, [sum(number(r, "wifi_queue_drops") for r in by_time[float(row["time_s"])]) for row in times],
            where="mid", color="#1976a3", label="All Wi-Fi MAC queues")
    ax.set(ylabel="Queue drop events per interval", title="Queue drops (data and routing packets)")
    ax.legend(fontsize=8)
    for ax in axes.flat:
        ax.axvline(duration, color="#777", ls="--", alpha=.6)
        ax.set_xlabel("Seconds since traffic start; dashed line begins drain")
        ax.grid(alpha=.2)
        ax.set_xlim(0, float(summary["duration_s"]) + float(summary["drain_s"]))
        ax.set_ylim(bottom=0)
    if not any(float(r["uplink_queue_drops"]) for r in times) and not any(int(r["wifi_queue_drops"]) for r in node_times):
        axes[1, 1].set_ylim(0, 1)
    fig.suptitle(title)
    save_figure(fig, figures / "traffic-and-bottlenecks")

    active_means = defaultdict(float)
    for row in times:
        if row["phase"] == "traffic":
            for node in by_time[float(row["time_s"])]:
                active_means[int(node["node_id"])] += number(node, "wifi_queue_mean_packets") * float(row["interval_s"]) / duration
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), layout="constrained")
    source_by_id = {int(row["node_id"]): row for row in sources}
    ids = list(range(n * n))
    rates = [number(source_by_id[i], "throughput_mbps") if i in source_by_id else 0 for i in ids]
    axes[0].bar(ids, rates, color=["#d17520" if i == gateway else "#1976a3" for i in ids])
    fairness = f"{float(summary['jain_fairness']):.3f}" if summary['jain_fairness'] else "undefined"
    axes[0].set(xlabel="Mesh source node ID (gateway has no source flow)", ylabel="Server-delivered UDP Mbps",
                title=f"Per-source throughput · Jain fairness {fairness}")
    axes[0].grid(axis="y", alpha=.2)
    positions = [r for r in topology if r["role"] != "server"]
    xs = [number(r, "x_m") for r in positions]
    ys = [number(r, "y_m") for r in positions]
    scale = max(active_means.values(), default=1) or 1
    points = axes[1].scatter(xs, ys, c=[active_means[i] for i in ids], cmap="YlOrRd",
                             norm=Normalize(0, scale), s=max(80, 650 / n), edgecolors="#666")
    gx, gy = xs[gateway], ys[gateway]
    axes[1].scatter([gx], [gy], s=max(120, 950 / n), facecolors="none", edgecolors="#1976a3", linewidths=2)
    for i, (x, y) in enumerate(zip(xs, ys)):
        if n <= 6: axes[1].annotate(str(i), (x, y), xytext=(5, 5), textcoords="offset points", fontsize=8)
    axes[1].set(xlabel="x (m)", ylabel="y (m)", title="Wi-Fi MAC queues · blue ring marks gateway", aspect="equal")
    axes[1].invert_yaxis()
    fig.colorbar(points, ax=axes[1], label="Active-interval mean packets (scale local to this figure)")
    fig.suptitle(title)
    save_figure(fig, figures / "sources-and-queues")

    frames = []
    for row in times:
        nodes = sorted(by_time[float(row["time_s"])], key=lambda x: int(x["node_id"]))
        frames.append({"t": float(row["traffic_time_s"]), "phase": row["phase"],
                       "queue": [number(r, "wifi_queue_mean_packets") for r in nodes],
                       "forward": [number(r, "forward_mbps") for r in nodes],
                       "wireQueue": number(row, "uplink_queue_mean_packets"),
                       "wireDrops": int(row["uplink_queue_drops"]),
                       "wifiDrops": sum(int(r["wifi_queue_drops"]) for r in nodes),
                       "throughput": number(row, "server_throughput_mbps"),
                       "delay": number(row, "mean_delay_ms") if row["mean_delay_ms"] else None})
    payload = {"n": n, "gateway": gateway, "wifiCapacity": int(summary["wifi_queue_capacity"]),
               "rangeCells": float(summary["range_m"]) / float(summary["spacing_m"]),
               "wireCapacity": int(summary["uplink_queue_capacity"]),
               "uplinkMbps": float(summary["uplink_mbps"]), "frames": frames,
               "label": title, "duration": duration}
    (folder / "animation.html").write_text(ANIMATION.replace("__DATA__", json.dumps(payload)), encoding="utf-8")
    (folder / "report.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>" + html.escape(title) + "</title>"
        "<style>body{font:16px system-ui;max-width:1150px;margin:32px auto;padding:0 20px}img{width:100%}"
        "table{border-collapse:collapse}td{padding:5px 15px;border-bottom:1px solid #ddd}</style>"
        f"<h1>{html.escape(title)}</h1><p><a href='animation.html'>Play measured heatmap animation</a></p>"
        "<p>Every source sends through the mesh gateway and its finite wired queue to an external server. "
        "Queue heatmaps show measured interval averages. Radio and routing losses may dominate even when the wired link is underutilized.</p>"
        "<img src='figures/traffic-and-bottlenecks.png'><img src='figures/sources-and-queues.png'><table>" +
        "".join(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>" for k, v in summary.items()) +
        "</table><p>Raw data: <a href='summary.csv'>summary</a>, <a href='per-source.csv'>sources</a>, "
        "<a href='timeseries.csv'>time series</a>, <a href='node-timeseries.csv'>node time series</a>.</p>", encoding="utf-8")
    return summary


def render_comparison(root, results):
    root = Path(root)
    destination = root / "comparisons"
    destination.mkdir(exist_ok=True)
    fields = [("gateway_throughput_mbps", "External-server UDP throughput (Mbps)"),
              ("mean_delay_ms", "Delivered-packet mean delay (ms)"),
              ("loss_ratio", "End-to-end undelivered fraction"),
              ("jain_fairness", "Jain fairness"),
              ("uplink_utilization", "External wire utilization"),
              ("uplink_queue_drops", "External queue drops (all packets)")]
    groups = defaultdict(list)
    for result in results:
        groups[(int(result["grid_size"]), result["gateway"], result["routing"])].append(result)
    aggregated = []
    for (n, placement, protocol), records in sorted(groups.items()):
        group = {"grid_size": n, "gateway": placement, "routing": protocol, "runs": len(records)}
        for field, _ in fields:
            values = [number(r, field) for r in records if r[field]]
            group[field + "_mean"] = statistics.mean(values) if values else ""
            group[field + "_sd"] = statistics.stdev(values) if len(values) > 1 else 0 if values else ""
        aggregated.append(group)
    with (destination / "group-summary.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(aggregated[0]))
        writer.writeheader()
        writer.writerows(aggregated)
    for placement in ("center", "corner"):
        fig, axes = plt.subplots(3, 2, figsize=(12, 11), layout="constrained")
        for ax, (field, label) in zip(axes.flat, fields):
            for protocol, color in COLORS.items():
                points = sorted((r for r in aggregated if r["gateway"] == placement and r["routing"] == protocol), key=lambda r:r["grid_size"])
                valid = [r for r in points if r[field + "_mean"] != ""]
                if valid:
                    ax.errorbar([r["grid_size"] for r in valid], [r[field + "_mean"] for r in valid],
                                yerr=[r[field + "_sd"] for r in valid], fmt="o-", capsize=3,
                                color=color, label=protocol)
            ax.set(xlabel="n (n×n mesh; n²−1 sources)", ylabel=label)
            ax.set_xticks(sorted({r["grid_size"] for r in aggregated}))
            ax.set_ylim(bottom=0)
            ax.grid(alpha=.2)
            ax.legend(fontsize=8)
        fig.suptitle(f"{placement.title()} gateway · means ± one sample SD across independent runs")
        save_figure(fig, destination / f"{placement}-comparison")
    fig, axes = plt.subplots(3, 3, figsize=(14, 11), layout="constrained")
    for column, protocol in enumerate(COLORS):
        for row_index, (field, label) in enumerate(fields[:3]):
            ax = axes[row_index, column]
            for placement, color, marker in (("center", "#1976a3", "o"), ("corner", "#c75b18", "s")):
                points = sorted((r for r in aggregated if r["gateway"] == placement and r["routing"] == protocol
                                 and r[field + "_mean"] != ""), key=lambda r:r["grid_size"])
                if points:
                    ax.errorbar([r["grid_size"] for r in points], [r[field + "_mean"] for r in points],
                                yerr=[r[field + "_sd"] for r in points], marker=marker,
                                capsize=3, color=color, label=placement)
            ax.set(xlabel="n (n×n mesh)", ylabel=label)
            ax.set_xticks(sorted({r["grid_size"] for r in aggregated}))
            ax.set_ylim(bottom=0)
            if row_index == 0:
                ax.set_title(protocol)
                ax.legend(fontsize=8)
            ax.grid(alpha=.2)
    fig.suptitle("Center versus corner placement · means ± one sample SD")
    save_figure(fig, destination / "placement-comparison")
    links = []
    for result in sorted(results, key=lambda r:(int(r["grid_size"]), r["gateway"], r["routing"], int(r["run"]))):
        folder = experiment_name(int(result["grid_size"]), result["gateway"], result["routing"], int(result["run"]))
        links.append(f"<tr><td>{html.escape(folder)}</td><td><a href='{folder}/report.html'>Charts and data</a></td>"
                     f"<td><a href='{folder}/animation.html'>Animation</a></td></tr>")
    (root / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Gateway bottleneck experiments</title>"
        "<style>body{font:16px system-ui;max-width:1200px;margin:32px auto;padding:0 20px}"
        "img{max-width:100%}td{padding:7px 16px;border-bottom:1px solid #ddd}</style>"
        f"<h1>Gateway bottleneck experiments</h1><p>{len(results)} completed ns-3 simulations. "
        "Comparison error bars show one sample standard deviation, not confidence intervals. "
        "The external server participates in the selected routing protocol over the wired link.</p>"
        f"<p>Per-source offered UDP load: {results[0]['rate_kbps']} kbps. External link: "
        f"{results[0]['uplink_mbps']} Mbps, {results[0]['uplink_delay_ms']} ms one-way delay. "
        f"Each run: {results[0]['warmup_s']} s warmup, {results[0]['duration_s']} s traffic, "
        f"{results[0]['drain_s']} s drain. In a 2×2 grid the selected center node is also a corner; "
        "those two placements are geometrically equivalent under rotation.</p>"
        "<p>Queue drops include routing traffic. End-to-end undelivered packets include residual packets at termination. "
        "A heavily loaded external queue is evidence of external-link congestion; low delivery alone does not locate a bottleneck.</p>"
        "<img src='comparisons/center-comparison.png'><img src='comparisons/corner-comparison.png'>"
        "<img src='comparisons/placement-comparison.png'>"
        "<p><a href='combined-summary.csv'>All run summaries</a> · "
        "<a href='comparisons/group-summary.csv'>Group means and standard deviations</a></p><table>" +
        "".join(links) + "</table>", encoding="utf-8")


def experiment_name(n, placement, protocol, run):
    return f"n{n:02d}_{placement}_{protocol}_run{run:02d}"


ANIMATION = r'''<!doctype html><meta charset="utf-8"><title>Measured queue heatmap</title>
<style>body{font:16px system-ui;color:#163042;background:#f6f8fa;max-width:1100px;margin:24px auto;padding:0 16px}canvas{background:white;border:1px solid #cbd5df;width:100%;border-radius:8px}.controls{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:16px 0}input{flex:1;min-width:180px}button,select{padding:8px}#status{min-height:48px}a{color:#176a93}</style>
<h1 id="title"></h1><p><a href="report.html">Charts and raw data</a></p>
<div class="controls"><button id="play">Play</button><input id="slider" type="range" min="0" step="1"><select id="metric"><option value="queue">Wi-Fi MAC queue (mean packets)</option><option value="forward">Data IP forwarding (Mbps)</option></select><select id="speed"><option value="500">2 frames/s</option><option value="200" selected>5 frames/s</option><option value="100">10 frames/s</option></select></div>
<div id="status"></div><canvas id="canvas" width="1050" height="720"></canvas>
<p>Colors show measured interval averages on a fixed logarithmic scale. White lines indicate possible neighboring links; they do not trace actual packet routes. The gateway's wired queue is shown separately. Wi-Fi MAC and wired queue sizes exclude routing-protocol buffers and packets in transmission.</p>
<script>
const data=__DATA__;const canvas=document.querySelector('#canvas'),ctx=canvas.getContext('2d');const slider=document.querySelector('#slider');let timer=null;
document.querySelector('#title').textContent=data.label;slider.max=data.frames.length-1;slider.value=0;
const metric=document.querySelector('#metric');
function color(value,max){const f=Math.max(0,Math.min(1,Math.log1p(value)/Math.log1p(max)));return `rgb(${Math.round(248-40*f)},${Math.round(245-210*f)},${Math.round(215-175*f)})`;}
function draw(){const frame=data.frames[Number(slider.value)];if(!frame)return;ctx.clearRect(0,0,1050,720);const n=data.n,step=Math.min(58,480/(n-1)),left=70,top=95,r=Math.min(20,step*.31);const val=frame[metric.value],max=metric.value==='queue'?data.wifiCapacity:6;
const point=i=>[left+(i%n)*step,top+Math.floor(i/n)*step];ctx.strokeStyle='#cdd5dc';ctx.lineWidth=1;
for(let i=0;i<n*n;i++){const [x,y]=point(i);for(let j=i+1;j<n*n;j++)if(Math.hypot(i%n-j%n,Math.floor(i/n)-Math.floor(j/n))<=data.rangeCells+1e-9){const [x2,y2]=point(j);ctx.beginPath();ctx.moveTo(x,y);ctx.lineTo(x2,y2);ctx.stroke();}}
for(let i=0;i<n*n;i++){const [x,y]=point(i);ctx.fillStyle=color(val[i],max);ctx.strokeStyle=i===data.gateway?'#176a93':'#657889';ctx.lineWidth=i===data.gateway?4:1;ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.fillStyle='#163042';ctx.font=`${n>7?10:13}px system-ui`;ctx.textAlign='center';ctx.fillText(i,x,y+4);}
const [gx,gy]=point(data.gateway);ctx.strokeStyle='#176a93';ctx.lineWidth=3;ctx.setLineDash([6,5]);ctx.beginPath();ctx.moveTo(gx,gy);ctx.lineTo(780,180);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=color(frame.wireQueue,data.wireCapacity);ctx.fillRect(710,115,270,155);ctx.strokeStyle='#176a93';ctx.strokeRect(710,115,270,155);ctx.textAlign='left';ctx.fillStyle='#163042';ctx.font='17px system-ui';ctx.fillText('Gateway wired egress queue',725,143);ctx.fillText(`${frame.wireQueue.toFixed(2)} mean packets`,725,177);ctx.fillText(`${frame.wireDrops} drops this interval`,725,208);ctx.fillText(`${data.uplinkMbps} Mbps → external server`,725,243);
ctx.font='16px system-ui';ctx.fillText('Mesh node IDs; blue outline = gateway',50,35);ctx.fillText(metric.options[metric.selectedIndex].text,50,640);for(let x=0;x<480;x++){const f=x/479;ctx.fillStyle=color(Math.expm1(f*Math.log1p(max)),max);ctx.fillRect(50+x,660,1,18);}ctx.fillStyle='#163042';ctx.fillText('0',50,705);ctx.fillText(String(max),510,705);ctx.font='16px system-ui';ctx.fillText(`Server: ${frame.throughput.toFixed(3)} UDP Mbps`,710,320);ctx.fillText(`Delay: ${frame.delay===null?'no delivery':frame.delay.toFixed(1)+' ms'}`,710,353);ctx.fillText(`Wi-Fi queue drops: ${frame.wifiDrops}`,710,386);
document.querySelector('#status').textContent=`Time ${frame.t.toFixed(1)} s after traffic start · ${frame.phase} · measured interval ending at this time. Playback speed changes display only.`;}
function stop(){clearInterval(timer);timer=null;document.querySelector('#play').textContent='Play';}function play(){stop();if(Number(slider.value)>=data.frames.length-1){slider.value=0;draw();}timer=setInterval(()=>{if(Number(slider.value)>=data.frames.length-1){stop();return;}slider.value=Number(slider.value)+1;draw();},Number(document.querySelector('#speed').value));document.querySelector('#play').textContent='Pause';}
document.querySelector('#play').onclick=()=>timer?stop():play();slider.oninput=draw;metric.onchange=draw;document.querySelector('#speed').onchange=()=>{if(timer)play();};draw();
</script>'''


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--study", action="store_true", help="Regenerate all figures and animations from existing study CSVs")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    if args.study:
        from concurrent.futures import ProcessPoolExecutor
        results = rows(args.folder / "combined-summary.csv")
        folders = [args.folder / experiment_name(int(r["grid_size"]), r["gateway"], r["routing"], int(r["run"])) for r in results]
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(render_experiment, folders))
        render_comparison(args.folder, results)
    else:
        render_experiment(args.folder)
