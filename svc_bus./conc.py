import os
import json
import hashlib
import logging
from azure.servicebus import ServiceBusClient, AutoLockRenewer, ServiceBusMessage, ServiceBusReceiveMode
from azure.servicebus.management import ServiceBusAdministrationClient
import asyncio
from bz5_commons.interfaces.authentication_interface import (
    get_credential
)
from bz5_commons.logging.logger import get_logger, set_request_guid, clear_request_guid

logger = get_logger(__name__)

def generate_message_id_from_message_body(message_body):
    """Generate deterministic hash-based message ID."""
    if isinstance(message_body, dict):
        normalized = json.dumps(message_body, sort_keys=True, separators=(',', ':'))
    else:
        normalized = str(message_body)
    return hashlib.sha256(normalized.encode()).hexdigest()

def extract_message_body(message):
    body = message.body 
    if isinstance(body, bytes):
        return body.decode("utf-8")
    elif isinstance(body, str):
        return body
    elif hasattr(body, "__iter__") and not isinstance(body, (bytes, str)):
        message_body_bytes = b"".join(chunk for chunk in body)
        return message_body_bytes.decode("utf-8")
    elif isinstance(body, dict):
        return json.dumps(body)
    else:
        return str(body)

def extract_job_id(message_body):
    """Extract job_id from message body if possible."""
    try:
        message_data = json.loads(message_body)
        if isinstance(message_data, dict):
            return message_data.get("job_id", "no_job_id")
    except Exception as e:
        logger.error(f"Failed to extract job_id from message body: {e}")
    return "no_job_id"

def send_message_to_queue(
    message_body,
    message_id=None,
    out_queue=None,
    namespace=None,
    credentials=None):

    out_queue = out_queue or os.getenv("APP_SERVICE_OUT_QUEUE")
    namespace = namespace or os.getenv("APP_SB_FULLY_QUALIFIED_NAMESPACE")
    created_credential = False
    if credentials is None:
        credentials = get_credential()
        created_credential = True

    # Prepare message body and ID
    if isinstance(message_body, str):
        message_body_str = message_body
    else:
        message_body_str = json.dumps(message_body)
    message_id = message_id or generate_message_id_from_message_body(message_body_str)
    job_id = extract_job_id(message_body_str)
    set_request_guid(job_id)

    servicebus_client = ServiceBusClient(namespace, credentials)
    with servicebus_client:
        sender = servicebus_client.get_queue_sender(queue_name=out_queue)
        with sender:
            try:
                message = ServiceBusMessage(body=message_body_str, message_id=message_id)
                sender.send_messages(message)
                logger.info(f"sent message with message_id:{message_id} for job_id:{job_id} to the out_queue: {out_queue}")
                return True
            except Exception as e:
                logger.error(f"Failed to send message with message_id:{message_id} for job_id:{job_id} to the out_queue: {out_queue} due to exception: {e}")
                return False
    if created_credential:
        credentials.close()

async def process_message(message_body, processor_function):
    try:
        return await processor_function(message_body)
    except Exception as e:
        logger.error(
            f"Error processing message: {e}",
            exc_info=True
        )
        return None

