"""
Compare load-test runs at different concurrency levels (2, 5, 10, 20, ...) in one report.

Run:
    python compare_runs.py sent_runs/run-A/metrics_out/message_summary.csv sent_runs/run-B/metrics_out/message_summary.csv ...
    python compare_runs.py --list runs.txt          (one message_summary.csv path per line)
    python compare_runs.py ... --out compare.html

Needs nothing but Python; the report is one HTML file (open it in a browser, needs internet
for the chart library). It also writes  <out>_summary.csv  with the numbers table.

Worker count of a run: the highest worker_id in its message_summary.csv. If listener_started.csv
is next to it and says a different max_in_flight, a NOTE is printed (nothing else changes).

Charts (all runs overlaid, one colour per run, sorted by worker count):
  1. Messages finished over time - the slope is the throughput. Steeper = faster.
  2. processor time per message - does each call get slower with more workers? (throttling)
  3. Queue wait per message - how long messages waited for a free worker.
  4. Memory over time (if monitor.csv is next to message_summary.csv) - RSS of the process.
Plus a table: throughput, median / p95 of each duration, outcomes, peak memory.
"""
import argparse
import csv
import json
import os
import random
from datetime import datetime


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def num(value):
    try:
        return None if value in (None, "", "None", "null") else float(value)
    except ValueError:
        return None


def percentile(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 3)


def load_run(summary_path):
    folder = os.path.dirname(os.path.abspath(summary_path))
    rows = read_csv(summary_path)
    if not rows:
        raise SystemExit(f"{summary_path}: no rows (is it a message_summary.csv?)")

    # Run name: the folder above metrics_out (= the run_id), else the folder itself.
    run_name = os.path.basename(os.path.dirname(folder)) if os.path.basename(folder) == "metrics_out" else os.path.basename(folder)

    # Worker count = the highest worker_id in the summary.
    worker_ids = [int(num(r.get("worker_id"))) for r in rows if num(r.get("worker_id")) is not None]
    if not worker_ids:
        raise SystemExit(f"{summary_path}: no worker_id values")
    workers, source = max(worker_ids), "highest worker_id"
    # Only a cross-check: if the listener was configured with a different number, say so.
    started = read_csv(os.path.join(folder, "listener_started.csv"))
    configured = sorted({int(num(r.get("max_in_flight"))) for r in started if num(r.get("max_in_flight"))})
    if configured and configured != [workers]:
        print(f"  NOTE {run_name}: highest worker_id is {workers}, listener_started.csv says max_in_flight={configured}")

    # Time zero = first moment of the run: earliest enqueue (sender) or, if unknown, earliest accept.
    enqueued = [num(r.get("enqueued_epoch")) for r in rows if num(r.get("enqueued_epoch"))]
    accepted = [num(r.get("accepted_epoch")) for r in rows if num(r.get("accepted_epoch"))]
    t0 = min(enqueued) if enqueued else min(accepted)

    settled = sorted(num(r.get("settled_epoch")) - t0 for r in rows if num(r.get("settled_epoch")))
    processor = [num(r.get("processor_seconds")) if num(r.get("processor_seconds")) is not None
                 else num(r.get("processing_seconds")) for r in rows]
    queue_wait = [num(r.get("queue_wait_seconds")) for r in rows]
    end_to_end = [num(r.get("end_to_end_seconds")) for r in rows]
    total = [num(r.get("total_seconds")) for r in rows]
    outcomes = {}
    for r in rows:
        outcomes[r.get("outcome") or "unknown"] = outcomes.get(r.get("outcome") or "unknown", 0) + 1

    # Memory from monitor.csv in the same folder, limited to the run's time window.
    t_end = t0 + (settled[-1] if settled else 0)
    memory = []
    for r in read_csv(os.path.join(folder, "monitor.csv")):
        ts = num(r.get("ts_epoch"))
        mem = num(r.get("container_working_set_mb")) or num(r.get("rss_mb"))
        if ts is not None and mem is not None and t0 - 30 <= ts <= t_end + 30:
            memory.append({"x": round(ts - t0, 2), "y": mem, "in_flight": num(r.get("in_flight"))})
    peak_rss = [num(r.get("peak_rss_mb")) for r in read_csv(os.path.join(folder, "monitor.csv"))]
    peak_rss = max([v for v in peak_rss if v is not None], default=None)

    finished = len(settled)
    wall = settled[-1] if settled else None
    return {
        "run": run_name,
        "path": summary_path,
        "workers": workers,
        "workers_source": source,
        "messages": len(rows),
        "finished": finished,
        "wall_seconds": round(wall, 1) if wall else None,
        "throughput_per_min": round(finished / wall * 60, 2) if wall else None,
        "processor_p50": percentile(processor, 50), "processor_p95": percentile(processor, 95),
        "queue_wait_p50": percentile(queue_wait, 50), "queue_wait_p95": percentile(queue_wait, 95),
        "total_p50": percentile(total, 50), "total_p95": percentile(total, 95),
        "end_to_end_p50": percentile(end_to_end, 50), "end_to_end_p95": percentile(end_to_end, 95),
        "peak_memory_mb": max([m["y"] for m in memory], default=None),
        "peak_rss_mb": peak_rss,
        "outcomes": outcomes,
        # chart data
        "cumulative": [{"x": round(t, 2), "y": i + 1} for i, t in enumerate(settled)],
        "processor": [round(v, 3) for v in processor if v is not None],
        "queue_wait": [round(v, 3) for v in queue_wait if v is not None],
        "memory": memory,
    }


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Concurrency Run Comparison</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    --surface-0: #f7f6f3; --surface-1: #ffffff; --border: #e3e1da;
    --text-primary: #1f1e1b; --text-secondary: #5c5a52; --text-muted: #8a877d; --grid: #ecebe6;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --surface-0: #121211; --surface-1: #1a1a19; --border: #2f2f2c;
      --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8f8d84; --grid: #2a2a27;
    }
  }
  :root[data-theme="dark"] {
    --surface-0: #121211; --surface-1: #1a1a19; --border: #2f2f2c;
    --text-primary: #f0efec; --text-secondary: #c3c2b7; --text-muted: #8f8d84; --grid: #2a2a27;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--surface-0); color: var(--text-primary);
         font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 48px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--text-secondary); margin: 0 0 20px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 520px), 1fr)); gap: 16px; }
  .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px; min-width: 0; }
  .card h2 { font-size: 15px; margin: 0 0 2px; }
  .card p { color: var(--text-secondary); margin: 0 0 10px; font-size: 13px; }
  .chart { position: relative; height: 300px; }
  .table-wrap { overflow-x: auto; margin-top: 16px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
  th, td { padding: 6px 10px; text-align: right; border-bottom: 1px solid var(--border); white-space: nowrap; }
  th { color: var(--text-secondary); font-weight: 600; }
  th:first-child, td:first-child, td.left { text-align: left; }
  .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: 0; }
  .muted { color: var(--text-muted); }
  .note { color: var(--text-secondary); font-size: 13px; margin-top: 12px; }
