"""Anomaly detection: per-service 1-second buckets, hard rules + z-score, 3-second runs."""
import math
from collections import defaultdict


class Agg:
    __slots__ = ("n", "err", "lats", "health", "conn", "pg_ok", "inbound", "alert")

    def __init__(self):
        self.n = 0
        self.err = 0
        self.lats = []
        self.health = None
        self.conn = None
        self.pg_ok = None
        self.inbound = 0
        self.alert = None


def p95(values):
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(math.ceil(0.95 * len(v))) - 1)]


class Detector:
    def __init__(self, cfg: dict, start_t: float):
        self.cfg = cfg
        d = cfg["detection"]
        self.d = d
        self.services = list(cfg["services"])
        self.graph = cfg["graph"]
        self.start_t = start_t
        self.sec = defaultdict(lambda: defaultdict(Agg))
        self.state = {}
        self.last_eval = int(start_t) - 1
        self.last_health = {}
        self.last_conn = None
        self.last_pg_ok = None
        self.last_alert = {}
        self.recent_cleared = {}

    # ------------------------------------------------------------- ingest
    def ingest(self, ev: dict):
        svc, s, kind = ev["service"], int(ev["_t"]), ev["kind"]
        if svc not in self.services:
            self.services.append(svc)
        a = self.sec[svc][s]
        if kind in ("request", "log") and (ev.get("status") is not None or ev["_bad"]):
            a.n += 1
            if (ev.get("status") or 0) >= 500 or ev["level"] in ("ERROR", "FATAL", "CRITICAL"):
                a.err += 1
            if ev.get("latency_ms") is not None:
                a.lats.append(ev["latency_ms"])
            if svc == "gateway" and ev.get("dependency"):
                self.sec[ev["dependency"]][s].inbound += 1
        elif kind == "health":
            a.health = ev.get("status") == 200
        elif kind == "metric" and svc == "user-db":
            a.pg_ok = bool(ev.get("pg_ok"))
            if ev.get("conn_used") is not None:
                a.conn = ev["conn_used"]
        elif kind == "alert":
            a.alert = ev.get("error_type") or ev.get("message") or "alert"

    # ------------------------------------------------------------- rules
    def _window(self, svc, t, w):
        n = err = inbound = 0
        lats = []
        for s in range(t - w + 1, t + 1):
            a = self.sec[svc].get(s)
            if a:
                n += a.n
                err += a.err
                lats.extend(a.lats)
                inbound += a.inbound
        return n, err, lats, inbound

    def _baseline(self, svc, t):
        d = self.d
        errs, p95s = [], []
        for s in range(t - d["baseline_skip_s"] - d["baseline_s"], t - d["baseline_skip_s"]):
            a = self.sec[svc].get(s)
            if a and a.n:
                errs.append(a.err / a.n)
                if a.lats:
                    p95s.append(p95(a.lats))
        return errs, p95s

    @staticmethod
    def _z(x, xs, floor):
        m = sum(xs) / len(xs)
        sd = math.sqrt(sum((v - m) ** 2 for v in xs) / len(xs))
        return (x - m) / max(sd, floor), m

    def _rules(self, svc, t):
        d = self.d
        w = d["window_s"]
        reasons, codes, metrics = [], set(), {}
        n, err, lats, _ = self._window(svc, t, w)
        rate = err / n if n else None
        q = p95(lats)
        metrics.update(rps=round(n / w, 1), err_rate=rate, p95=q)
        if n and err >= 2 and rate > d["error_rate"]:
            reasons.append(f"error rate {rate:.0%} over the last {w} s ({err}/{n} requests)")
            codes.add("err")
        if q is not None and len(lats) >= 2 and q > d["p95_ms"]:
            reasons.append(f"p95 latency {q:,.0f} ms (limit {d['p95_ms']:,} ms)")
            codes.add("p95")
            metrics["peak_p95"] = q
        # health (polled every 2 s; keep last known value)
        for s in range(t - 2, t + 1):
            a = self.sec[svc].get(s)
            if a and a.health is not None:
                self.last_health[svc] = a.health
        if self.last_health.get(svc) is False:
            reasons.append("health check failing")
            codes.add("health")
        metrics["health"] = self.last_health.get(svc)
        # silent while the gateway still sends it traffic
        if svc not in ("gateway", "user-db"):
            sn, _, _, sin = self._window(svc, t, d["silent_s"])
            if sn == 0 and sin > 0:
                reasons.append(f"silent for {d['silent_s']} s while the gateway still sends it {sin} requests")
                codes.add("silent")
        # user-db: connection slots, monitor reachability
        if svc == "user-db":
            for s in range(t - 2, t + 1):
                a = self.sec[svc].get(s)
                if a and a.pg_ok is not None:
                    self.last_pg_ok = a.pg_ok
                    if a.conn is not None:
                        self.last_conn = a.conn
            slots = d["db_ordinary_slots"]
            metrics["conn_used"] = self.last_conn
            metrics["conn_limit"] = slots
            if self.last_conn is not None and self.last_conn >= slots:
                reasons.append(f"all {slots} ordinary connection slots in use ({self.last_conn}/{slots})")
                codes.add("db_full")
            if self.last_pg_ok is False:
                reasons.append("monitor cannot read Postgres stats (counted as evidence)")
                codes.add("db_unreadable")
        # external alert (webhook)
        for s in range(t - d["alert_active_s"], t + 1):
            a = self.sec[svc].get(s)
            if a and a.alert:
                self.last_alert[svc] = (s, a.alert)
        la = self.last_alert.get(svc)
        if la and t - la[0] <= d["alert_active_s"]:
            reasons.append(f"external alert firing: {la[1]}")
            codes.add("alert")
        # z-score, only after warm-up
        if t - self.start_t >= d["warmup_s"] and n:
            errs, p95s = self._baseline(svc, t)
            if len(errs) >= 30 and rate is not None:
                z, m = self._z(rate, errs, 0.02)
                if z >= d["z"] and rate - m >= 0.05 and "err" not in codes:
                    reasons.append(f"error rate {rate:.0%} is {z:.1f} sd above its baseline {m:.0%}")
                    codes.add("z_err")
            if len(p95s) >= 30 and q is not None:
                z, m = self._z(q, p95s, 50.0)
                if z >= d["z"] and q - m >= 300 and "p95" not in codes:
                    reasons.append(f"p95 latency {q:,.0f} ms is {z:.1f} sd above its baseline {m:,.0f} ms")
                    codes.add("z_p95")
                    metrics["peak_p95"] = q
        return reasons, codes, metrics

    # ------------------------------------------------------------- evaluate
    def evaluate(self, now: float):
        t_end = int(now) - 2       # give batched telemetry ~2 s to arrive
        for t in range(self.last_eval + 1, t_end + 1):
            for svc in list(self.services):
                self._step(svc, t)
        self.last_eval = max(self.last_eval, t_end)
        # prune buckets
        cutoff = t_end - self.d["baseline_s"] - self.d["baseline_skip_s"] - 120
        for svc in self.sec:
            for s in [s for s in self.sec[svc] if s < cutoff]:
                del self.sec[svc][s]

    def _step(self, svc, t):
        st = self.state.setdefault(svc, {"run": 0, "run_start": None, "anomalous": False, "onset": None,
                                          "reasons": [], "codes": set(), "healthy": 0, "metrics": {},
                                          "peak_p95": None})
        reasons, codes, metrics = self._rules(svc, t)
        st["metrics"] = metrics
        if reasons:
            st["run"] += 1
            st["healthy"] = 0
            if st["run"] == 1:
                st["run_start"] = t
            if st["anomalous"]:
                st["reasons"] = reasons
                st["codes"] |= codes
            elif st["run"] >= self.d["run_s"]:
                st.update(anomalous=True, onset=float(st["run_start"]), reasons=reasons, codes=set(codes))
            if metrics.get("peak_p95"):
                st["peak_p95"] = max(st["peak_p95"] or 0, metrics["peak_p95"])
        else:
            st["run"] = 0
            if st["anomalous"]:
                st["healthy"] += 1
                if st["healthy"] >= self.d["clear_s"]:
                    self.recent_cleared[svc] = {"cleared_t": float(t), "onset": st["onset"], "codes": set(st["codes"]),
                                                "reasons": list(st["reasons"]), "peak_p95": st["peak_p95"]}
                    st.update(anomalous=False, onset=None, reasons=[], codes=set(), peak_p95=None)

    def anomalies(self) -> dict:
        return {s: {"onset": st["onset"], "reasons": list(st["reasons"]), "codes": set(st["codes"]),
                    "inferred": False, "peak_p95": st["peak_p95"]}
                for s, st in self.state.items() if st["anomalous"]}

    def recovering(self, now: float, window: float = 60) -> dict:
        """Services that were anomalous and cleared within the last `window` seconds."""
        return {s: r for s, r in self.recent_cleared.items()
                if now - r["cleared_t"] <= window and not self.state.get(s, {}).get("anomalous")}

    def snapshot(self) -> dict:
        return {s: {"anomalous": st["anomalous"], "reasons": st["reasons"], "metrics": st["metrics"]}
                for s, st in self.state.items()}