# ===========================================================================
# CONCURRENCY - everything below replaces listen() from svc_bus.py.
# Everything above this line is unchanged from svc_bus.py.
# ===========================================================================
#
# THE IDEA
#     Run N copies ("workers") of the original listen loop side by side.
#     A worker does exactly what svc_bus.py did, one message at a time:
#         receive 1 message -> process it -> send the result -> complete it -> repeat
#     Each worker has its OWN client, receiver and lock renewer, so workers never
#     share anything and cannot get in each other's way.
#
#     R1  N workers, 1 message each      -> at most N messages in flight per pod
#     R2  each worker sends + completes its own message as soon as it is done
#     R3  a worker that finishes immediately receives the next message
#     R4  each message is registered with its worker's AutoLockRenewer
#     R5  workers finish in any order    -> results are not ordered
#
# THE GOLDEN RULE: NOTHING IS EVER RAISED, NOTHING EVER STOPS THE LISTENER
#     There is no `raise` anywhere in this section. Every step that can fail is
#     wrapped in try/except, the failure is logged, and the code decides what to
#     do with the message using plain if/else. On top of that, two safety nets
#     catch anything nobody thought of:
#       - handle_message() catches any unexpected error -> the worker moves on
#         to the next message.
#       - worker() catches any unexpected error (e.g. the receiver could not
#         connect) -> its `with` blocks close everything, it waits
#         RETRY_SECONDS and starts again with a fresh client and receiver.
#     So a worker can never die, and listen() runs forever.
#     (Only bad configuration at startup, e.g. a missing namespace or an
#     invalid max_lock_renewal_duration, still fails immediately, exactly like
#     svc_bus.py. That is on purpose, so a broken deploy is noticed at once.)
#
# WHAT HAPPENS TO A MESSAGE IN EACH SITUATION
#     | Situation                                  | What we do                                |
#     |--------------------------------------------|-------------------------------------------|
#     | Normal                                     | send result, complete                     |
#     | processor_function fails                   | same as svc_bus.py: whatever it returned  |
#     |   (it has its own try/except + retries)    |   is sent, message completed, no retry    |
#     | Body cannot be read (e.g. not UTF-8)       | dead-letter (no job_id to report anyway)  |
#     | Sending the result fails (after the SDK's  | dead-letter: no re-run of the job, the    |
#     |   own 4 attempts, about 11 s)              |   message is kept so it can be resubmitted|
#     | Complete fails after the result was sent   | log only. The job worked; Service Bus     |
#     |   (after the SDK's own 4 attempts)         |   redelivers it later, dedup drops the    |
#     |                                            |   duplicate result (see NOTE below)       |
#     | Receiving fails (network, throttling, ...) | log, wait RETRY_SECONDS, receive again    |
#     | Anything unexpected                        | log with traceback, carry on              |
#
#     NOTE on max_lock_renewal_duration: while a message is not completed, its
#     lock keeps being renewed for up to max_lock_renewal_duration seconds. Keep
#     max_lock_renewal_duration + the queue's Lock Duration below your 10 minute
#     dedup window (e.g. 300 s), so a redelivered duplicate is always dropped.
#
# WHY "asyncio.to_thread" (inside run_in_thread)
#     The Service Bus SDK is sync: receive_messages() waits up to 5 seconds for
#     a message, complete/send wait for the network. If those calls ran directly,
#     ALL workers would freeze while one of them waits. asyncio.to_thread runs
#     the call on a background thread, so the other workers keep going.
#     processor_function is async, so `await process_message(...)` does not
#     freeze anyone as long as everything inside it is async too (AsyncOpenAI,
#     asyncio.sleep). A blocking call inside it (time.sleep, requests, the sync
#     OpenAI client) WOULD freeze all workers.
# ===========================================================================

# Number of messages processed at the same time by one pod (R1).
MAX_IN_FLIGHT = int(os.getenv("APP_SB_MAX_IN_FLIGHT", "5"))

# How long to wait before trying again after a receive error or an unexpected error.
RETRY_SECONDS = 5