</style>
</head>
<body>
<main>
  <h1>Concurrency run comparison</h1>
  <p class="sub" id="sub"></p>
  <div class="grid">
    <section class="card"><h2>Messages finished over time</h2>
      <p>Cumulative messages settled, seconds since the run's first message was enqueued. Steeper = higher throughput.</p>
      <div class="chart"><canvas id="cumulative"></canvas></div></section>
    <section class="card"><h2>Processor time per message</h2>
      <p>Each dot is one message; the bar is the median. Rising with more workers = calls slow down (e.g. throttling).</p>
      <div class="chart"><canvas id="processor"></canvas></div></section>
    <section class="card"><h2>Queue wait per message</h2>
      <p>Time from enqueue until a worker picked the message up. Large = not enough workers for the burst.</p>
      <div class="chart"><canvas id="queue"></canvas></div></section>
    <section class="card" id="memoryCard"><h2>Memory over time</h2>
      <p id="memoryNote">Container working set (or process RSS locally), MB, from monitor.csv.</p>
      <div class="chart"><canvas id="memory"></canvas></div></section>
  </div>
  <section class="card table-wrap"><h2>Numbers</h2>
    <table id="table"></table>
    <p class="note">p50 = median, p95 = 95% of messages were at or below this. Seconds unless noted.</p>
  </section>
</main>
<script>
const RUNS = __DATA__;
const GENERATED = "__GENERATED__";

const dark = document.documentElement.dataset.theme === "dark" ||
  (document.documentElement.dataset.theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
// Validated categorical order (fixed, never cycled). Runs are sorted by worker count, so slot = rank.
const LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];
const DARK  = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const PALETTE = dark ? DARK : LIGHT;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const surface = css("--surface-1"), grid = css("--grid"), muted = css("--text-muted"), ink = css("--text-secondary");

