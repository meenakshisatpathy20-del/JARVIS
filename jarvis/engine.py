"""Incident manager: turns correlation groups into incidents (merge, reopen, resolve)."""
import threading
from collections import Counter

from . import explain, impact, runbooks
from .common import hms, iso
from .correlate import correlate
from .rca import rank


def _title(members, bad_events, top, conf, second=None):
    mset = set(members)
    parts = []
    login_fail = any(e.get("route") in ("/login", "/verify") and (e.get("status") or 0) >= 500
                     for e in bad_events if e["service"] in mset)
    pay_fail = any(e.get("route") == "/pay" and (e.get("status") or 0) >= 500 for e in bad_events if e["service"] in mset)
    pay_slow = any(e.get("route") == "/pay" and (e.get("status") or 0) < 500 for e in bad_events if e["service"] in mset)
    if login_fail:
        parts.append("Login failures")
    if pay_fail:
        parts.append("payment failures")
    elif pay_slow:
        parts.append("slow payments")
    if not parts:
        parts.append(f"{', '.join(sorted(mset))} anomalous")
    head = " and ".join(parts)
    head = head[0].upper() + head[1:]
    if conf == "Not sure" and second:
        return f"{head} — likely cause: {top} or {second} (not sure)"
    return f"{head} — likely cause: {top}"


def build_timeline(inc, corr, members, bad_events):
    items = []
    for m in members:
        a = corr["anoms"][m]
        if a.get("onset") is not None:
            items.append((a["onset"], f"{m} anomalous: {a['reasons'][0] if a['reasons'] else 'anomaly'}"))
        first = next((e for e in bad_events if e["service"] == m), None)
        if first:
            what = first.get("error_type") or ("slow request" if (first.get("status") or 0) < 500 else "error")
            dep = f" (dependency={first['dependency']})" if first.get("dependency") else ""
            items.append((first["_t"], f"first bad event on {m}: {what} {first.get('route') or ''}{dep}".strip()))
    items.append((inc["_opened_t"], f"incident {inc['id']} opened"))
    if inc.get("_top_since_t"):
        items.append((inc["_top_since_t"], f"root cause identified: {inc['top']} ({inc['confidence']})"))
    for t, sev in inc.get("_sev_changes", []):
        items.append((t, f"severity SEV{sev}"))
    if inc.get("_resolved_t"):
        items.append((inc["_resolved_t"], "all members healthy: incident resolved"))
    items.sort(key=lambda x: x[0])
    return [{"t": t, "time": hms(t), "text": txt} for t, txt in items[:20]]