# ===========================================================================
# METRICS LOGS (new in v5) - everything else is the same as svc_bus_v4.py
# ===========================================================================
#
# Every metric is ONE log line:   SBMETRICS {"event": "...", ...json...}
# To get a table: keep the lines containing "SBMETRICS ", take the text after
# it, json.loads() it. (parse_sb_metrics.py next to this file does that and
# writes CSVs.) Every line has: event, ts (UTC ISO), ts_epoch, pod.
#
# Events:
#   listener_started   once at startup, with the config
#   message_accepted   a worker took a message (before processing)
#   message_processed  processor_function returned
#   message_sent       result sent to the out queue (or send failed)
#   message_summary    ONE ROW PER MESSAGE with all timestamps + durations +
#                      outcome. This is the line to put in Excel.
#   monitor            every APP_SB_MONITOR_SECONDS: in_flight, counters,
#                      memory of this process and of this container, threads,
#                      event-loop lag.
#
# Memory fields (monitor):
#   rss_mb                    this Python process now (all workers together)
#   peak_rss_mb               highest RSS since start (exact, no sampling gap)
#   container_mb              this container's cgroup memory (incl. file cache)
#   container_working_set_mb  container_mb minus inactive file cache; this is
#                             what `kubectl top` / Azure Monitor show
#   container_peak_mb         highest container memory (cgroup v2, newer kernels)
#   container_limit_mb        this container's memory limit (None = no limit)
#   The proxy sidecar is a different container, so it is never included.
#
# Logging never raises: a failure to build a metric line is ignored.

import socket
import threading
import time
from datetime import datetime, timezone

try:
    import psutil                       # optional; /proc is used if missing
    _PROCESS = psutil.Process()
except Exception:
    psutil = None
    _PROCESS = None

try:
    import resource                     # Linux/macOS only (peak RSS)
except Exception:
    resource = None

METRICS_PREFIX = "SBMETRICS"
POD_NAME = os.getenv("HOSTNAME") or socket.gethostname()
# Seconds between `monitor` lines. 0 turns the monitor off.
MONITOR_SECONDS = float(os.getenv("APP_SB_MONITOR_SECONDS", "30"))

# Counters for this process. Only touched from the event loop thread, so no lock needed.
COUNTERS = {
    "in_flight": 0,
    "peak_in_flight": 0,
    "accepted": 0,
    "processed": 0,
    "sent": 0,
    "send_failed": 0,
    "completed": 0,
    "complete_failed": 0,
    "dead_lettered": 0,
    "unexpected_errors": 0,
}


def _utc_iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="milliseconds")


def log_metric(event, **fields):
    """Write one SBMETRICS JSON line. NEVER RAISES."""
    try:
        now = time.time()
        record = {"event": event, "ts": _utc_iso(now), "ts_epoch": round(now, 3), "pod": POD_NAME}
        record.update(fields)
        logger.info(f"{METRICS_PREFIX} {json.dumps(record, default=str, separators=(',', ':'))}")
    except Exception:
        pass


def _mb(num_bytes):
    return None if num_bytes is None else round(num_bytes / (1024 * 1024), 1)


def _read_cgroup_value(path):
    try:
        with open(path) as f:
            value = f.read().strip()
        if value == "max":
            return None
        value = int(value)
        return None if value >= 2 ** 60 else value   # cgroup v1 "no limit" is a huge number
    except Exception:
        return None


def _read_cgroup_stat(path, key):
    try:
        with open(path) as f:
            for line in f:
                name, _, value = line.partition(" ")
                if name == key:
                    return int(value)
    except Exception:
        pass
    return None


def container_memory():
    """Memory of THIS container (its cgroup), in bytes. Values are None if not on Linux."""
    if os.path.exists("/sys/fs/cgroup/memory.current"):             # cgroup v2 (AKS default)
        base = "/sys/fs/cgroup/"
        current = _read_cgroup_value(base + "memory.current")
        limit = _read_cgroup_value(base + "memory.max")
        peak = _read_cgroup_value(base + "memory.peak")
        inactive_file = _read_cgroup_stat(base + "memory.stat", "inactive_file")
    else:                                                            # cgroup v1
        base = "/sys/fs/cgroup/memory/"
        current = _read_cgroup_value(base + "memory.usage_in_bytes")
        limit = _read_cgroup_value(base + "memory.limit_in_bytes")
        peak = _read_cgroup_value(base + "memory.max_usage_in_bytes")
        inactive_file = _read_cgroup_stat(base + "memory.stat", "total_inactive_file")
    working_set = None
    if current is not None:
        working_set = max(current - (inactive_file or 0), 0)
    return current, working_set, peak, limit


