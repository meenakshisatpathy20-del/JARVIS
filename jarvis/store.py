"""In-memory ring buffer of recent events + alert dedup by fingerprint."""
import hashlib
import threading
from collections import deque

from .common import is_bad, parse_ts, scrub

SCHEMA_FIELDS = ["ts", "source", "service", "kind", "level", "route", "status", "latency_ms",
                 "dependency", "error_type", "trace_id", "user_id", "amount_inr", "message"]


def normalize(raw: dict, source: str, now: float, slow_ms: float) -> dict:
    ev = {k: raw.get(k) for k in SCHEMA_FIELDS}
    for k, v in raw.items():          # keep extra numeric fields such as conn_used
        if k not in ev and isinstance(v, (int, float, bool)):
            ev[k] = v
    ev["source"] = raw.get("source") or source
    ev["service"] = (raw.get("service") or "unknown").strip()
    ev["kind"] = raw.get("kind") or "log"
    ev["level"] = (raw.get("level") or "INFO").upper()
    t = parse_ts(raw.get("ts"))
    ev["_t"] = t if t is not None else now
    if ev["ts"] is None:
        from .common import iso
        ev["ts"] = iso(ev["_t"])
    for k in ("status",):
        try:
            ev[k] = int(ev[k]) if ev[k] is not None else None
        except (TypeError, ValueError):
            ev[k] = None
    for k in ("latency_ms", "amount_inr"):
        try:
            ev[k] = float(ev[k]) if ev[k] is not None else None
        except (TypeError, ValueError):
            ev[k] = None
    ev["message"] = scrub(str(ev["message"])[:500]) if ev["message"] is not None else ""
    if ev.get("dependency") == "":
        ev["dependency"] = None
    ev["_bad"] = is_bad(ev, slow_ms)
    return ev


def fingerprint(ev: dict) -> str:
    key = "|".join(str(ev.get(k)) for k in ("service", "kind", "error_type", "route", "status"))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


class Store:
    def __init__(self, cfg: dict):
        self.retention = cfg.get("event_retention_s", 900)
        self.dedup_window = cfg.get("dedup_window_s", 60)
        self.events: deque = deque()
        self.alerts: deque = deque()     # each: {fp, service, ..., first_t, last_t, count}
        self._last_alert: dict = {}
        self.total_raw = 0
        self.lock = threading.RLock()

    def add(self, ev: dict):
        with self.lock:
            self.events.append(ev)
            self.total_raw += 1
            if ev["_bad"]:
                fp = fingerprint(ev)
                a = self._last_alert.get(fp)
                if a and ev["_t"] - a["last_t"] <= self.dedup_window:
                    a["count"] += 1
                    a["last_t"] = max(a["last_t"], ev["_t"])
                else:
                    a = {"fp": fp, "service": ev["service"], "kind": ev["kind"], "error_type": ev.get("error_type"),
                         "route": ev.get("route"), "status": ev.get("status"), "message": ev.get("message"),
                         "first_t": ev["_t"], "last_t": ev["_t"], "count": 1}
                    self._last_alert[fp] = a
                    self.alerts.append(a)

    def prune(self, now: float):
        cutoff = now - self.retention
        with self.lock:
            while self.events and self.events[0]["_t"] < cutoff:
                self.events.popleft()
            while self.alerts and self.alerts[0]["last_t"] < cutoff:
                a = self.alerts.popleft()
                if self._last_alert.get(a["fp"]) is a:
                    del self._last_alert[a["fp"]]

    def since(self, t0: float, bad_only: bool = False) -> list:
        out = []
        with self.lock:
            for ev in reversed(self.events):
                if ev["_t"] < t0:
                    break
                if not bad_only or ev["_bad"]:
                    out.append(ev)
        out.reverse()
        return out

    def funnel(self, now: float, window: float = 300) -> dict:
        t0 = now - window
        with self.lock:
            raw = sum(1 for ev in reversed(self.events) if ev["_t"] >= t0 and ev["_bad"])
            alerts = sum(1 for a in self.alerts if a["first_t"] >= t0)
        return {"raw": raw, "alerts": alerts}
