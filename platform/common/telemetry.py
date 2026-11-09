"""emit(event): batches telemetry events and POSTs them to J.A.R.V.I.S. /ingest every second.

Never blocks a request and never raises: if J.A.R.V.I.S. is down, events are dropped.
"""
import datetime
import os
import queue
import threading
import time
import uuid

import httpx

JARVIS_URL = os.getenv("JARVIS_URL", "http://localhost:8000")
SERVICE = os.getenv("SERVICE_NAME", "unknown")

_q: "queue.Queue[dict]" = queue.Queue(maxsize=50000)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


def emit(**fields) -> None:
    event = {
        "ts": now_iso(),
        "source": "live",
        "service": SERVICE,
        "kind": "log",
        "level": "INFO",
        "route": None,
        "status": None,
        "latency_ms": None,
        "dependency": None,
        "error_type": None,
        "trace_id": None,
        "user_id": None,
        "amount_inr": None,
        "message": "",
    }
    event.update(fields)
    try:
        _q.put_nowait(event)
    except queue.Full:
        pass


def _sender() -> None:
    client = httpx.Client(timeout=2.0)
    while True:
        time.sleep(1.0)
        batch = []
        while len(batch) < 5000:
            try:
                batch.append(_q.get_nowait())
            except queue.Empty:
                break
        if not batch:
            continue
        try:
            client.post(f"{JARVIS_URL}/ingest", json=batch)
        except Exception:
            pass  # J.A.R.V.I.S. unreachable: drop, never hurt the service


threading.Thread(target=_sender, daemon=True, name="telemetry-sender").start()