def process_memory():
    """(rss_bytes, peak_rss_bytes) of this Python process. Values are None if unknown."""
    rss = None
    if _PROCESS is not None:
        try:
            rss = _PROCESS.memory_info().rss
        except Exception:
            rss = None
    if rss is None:
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) * 1024
                        break
        except Exception:
            rss = None
    peak = None
    if resource is not None:
        try:
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024   # KB on Linux
        except Exception:
            peak = None
    return rss, peak


def _cpu_percent():
    if _PROCESS is None:
        return None
    try:
        return _PROCESS.cpu_percent(None)    # % since the previous call (100 = one full core)
    except Exception:
        return None


async def monitor(max_in_flight):
    """Log a `monitor` line every MONITOR_SECONDS. Runs forever. NEVER RAISES."""
    _cpu_percent()   # first call only starts the measurement
    while True:
        expected = time.monotonic() + MONITOR_SECONDS
        await asyncio.sleep(MONITOR_SECONDS)
        try:
            # How late did we wake up? A big number means something blocked the
            # event loop (e.g. a sync call inside processor_function).
            loop_lag_ms = round(max(time.monotonic() - expected, 0) * 1000, 1)
            rss, peak_rss = process_memory()
            current, working_set, peak, limit = container_memory()
            log_metric(
                "monitor",
                max_in_flight=max_in_flight,
                **COUNTERS,
                rss_mb=_mb(rss),
                peak_rss_mb=_mb(peak_rss),
                container_mb=_mb(current),
                container_working_set_mb=_mb(working_set),
                container_peak_mb=_mb(peak),
                container_limit_mb=_mb(limit),
                cpu_percent=_cpu_percent(),
                threads=threading.active_count(),
                loop_lag_ms=loop_lag_ms,
            )
        except Exception:
            pass


def _message_info(message):
    """Extra info from the received message, for the logs. NEVER RAISES."""
    info = {"delivery_count": None, "enqueued_at": None, "enqueued_epoch": None, "test_run_id": None}
    try:
        info["delivery_count"] = getattr(message, "delivery_count", None)
        enqueued = getattr(message, "enqueued_time_utc", None)
        if enqueued is not None:
            if enqueued.tzinfo is None:
                enqueued = enqueued.replace(tzinfo=timezone.utc)
            info["enqueued_at"] = enqueued.isoformat(timespec="milliseconds")
            info["enqueued_epoch"] = round(enqueued.timestamp(), 3)
        props = getattr(message, "application_properties", None) or {}
        for key, value in props.items():
            key = key.decode() if isinstance(key, bytes) else key
            if key == "test_run_id":
                info["test_run_id"] = value.decode() if isinstance(value, bytes) else value
    except Exception:
        pass
    return info


def _out_message_id(processed_message_body):
    """The message_id send_message_to_queue will give the result (same calculation)."""
    try:
        body = processed_message_body if isinstance(processed_message_body, str) else json.dumps(processed_message_body)
        return generate_message_id_from_message_body(body)
    except Exception:
        return None


async def run_in_thread(description, func, *args, **kwargs):
    """Run a blocking (sync) Service Bus call on a background thread.

    NEVER RAISES. Returns a pair:
        (True, <what func returned>)   if the call worked
        (False, None)                  if it failed; the error is logged here
    The caller then decides what to do with a simple ``if``.

    Note: the SDK already retries network problems inside these calls (up to 4
    attempts, about 11 s), so a failure here means the problem lasted longer.
    """
    try:
        result = await asyncio.to_thread(func, *args, **kwargs)
        return True, result
    except Exception as error:
        logger.error(f"{description} failed: {error!r}")
        return False, None


