"""
Turn SBMETRICS log lines from svc_bus_v5_with_logs into CSV tables (Excel / pandas).

Run:
    kubectl logs <pod> > pod.log           (or export the logs from Azure Monitor)
    python parse_sb_metrics.py pod.log [more.log ...] [--run sent_runs/<run_id>/sent_messages.json] [--out metrics_out]

Writes one CSV per event into --out (default metrics_out):
    message_summary.csv   one row per message: all timestamps, durations, outcome
    monitor.csv           memory / in_flight / counters over time
    message_accepted.csv, message_processed.csv, message_sent.csv, listener_started.csv

With --run, message_summary.csv is limited to that run's message_ids and gets a
`sender_sent_at` / `sender_sent_epoch` column from the sender's file, and
missing_messages.csv lists the ids that were sent but never showed up in the logs.

Works whether the log line is plain text or wrapped in a JSON / CSV export
(escaped quotes are handled). In pandas:  pd.read_csv("metrics_out/message_summary.csv")
"""
import argparse
import csv
import json
import os
from collections import defaultdict

PREFIX = "SBMETRICS "
_decoder = json.JSONDecoder()


def parse_line(line):
    i = line.find(PREFIX)
    if i < 0:
        return None
    text = line[i + len(PREFIX):]
    for candidate in (text, text.replace('\\"', '"'), text.replace('""', '"')):
        try:
            obj, _ = _decoder.raw_decode(candidate.lstrip())
            if isinstance(obj, dict) and "event" in obj:
                return obj
        except ValueError:
            continue
    return None


def write_csv(path, rows):
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", nargs="+", help="log files")
    parser.add_argument("--run", help="sent_messages.json written by send_test_messages.py")
    parser.add_argument("--out", default="metrics_out")
    args = parser.parse_args()

    events = defaultdict(list)
    seen = set()
    for path in args.logs:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                record = parse_line(line)
                if record is None:
                    continue
                key = json.dumps(record, sort_keys=True)   # drop exact duplicates (same line exported twice)
                if key in seen:
                    continue
                seen.add(key)
                events[record["event"]].append(record)

    if args.run:
        with open(args.run, encoding="utf-8") as f:
            run = json.load(f)
        sent = {m["message_id"]: m for m in run["messages"] if m.get("ok")}
        summaries = [r for r in events.get("message_summary", []) if r.get("message_id") in sent]
        for r in summaries:
            r["sender_sent_at"] = sent[r["message_id"]]["sent_at"]
            r["sender_sent_epoch"] = sent[r["message_id"]]["sent_epoch"]
        events["message_summary"] = summaries
        found = {r["message_id"] for r in summaries}
        missing = [{"message_id": mid} for mid in sent if mid not in found]
        events["missing_messages"] = missing
        print(f"run {run['run_id']}: {len(sent)} sent, {len(found)} with a message_summary, {len(missing)} missing")

    os.makedirs(args.out, exist_ok=True)
    for event, rows in sorted(events.items()):
        rows.sort(key=lambda r: r.get("ts_epoch") or 0)
        path = os.path.join(args.out, f"{event}.csv")
        write_csv(path, rows)
        print(f"{len(rows):6d} rows -> {path}")


if __name__ == "__main__":
    main()