if (RUNS.length > PALETTE.length) {
  document.getElementById("sub").insertAdjacentHTML("afterend",
    `<p class="note">More than ${PALETTE.length} runs: only the first ${PALETTE.length} (by worker count) are charted; all are in the table.</p>`);
}
const charted = RUNS.slice(0, PALETTE.length);
charted.forEach((r, i) => r.color = PALETTE[i]);
RUNS.forEach(r => r.color = r.color || muted);

const dupes = {};
RUNS.forEach(r => dupes[r.workers] = (dupes[r.workers] || 0) + 1);
RUNS.forEach(r => r.label = dupes[r.workers] > 1 ? `${r.workers} workers (${r.run.slice(-6)})` : `${r.workers} workers`);

document.getElementById("sub").textContent =
  `${RUNS.length} run(s): ${RUNS.map(r => r.label).join(", ")} · generated ${GENERATED}`;

Chart.defaults.color = ink;
Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
Chart.defaults.font.size = 12;
const axis = title => ({
  title: { display: true, text: title, color: ink },
  grid: { color: grid, drawTicks: false }, border: { display: false },
  ticks: { color: muted, padding: 6 },
});
const legend = { position: "top", align: "start",
  labels: { usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, boxHeight: 10, color: ink } };
const tooltip = { backgroundColor: dark ? "#2a2a27" : "#1f1e1b", titleColor: "#f0efec", bodyColor: "#f0efec",
  padding: 10, cornerRadius: 6, boxPadding: 4, usePointStyle: true };

// 1. cumulative finished
new Chart(document.getElementById("cumulative"), {
  type: "line",
  data: { datasets: charted.map(r => ({
    label: r.label, data: [{ x: 0, y: 0 }, ...r.cumulative], stepped: true,
    borderColor: r.color, backgroundColor: r.color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 5,
    pointHoverBorderColor: surface, pointHoverBorderWidth: 2 })) },
  options: { responsive: true, maintainAspectRatio: false, parsing: false,
    interaction: { mode: "nearest", intersect: false },
    scales: { x: { ...axis("seconds since run start"), type: "linear", min: 0 },
              y: { ...axis("messages finished"), beginAtZero: true, ticks: { ...axis("").ticks, precision: 0 } } },
    plugins: { legend, tooltip: { ...tooltip, callbacks: {
      title: items => `${items[0].parsed.x.toFixed(1)} s`,
      label: c => `${c.dataset.label}: ${c.parsed.y} finished` } } } },
});

// 2 + 3. strip plots: one column per run, jittered dots + median bar
function strip(canvasId, key, unit, emptyNote) {
  if (!charted.some(r => r[key].length)) {
    const canvas = document.getElementById(canvasId);
    canvas.parentElement.outerHTML = `<p class="note">${emptyNote}</p>`;
    return;
  }
  const rand = (() => { let s = 7; return () => (s = (s * 16807) % 2147483647) / 2147483647; })();
  const datasets = [];
  charted.forEach((r, i) => {
    const values = r[key];
    datasets.push({ label: r.label, data: values.map(v => ({ x: i + (rand() - 0.5) * 0.5, y: v })),
      backgroundColor: r.color + "b3", borderColor: surface, borderWidth: 1, pointRadius: 4, pointHoverRadius: 6 });
    const sorted = [...values].sort((a, b) => a - b);
    const med = sorted.length ? sorted[Math.floor((sorted.length - 1) / 2)] * 0.5 + sorted[Math.ceil((sorted.length - 1) / 2)] * 0.5 : null;
    if (med !== null) datasets.push({ label: `${r.label} median`, data: [{ x: i, y: med }], pointStyle: "line",
      pointRadius: 22, pointHoverRadius: 22, borderColor: css("--text-primary"), borderWidth: 3, isMedian: true });
  });
  new Chart(document.getElementById(canvasId), {
    type: "scatter", data: { datasets },
    options: { responsive: true, maintainAspectRatio: false,
      scales: { x: { ...axis("workers"), min: -0.6, max: charted.length - 0.4,
                     ticks: { ...axis("").ticks, stepSize: 1, callback: v => Number.isInteger(v) && charted[v] ? charted[v].label : "" },
                     grid: { display: false } },
                y: { ...axis(unit), beginAtZero: true } },
      plugins: { legend: { ...legend, labels: { ...legend.labels, filter: item => !item.text.endsWith(" median") } },
        tooltip: { ...tooltip, callbacks: {
          title: () => "",
          label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(2)} s` } } } },
  });
}
strip("processor", "processor", "seconds", "No processing times in these files.");
strip("queue", "queue_wait", "seconds",
  "No queue wait data: the messages had no enqueued time (real Service Bus messages always have one).");

// 4. memory
const withMemory = charted.filter(r => r.memory.length);
if (!withMemory.length) {
  document.getElementById("memoryNote").textContent =
    "No memory data: monitor.csv was not found next to the message_summary.csv files, or it had no memory values (container memory only exists on Linux / AKS; RSS needs psutil locally).";
  document.querySelector("#memoryCard .chart").style.height = "0";
} else {
  new Chart(document.getElementById("memory"), {
    type: "line",
    data: { datasets: withMemory.map(r => ({ label: r.label, data: r.memory,
      borderColor: r.color, backgroundColor: r.color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, tension: 0 })) },
    options: { responsive: true, maintainAspectRatio: false, parsing: false,
      interaction: { mode: "nearest", intersect: false },
      scales: { x: { ...axis("seconds since run start"), type: "linear" }, y: { ...axis("MB") } },
      plugins: { legend, tooltip: { ...tooltip, callbacks: {
        title: items => `${items[0].parsed.x.toFixed(1)} s`,
        label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(1)} MB` +
          (c.raw.in_flight != null ? ` (in flight ${c.raw.in_flight})` : "") } } } },
  });
}