async def handle_message(
    worker_id,
    message,
    receiver,
    renewer,
    processor_function,
    out_queue,
    namespace,
    credentials,
    max_lock_renewal_duration,
    send_to_out_queue_immediately_after_processing_message,
):
    """Take ONE received message from start to finish. NEVER RAISES.

    Steps: read body -> start lock renewal -> process -> send result -> complete.
    Each step either works, or the failure is logged and the message is handled
    as described in the table at the top of this section.

    v5: also writes SBMETRICS lines (message_accepted, message_processed,
    message_sent) and, in `finally`, ONE message_summary line per message.
    """
    tag = f"[worker {worker_id}]"
    message_id = message.message_id
    job_id = "no_job_id"

    # --- v5 metrics: everything known about this message ends up in `row` ---
    accepted_epoch = time.time()
    COUNTERS["in_flight"] += 1
    COUNTERS["accepted"] += 1
    COUNTERS["peak_in_flight"] = max(COUNTERS["peak_in_flight"], COUNTERS["in_flight"])
    row = {
        "worker_id": worker_id,
        "message_id": message_id,
        "job_id": job_id,
        **_message_info(message),
        "accepted_at": _utc_iso(accepted_epoch),
        "accepted_epoch": round(accepted_epoch, 3),
        "processed_at": None, "processed_epoch": None,
        "sent_at": None, "sent_epoch": None, "out_message_id": None,
        "settled_at": None, "settled_epoch": None,
        "outcome": "unknown",   # completed | complete_failed | dead_lettered_* | unexpected_error
    }

    def mark(name):
        """Record now as <name>_at / <name>_epoch in row; return the epoch."""
        epoch = time.time()
        row[f"{name}_at"] = _utc_iso(epoch)
        row[f"{name}_epoch"] = round(epoch, 3)
        return epoch

    try:
        # --- Step 1: read the body -------------------------------------------
        # In svc_bus.py this was outside the try, so a message whose body is
        # not valid UTF-8 stopped the whole listener.
        try:
            message_body = extract_message_body(message)
        except Exception as read_error:
            logger.error(f"{tag} Could not read body of message_id:{message_id}: {read_error!r}. Dead-lettering it.")
            await run_in_thread(
                f"{tag} Dead-letter of unreadable message_id:{message_id}",
                receiver.dead_letter_message, message,
                reason="unreadable_message_body", error_description=repr(read_error)[:1000],
            )
            row["outcome"] = "dead_lettered_unreadable"
            COUNTERS["dead_lettered"] += 1
            mark("settled")
            return

        job_id = extract_job_id(message_body)  # never raises (has its own try/except)
        row["job_id"] = job_id
        # Safe with several workers: each worker has its own copy of the GUID
        # (contextvar), so log lines never show another worker's job_id.
        set_request_guid(job_id)
        logger.info(f"{tag} Received message with message_id:{message_id} for job_id:{job_id}")
        log_metric(
            "message_accepted",
            worker_id=worker_id, message_id=message_id, job_id=job_id,
            delivery_count=row["delivery_count"], test_run_id=row["test_run_id"],
            in_flight=COUNTERS["in_flight"],
        )

        # --- Step 2: start lock renewal ---------------------------------------
        # The renewer keeps this message's lock alive on its own background
        # thread until we complete or dead-letter it (or until
        # max_lock_renewal_duration). If this ever failed we still go on: the
        # job can still finish within the queue's normal lock duration.
        try:
            renewer.register(receiver, message, max_lock_renewal_duration=max_lock_renewal_duration)
        except Exception as register_error:
            logger.warning(f"{tag} Could not start lock renewal for message_id:{message_id} for job_id:{job_id}: {register_error!r}. Continuing.")

        # --- Step 3: process ---------------------------------------------------
        # Same as svc_bus.py: process_message never raises. If processor_function
        # raises, it logs it and returns None. Your processor_function catches its
        # own errors, does its own retries and always returns a dict.
        logger.info(f"{tag} Processing message with message_id:{message_id} for job_id:{job_id}")
        processing_started = time.time()
        processed_message_body = await process_message(message_body, processor_function)
        processed_epoch = mark("processed")
        COUNTERS["processed"] += 1
        logger.info(f"{tag} Processed message with message_id:{message_id} for job_id:{job_id}")
        log_metric(
            "message_processed",
            worker_id=worker_id, message_id=message_id, job_id=job_id,
            processing_seconds=round(processed_epoch - processing_started, 3),
            processor_returned_none=processed_message_body is None,
            in_flight=COUNTERS["in_flight"],
        )

        # --- Step 4: send the result to the out queue ------------------------
        if send_to_out_queue_immediately_after_processing_message:
            row["out_message_id"] = _out_message_id(processed_message_body)
            send_started = time.time()
            call_worked, sent = await run_in_thread(
                f"{tag} Sending result for job_id:{job_id}",
                send_message_to_queue,
                message_body=processed_message_body,
                namespace=namespace,
                out_queue=out_queue,
                credentials=credentials,
            )
            send_ok = bool(call_worked and sent)
            if send_ok:
                sent_epoch = mark("sent")
                COUNTERS["sent"] += 1
            else:
                sent_epoch = time.time()
                COUNTERS["send_failed"] += 1
            log_metric(
                "message_sent",
                worker_id=worker_id, message_id=message_id, job_id=job_id,
                out_message_id=row["out_message_id"], send_ok=send_ok,
                send_seconds=round(sent_epoch - send_started, 3),
                in_flight=COUNTERS["in_flight"],
            )
            # call_worked False -> send_message_to_queue raised (e.g. it could not
            #                      open the connection to the out queue)
            # sent False        -> send_message_to_queue caught the error itself
            # In both cases the SDK has already retried. In svc_bus.py the first
            # case re-ran the whole job and the second lost the result silently.
            if not send_ok:
                logger.error(f"{tag} Result for job_id:{job_id} could NOT be sent to out_queue: {out_queue}. Dead-lettering message_id:{message_id} so it can be resubmitted.")
                await run_in_thread(
                    f"{tag} Dead-letter of message_id:{message_id} for job_id:{job_id}",
                    receiver.dead_letter_message, message,
                    reason="send_to_out_queue_failed",
                    error_description=f"result for job_id:{job_id} could not be sent to {out_queue}",
                )
                row["outcome"] = "dead_lettered_send_failed"
                COUNTERS["dead_lettered"] += 1
                mark("settled")
                return

        # --- Step 5: complete the input message -------------------------------
        completed, _ = await run_in_thread(
            f"{tag} Complete of message_id:{message_id} for job_id:{job_id}",
            receiver.complete_message, message,
        )
        mark("settled")
        if completed:
            row["outcome"] = "completed"
            COUNTERS["completed"] += 1
            logger.info(f"{tag} Completed message with message_id:{message_id} for job_id:{job_id}")
        else:
            row["outcome"] = "complete_failed"
            COUNTERS["complete_failed"] += 1
            # The job worked and its result is already in the out queue, so we do
            # NOT dead-letter or abandon. Service Bus will hand this message out
            # again once its lock runs out; the re-run sends the same result,
            # which the out queue's duplicate detection drops.
            logger.warning(f"{tag} Result for job_id:{job_id} was sent but message_id:{message_id} could not be completed. It will be redelivered later; the duplicate result will be dropped by dedup.")

    except Exception as unexpected_error:
        # Safety net: a bug or an error nobody expected. Log it and carry on with
        # the next message. If this message was not settled, Service Bus delivers
        # it again after its lock runs out.
        row["outcome"] = "unexpected_error"
        row["error"] = repr(unexpected_error)[:500]
        COUNTERS["unexpected_errors"] += 1
        logger.error(f"{tag} Unexpected error while handling message_id:{message_id} for job_id:{job_id}: {unexpected_error!r}", exc_info=True)
    finally:
        COUNTERS["in_flight"] -= 1
        # ONE row per message with every timestamp and the durations between them.
        try:
            def between(start, end):
                a, b = row.get(f"{start}_epoch"), row.get(f"{end}_epoch")
                return None if a is None or b is None else round(b - a, 3)
            row["queue_wait_seconds"] = between("enqueued", "accepted")      # waited in the in queue
            row["processing_seconds"] = between("accepted", "processed")     # body read + processor_function
            row["send_seconds"] = between("processed", "sent")               # send to the out queue
            row["total_seconds"] = between("accepted", "settled")            # accepted -> completed/dead-lettered
            row["end_to_end_seconds"] = between("enqueued", "settled")       # sender enqueued -> done
            row["in_flight_after"] = COUNTERS["in_flight"]
            log_metric("message_summary", **row)
        except Exception:
            pass
        clear_request_guid()