class Engine:
    def __init__(self, cfg, store, detector, explainer):
        self.cfg, self.store, self.detector, self.explainer = cfg, store, detector, explainer
        self.incidents = {}
        self.seq = 0
        self.lock = threading.RLock()
        self.slow_ms = cfg["detection"]["slow_ms"]

    def _new_id(self):
        self.seq += 1
        return f"INC-{self.seq:03d}"

    # ------------------------------------------------------------------ live tick
    def tick(self, now):
        with self.lock:
            self.detector.evaluate(now)
            self.store.prune(now)
            c = self.cfg["correlation"]
            bad = self.store.since(now - c["window_s"], bad_only=True)
            corr = correlate(self.detector.anomalies(), bad, self.cfg["graph"], self.cfg, now)
            bad = corr["recent_bad"]
            window_events = self.store.since(now - 900)
            seen = set()
            for root, members in corr["groups"].items():
                inc = self._match(root, members, now)
                seen.add(inc["id"])
                self._update(inc, root, members, corr, bad, window_events, now)
            for inc in self.incidents.values():
                if inc["origin"] == "live" and inc["status"] == "open" and inc["id"] not in seen:
                    if now - inc["_last_active_t"] >= c["resolve_after_s"]:
                        inc["status"] = "resolved"
                        inc["_resolved_t"] = now
                        inc["resolved_at"] = iso(now)
                        inc["timeline"] = build_timeline(inc, inc["_corr"], inc["members"], inc["_bad"])

    def _match(self, root, members, now):
        mset = set(members)
        open_ = [i for i in self.incidents.values()
                 if i["origin"] == "live" and i["status"] == "open" and (i["root"] in mset or i["root"] == root)]
        open_.sort(key=lambda i: i["_opened_t"])
        if open_:
            keep = open_[0]
            for other in open_[1:]:            # merge premature incidents into the oldest one
                keep["merged"].append(other["id"])
                keep["members"] = sorted(set(keep["members"]) | set(other["members"]))
                del self.incidents[other["id"]]
            return keep
        recent = [i for i in self.incidents.values() if i["origin"] == "live" and i["status"] == "resolved"
                  and i["root"] == root and now - (i.get("_resolved_t") or 0) <= self.cfg["correlation"]["reopen_s"]]
        if recent:
            inc = max(recent, key=lambda i: i["_resolved_t"])
            inc.update(status="open", resolved_at=None, _resolved_t=None, reopened_at=iso(now), _reopened_t=now,
                       _top_since_t=None, top_since=None, top=None)
            inc["reopen_count"] += 1
            return inc
        return self._create(root, now, "live")

    def _create(self, root, now, origin):
        iid = self._new_id()
        inc = {"id": iid, "origin": origin, "root": root, "status": "open", "members": [], "merged": [],
               "opened_at": iso(now), "_opened_t": now, "reopened_at": None, "_reopened_t": None,
               "reopen_count": 0, "resolved_at": None, "_resolved_t": None, "top": None, "top_since": None,
               "_top_since_t": None, "severity": 3, "peak_severity": 3, "_sev_changes": [], "_last_active_t": now}
        self.incidents[iid] = inc
        return inc

    def _update(self, inc, root, members, corr, bad, events, now):
        inc["root"] = root
        inc["members"] = sorted(set(members))
        inc["_last_active_t"] = now
        r = rank(members, corr, bad, self.cfg["graph"], self.cfg)
        if r["top"] != inc.get("top"):
            inc["top"] = r["top"]
            inc["_top_since_t"] = now
            inc["top_since"] = iso(now)
        start = min([corr["anoms"][m]["onset"] for m in members if corr["anoms"][m].get("onset")] or [now])
        if inc["origin"] == "live":
            sev, sev_detail = impact.severity(events, now, self.cfg)
        else:
            sev, sev_detail = impact.severity(events, max(e["_t"] for e in events), self.cfg)
        if sev != inc["severity"] or not inc["_sev_changes"]:
            inc["_sev_changes"].append((now, sev))
        inc["severity"] = sev
        inc["peak_severity"] = min(inc["peak_severity"], sev)
        inc["severity_detail"] = sev_detail
        inc["confidence"] = r["confidence"]
        inc["candidates"] = r["candidates"]
        inc["not_sure_check"] = r["not_sure_check"]
        inc["healthy_dependencies"] = r["healthy_dependencies"]
        inc["baselines"] = r["baselines"]
        second = r["candidates"][1]["service"] if len(r["candidates"]) > 1 else None
        inc["title"] = _title(members, bad, r["top"], r["confidence"], second)
        inc["impact"] = impact.compute(members, start, now, events, self.cfg, self.slow_ms)
        inc["recommendations"] = runbooks.recommend(r, corr["anoms"])
        inc["_corr"], inc["_bad"] = corr, [e for e in bad if e["service"] in set(members)]
        inc["timeline"] = build_timeline(inc, corr, members, inc["_bad"])
        inc["template_report"] = explain.template_report(inc)
        text, src = self.explainer.get(inc)
        inc["report"] = text or inc["template_report"]
        inc["report_source"] = src
        inc["alert_count"] = len(inc["_bad"])

    # ------------------------------------------------------------------ uploads
    def analyze_upload(self, events, name):
        """Run the same correlation + RCA on an uploaded log file (static rules over the file)."""
        if not events:
            return []
        d = self.cfg["detection"]
        by_svc = {}
        for e in events:
            by_svc.setdefault(e["service"], []).append(e)
        anoms = {}
        for svc, evs in by_svc.items():
            bad = [e for e in evs if e["_bad"]]
            if len(bad) < 3 or len(bad) / len(evs) < d["error_rate"]:
                continue
            codes, reasons = set(), []
            msgs = " ".join((e.get("message") or "").lower() for e in bad)
            if any(k in msgs for k in ("connection slots", "too many clients", "too many connections")) \
                    and all(not e.get("dependency") for e in bad[:20]):
                codes.add("db_full")
                reasons.append("log lines report all connection slots in use")
            if any(e["kind"] == "health" or "health check failed" in (e.get("message") or "").lower() for e in bad):
                codes.add("health")
                reasons.append("health check failing")
            lats = [e["latency_ms"] for e in bad if e.get("latency_ms")]
            errs = [e for e in bad if (e.get("status") or 0) >= 500 or e["level"] in ("ERROR", "FATAL", "CRITICAL")]
            if errs:
                codes.add("err")
                reasons.append(f"{len(errs)} error lines out of {len(evs)} ({len(errs)/len(evs):.0%})")
            if lats and max(lats) > d["p95_ms"]:
                codes.add("p95")
                reasons.append(f"slow requests up to {max(lats):,.0f} ms")
            anoms[svc] = {"onset": float(int(bad[0]["_t"])), "codes": codes, "reasons": reasons,
                          "inferred": False, "peak_p95": max(lats) if lats else None}
        end = max(e["_t"] for e in events)
        bad = [e for e in events if e["_bad"]]
        # window = whole file (inference window too)
        cfg = dict(self.cfg)
        cfg["correlation"] = dict(self.cfg["correlation"], inferred_window_s=10 ** 9)
        corr = correlate(anoms, bad, self.cfg["graph"], cfg, end)
        bad = corr["recent_bad"]
        out = []
        with self.lock:
            for root, members in corr["groups"].items():
                start = min(corr["anoms"][m]["onset"] for m in members)
                inc = self._create(root, start, "upload")
                inc["upload_name"] = name
                self._update(inc, root, members, corr, bad, events, start)
                inc["_last_active_t"] = end
                inc["status"] = "from upload"
                out.append(inc["id"])
        return out

    # ------------------------------------------------------------------ views
    @staticmethod
    def public(inc):
        return {k: v for k, v in inc.items() if not k.startswith("_")}

    def list(self):
        with self.lock:
            incs = sorted(self.incidents.values(), key=lambda i: i["_opened_t"], reverse=True)
            return [self.public(i) for i in incs[:30] if i.get("top")]

    def get(self, iid):
        with self.lock:
            inc = self.incidents.get(iid)
            return self.public(inc) if inc else None