// table
const f = (v, d = 1) => v === null || v === undefined ? '<span class="muted">-</span>' : Number(v).toFixed(d);
const cols = [
  ["Run", r => `<span class="swatch" style="background:${r.color}"></span>${r.label}<div class="muted">${r.run}</div>`, "left"],
  ["Messages", r => `${r.finished}/${r.messages}`],
  ["Wall time", r => f(r.wall_seconds)],
  ["Msgs/min", r => f(r.throughput_per_min, 2)],
  ["Processor p50", r => f(r.processor_p50, 2)], ["Processor p95", r => f(r.processor_p95, 2)],
  ["Queue wait p50", r => f(r.queue_wait_p50, 2)], ["Queue wait p95", r => f(r.queue_wait_p95, 2)],
  ["End-to-end p50", r => f(r.end_to_end_p50, 2)], ["End-to-end p95", r => f(r.end_to_end_p95, 2)],
  ["Peak memory MB", r => f(r.peak_memory_mb)], ["Peak RSS MB", r => f(r.peak_rss_mb)],
  ["Outcomes", r => Object.entries(r.outcomes).map(([k, v]) => `${k} ${v}`).join(", "), "left"],
];
document.getElementById("table").innerHTML =
  `<thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>` +
  RUNS.map(r => `<tr>${cols.map(c => `<td class="${c[2] || ""}">${c[1](r)}</td>`).join("")}</tr>`).join("") + "</tbody>";
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Overlay load-test runs at different concurrency levels.")
    parser.add_argument("summaries", nargs="*", help="message_summary.csv files")
    parser.add_argument("--list", help="text file with one message_summary.csv path per line")
    parser.add_argument("--out", default=None, help="output HTML (default: compare_runs_<timestamp>.html)")
    args = parser.parse_args()

    paths = list(args.summaries)
    if args.list:
        with open(args.list, encoding="utf-8") as f:
            paths += [line.strip().strip('"') for line in f if line.strip() and not line.strip().startswith("#")]
    if not paths:
        raise SystemExit("Give one or more message_summary.csv paths (or --list file).")

    runs = [load_run(p) for p in paths]
    runs.sort(key=lambda r: (r["workers"], r["run"]))

    out = args.out or f"compare_runs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    html = HTML.replace("__DATA__", json.dumps(runs)).replace("__GENERATED__", datetime.now().strftime("%Y-%m-%d %H:%M"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    summary_csv = os.path.splitext(out)[0] + "_summary.csv"
    keys = ["workers", "workers_source", "run", "messages", "finished", "wall_seconds", "throughput_per_min",
            "processor_p50", "processor_p95", "queue_wait_p50", "queue_wait_p95", "total_p50", "total_p95",
            "end_to_end_p50", "end_to_end_p95", "peak_memory_mb", "peak_rss_mb", "outcomes", "path"]
    with open(summary_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in runs:
            writer.writerow({k: (json.dumps(r[k]) if k == "outcomes" else r[k]) for k in keys})

    for r in runs:
        print(f"  {r['workers']:>3} workers ({r['workers_source']}): {r['finished']}/{r['messages']} finished, "
              f"{r['throughput_per_min']} msgs/min, processor p50 {r['processor_p50']}s  [{r['run']}]")
    print(f"Report: {out}\nTable:  {summary_csv}")


if __name__ == "__main__":
    main()