async def worker(
    worker_id,
    processor_function,
    in_queue,
    out_queue,
    namespace,
    credentials,
    max_lock_renewal_duration,
    send_to_out_queue_immediately_after_processing_message,
):
    """One worker = the original svc_bus.py listen loop. Runs forever. NEVER RAISES."""
    tag = f"[worker {worker_id}]"

    # The outer loop only goes round again if something unexpected broke this
    # worker. The `with` blocks then close the client, receiver and renewer,
    # and after RETRY_SECONDS the worker starts over with fresh ones.
    while True:
        try:
            # Same `with` blocks as svc_bus.py, kept on purpose because they are
            # proven in production.
            #
            # One thing to know: entering `with receiver` connects to Azure right
            # there, on the main thread, so the other workers pause for that
            # moment. Normally that is a fraction of a second, once per worker at
            # startup (or after a rare worker restart). If connecting fails, the
            # safety net below catches it and this worker simply tries again.
            servicebus_client = ServiceBusClient(namespace, credentials)
            with servicebus_client:
                receiver = servicebus_client.get_queue_receiver(
                    queue_name=in_queue,
                    receive_mode=ServiceBusReceiveMode.PEEK_LOCK
                )
                with receiver, AutoLockRenewer() as renewer:
                    while True:
                        logger.info(f"{tag} Waiting for jobs to be received in in_queue : {in_queue}")
                        # If receiving fails (network down, throttling, token problem)
                        # even after the SDK's own retries, svc_bus.py stopped the
                        # listener. Here we log it, wait, and receive again. The
                        # receiver reconnects by itself on the next call.
                        received_ok, messages = await run_in_thread(
                            f"{tag} Receive from in_queue: {in_queue}",
                            receiver.receive_messages, max_message_count=1, max_wait_time=5,
                        )
                        if not received_ok:
                            await asyncio.sleep(RETRY_SECONDS)
                            continue

                        for message in messages:
                            await handle_message(   # never raises
                                worker_id, message, receiver, renewer, processor_function,
                                out_queue, namespace, credentials, max_lock_renewal_duration,
                                send_to_out_queue_immediately_after_processing_message,
                            )

        except Exception as unexpected_error:
            # Safety net: should never happen, but if it does (e.g. connecting the
            # receiver failed) the worker restarts instead of dying. By the time
            # we get here the `with` blocks have already closed everything.
            logger.error(f"{tag} Unexpected error, restarting this worker in {RETRY_SECONDS} seconds: {unexpected_error!r}", exc_info=True)

        await asyncio.sleep(RETRY_SECONDS)


