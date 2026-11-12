"""
Send N test messages to the IN queue, for load testing svc_bus_v5_with_logs.

Run:
    python send_test_messages.py --file message.json --count 50
    python send_test_messages.py --file message.json --count 200 --interval 0.5

  --file           JSON file with the message body. Either one object (sent N
                   times) or a list of objects (cycled through: 1st, 2nd, ...).
  --count          how many messages to send (N)
  --interval       seconds to wait between sends (default 0 = as fast as possible)
  --keep-job-id    keep the job_id from the file. By default every message's
                   "job_id" is REPLACED by its message_id, so each message has
                   its own job_id in the listener logs.
  --queue          default: env APP_SERVICE_IN_QUEUE
  --namespace      default: env APP_SB_FULLY_QUALIFIED_NAMESPACE
  --out-dir        default: sent_runs

Every message gets a unique message_id  <run_id>-00001, <run_id>-00002, ...
its body's job_id is set to that same id, and it gets the application
property  test_run_id=<run_id>, which the listener logs, so you can filter
the SBMETRICS lines of one run.

The list of sent ids is saved to  <out-dir>/<run_id>/sent_messages.json :
    {
      "run_id": "...", "queue": "...", "namespace": "...", "source_file": "...",
      "count_requested": N, "count_sent": n, "count_failed": f,
      "started_at": "...", "finished_at": "...",
      "message_ids": ["<run_id>-00001", ...],
      "messages": [{"index": 1, "message_id": "...", "job_id": "...",
                    "sent_at": "...", "sent_epoch": 0.0, "ok": true, "error": null}, ...]
    }
The file is written even if the run stops halfway (Ctrl+C or an error).
"""
import argparse
import json
import os
import time
import uuid
from datetime import datetime, timezone

from azure.servicebus import ServiceBusClient, ServiceBusMessage
from bz5_commons.interfaces.authentication_interface import get_credential


def utc_iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="milliseconds")


def load_bodies(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    bodies = data if isinstance(data, list) else [data]
    if not bodies:
        raise SystemExit(f"{path} contains an empty list")
    return bodies


def save(run_file, record):
    tmp = run_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    os.replace(tmp, run_file)


def main():
    parser = argparse.ArgumentParser(description="Send N test messages to the Service Bus IN queue.")
    parser.add_argument("--file", required=True, help="JSON file with the message body (object or list of objects)")
    parser.add_argument("--count", type=int, required=True, help="number of messages to send")
    parser.add_argument("--interval", type=float, default=0.0, help="seconds between sends (default 0)")
    parser.add_argument("--keep-job-id", action="store_true",
                        help="keep job_id from the file (default: job_id is replaced by the message_id)")
    parser.add_argument("--queue", default=os.getenv("APP_SERVICE_IN_QUEUE"))
    parser.add_argument("--namespace", default=os.getenv("APP_SB_FULLY_QUALIFIED_NAMESPACE"))
    parser.add_argument("--out-dir", default="sent_runs")
    args = parser.parse_args()

    if not args.queue or not args.namespace:
        raise SystemExit("Set APP_SERVICE_IN_QUEUE and APP_SB_FULLY_QUALIFIED_NAMESPACE (or pass --queue / --namespace).")
    if args.count < 1:
        raise SystemExit("--count must be at least 1")

    bodies = load_bodies(args.file)
    run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
    run_dir = os.path.join(args.out_dir, run_id)
    os.makedirs(run_dir, exist_ok=True)
    run_file = os.path.join(run_dir, "sent_messages.json")

    record = {
        "run_id": run_id,
        "queue": args.queue,
        "namespace": args.namespace,
        "source_file": os.path.abspath(args.file),
        "count_requested": args.count,
        "count_sent": 0,
        "count_failed": 0,
        "interval_seconds": args.interval,
        "job_id_replaced": not args.keep_job_id,
        "started_at": utc_iso(time.time()),
        "finished_at": None,
        "message_ids": [],
        "messages": [],
    }
    print(f"Run {run_id}: sending {args.count} message(s) to {args.queue}. Ids go to {run_file}")

    credential = get_credential()
    try:
        # One connection for the whole run, so the sends go out as a real burst.
        with ServiceBusClient(args.namespace, credential) as client, \
                client.get_queue_sender(queue_name=args.queue) as sender:
            for i in range(1, args.count + 1):
                message_id = f"{run_id}-{i:05d}"
                body = json.loads(json.dumps(bodies[(i - 1) % len(bodies)]))   # fresh copy
                if not args.keep_job_id and isinstance(body, dict):
                    body["job_id"] = message_id
                job_id = body.get("job_id") if isinstance(body, dict) else None
                entry = {"index": i, "message_id": message_id, "job_id": job_id,
                         "sent_at": None, "sent_epoch": None, "ok": False, "error": None}
                try:
                    sender.send_messages(ServiceBusMessage(
                        body=json.dumps(body),
                        message_id=message_id,
                        application_properties={"test_run_id": run_id},
                    ))
                    now = time.time()
                    entry.update(sent_at=utc_iso(now), sent_epoch=round(now, 3), ok=True)
                    record["count_sent"] += 1
                    record["message_ids"].append(message_id)
                except Exception as e:
                    entry["error"] = repr(e)[:500]
                    record["count_failed"] += 1
                    print(f"  {message_id} FAILED: {e!r}")
                record["messages"].append(entry)
                if i % 25 == 0 or i == args.count:
                    print(f"  {i}/{args.count} done ({record['count_failed']} failed)")
                    save(run_file, record)
                if args.interval > 0 and i < args.count:
                    time.sleep(args.interval)
    finally:
        record["finished_at"] = utc_iso(time.time())
        save(run_file, record)
        try:
            credential.close()
        except Exception:
            pass
    print(f"Done: {record['count_sent']} sent, {record['count_failed']} failed. Saved {run_file}")


if __name__ == "__main__":
    main()
