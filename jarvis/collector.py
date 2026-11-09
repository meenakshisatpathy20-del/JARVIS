"""Collector: polls Postgres connection stats every 1 s and each /health every 2 s.

The monitor connects as a superuser so it can still read pg_stat_activity through a
reserved slot during a connection-exhaustion fault. It reads ONLY the number of client
connections: application names, query text and session IDs never reach the RCA.
"""
import os
import threading
import time

import httpx
import psycopg

from .common import iso

HEALTH_URLS = {
    "gateway": os.getenv("GATEWAY_HEALTH", "http://localhost:8001/health"),
    "auth-service": os.getenv("AUTH_HEALTH", "http://localhost:8002/health"),
    "payment-service": os.getenv("PAYMENT_HEALTH", "http://localhost:8003/health"),
}
MONITOR_DSN = os.getenv("MONITOR_DB_DSN", "host=localhost port=5432 dbname=payments user=postgres")


class Collector:
    def __init__(self, ingest):
        self.ingest = ingest
        self.conn = None

    def start(self):
        threading.Thread(target=self._pg_loop, daemon=True, name="pg-monitor").start()
        threading.Thread(target=self._health_loop, daemon=True, name="health").start()

    def _pg_count(self):
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(MONITOR_DSN, connect_timeout=2, autocommit=True,
                                        application_name="jarvis-monitor")
        n = self.conn.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' "
                              "AND pid <> pg_backend_pid()").fetchone()[0]
        return int(n)

    def _pg_loop(self):
        last = None
        while True:
            t = time.time()
            try:
                n = self._pg_count()
                last = n
                self.ingest({"ts": iso(t), "service": "user-db", "kind": "metric", "pg_ok": True,
                             "conn_used": n, "message": f"{n} client connections"})
                self.ingest({"ts": iso(t), "service": "user-db", "kind": "health", "status": 200, "message": "ok"})
            except Exception as e:
                try:
                    if self.conn:
                        self.conn.close()
                except Exception:
                    pass
                self.conn = None
                # keep the last value and count the failure itself as evidence
                self.ingest({"ts": iso(t), "service": "user-db", "kind": "metric", "pg_ok": False,
                             "conn_used": last, "message": f"monitor read failed: {type(e).__name__}"})
            time.sleep(max(0.0, 1.0 - (time.time() - t)))

    def _health_loop(self):
        client = httpx.Client(timeout=1.5)
        while True:
            t = time.time()
            for svc, url in HEALTH_URLS.items():
                try:
                    status = client.get(url).status_code
                except httpx.HTTPError:
                    status = 0
                self.ingest({"ts": iso(time.time()), "service": svc, "kind": "health", "status": status,
                             "level": "INFO" if status == 200 else "ERROR",
                             "message": "health ok" if status == 200 else "health check failed"})
            time.sleep(max(0.0, 2.0 - (time.time() - t)))