async def listen(
    processor_function,
    in_queue,
    out_queue,
    namespace,
    max_lock_renewal_duration=3000,
    send_to_out_queue_immediately_after_processing_message=True,
    max_in_flight=None,
):
    """
    Listen on the Azure Service Bus input queue and process up to ``max_in_flight``
    messages at the same time (default: APP_SB_MAX_IN_FLIGHT, 5). Runs forever.

    Same arguments as svc_bus.py plus the optional ``max_in_flight``. It starts
    ``max_in_flight`` workers (see ``worker``). Workers never raise, so this
    function never stops once it has started.
    """

    # max_lock_renewal_duration is mandatory to be integer for AutoLockRenewer , otherwise it will set it something random and will fail
    max_lock_renewal_duration = int(max_lock_renewal_duration)
    max_in_flight = int(max_in_flight or MAX_IN_FLIGHT)

    credentials = get_credential()
    workers = [
        worker(
            worker_id,
            processor_function,
            in_queue,
            out_queue,
            namespace,
            credentials,
            max_lock_renewal_duration,
            send_to_out_queue_immediately_after_processing_message,
        )
        for worker_id in range(1, max_in_flight + 1)
    ]
    # v5: one startup line + the monitor task (never raises) next to the workers.
    rss, _ = process_memory()
    current, working_set, _, limit = container_memory()
    log_metric(
        "listener_started",
        max_in_flight=max_in_flight, in_queue=in_queue, out_queue=out_queue,
        max_lock_renewal_duration=max_lock_renewal_duration,
        monitor_seconds=MONITOR_SECONDS, psutil_available=psutil is not None,
        rss_mb=_mb(rss), container_mb=_mb(current),
        container_working_set_mb=_mb(working_set), container_limit_mb=_mb(limit),
    )
    if MONITOR_SECONDS > 0:
        workers.append(monitor(max_in_flight))
    # Run all workers at the same time. They loop forever and never raise.
    await asyncio.gather(*workers)
    credentials.close()  # not reached in practice, same as svc_bus.py


async def check_service_bus_health() -> tuple[str, dict]:
    """Check the health of Azure Service Bus.

    Returns:
        tuple[str, dict]: A tuple containing health status ('healthy' or 'unhealthy')
        and a dictionary with details about the health check.
    """

    service_bus_namespace = os.getenv("APP_SB_FULLY_QUALIFIED_NAMESPACE")

    # get a queue to test from whichever is available among in/out queue
    if os.getenv("APP_SERVICE_IN_QUEUE"):
        queue_name = os.getenv("APP_SERVICE_IN_QUEUE")
    elif os.getenv("APP_SERVICE_OUT_QUEUE"):
        queue_name = os.getenv("APP_SERVICE_OUT_QUEUE")
    else:
        queue_name = ""

    if not service_bus_namespace or not queue_name:
        return "unhealthy", {
            "error": """Missing APP_SB_FULLY_QUALIFIED_NAMESPACE or
            APP_SERVICE_IN_QUEUE or APP_SERVICE_OUT_QUEUE"""
        }

    credential = get_credential()
    admin_client = None

    try:
        admin_client = ServiceBusAdministrationClient(service_bus_namespace, credential)
        await asyncio.to_thread(admin_client.get_queue, queue_name)
        return "healthy", {"queue": queue_name}
    except Exception as e:
        return "unhealthy", {"error": str(e)}
    finally:
        if admin_client:
            await asyncio.to_thread(admin_client.close)
        if credential:
            await asyncio.to_thread(credential.close)


# Start of ------ Example usage for listen loop implementation ------
async def example_processor_function(message_data):
    await asyncio.sleep(1)
    return {"processed": "xkcd" + str(message_data)}


async def main():
    await listen(
        processor_function=example_processor_function,
        send_to_out_queue_immediately_after_processing_message=True,
        in_queue=os.getenv("IN_QUEUE_NAME"),
        out_queue=os.getenv("OUT_QUEUE_NAME"),
        namespace=os.getenv("FULLY_QUALIFIED_NAMESPACE"),
        max_lock_renewal_duration=os.getenv("MAX_LOCK_RENEWAL_DURATION")
    )
if __name__ == "__main__":
    asyncio.run(main())


# End of ------ Example usage for listen loop implementation ------
