"""Impact and severity, measured from the platform's own (synthetic) traffic."""
from .detect import p95


def _gateway(events, t0, t1, route=None):
    return [e for e in events if e["service"] == "gateway" and e["kind"] == "request"
            and t0 <= e["_t"] < t1 and (route is None or e.get("route") == route)]


def compute(members, start_t, now, events, cfg, slow_ms):
    """events: all events (live store window or an uploaded file) covering [start_t-130, now]."""
    gw = _gateway(events, start_t, now + 1)
    failed = [e for e in gw if (e.get("status") or 0) >= 500]
    users = {e.get("user_id") for e in failed if e.get("user_id")}
    # failures and slow requests: counted over the last 60 s (they only occur during the incident)
    recent = _gateway(events, now - 60, now + 1)
    minutes = 1.0
    f_login = sum(1 for e in recent if e.get("route") == "/login" and (e.get("status") or 0) >= 500)
    f_pay = sum(1 for e in recent if e.get("route") == "/pay" and (e.get("status") or 0) >= 500)
    slow_pay = sum(1 for e in recent if e.get("route") == "/pay" and (e.get("status") or 0) < 500
                   and (e.get("latency_ms") or 0) > slow_ms)
    # estimated revenue at risk per minute = (P_baseline - P_now) x AOV
    base = [e for e in _gateway(events, start_t - 130, start_t - 10, "/pay") if e.get("status") == 200]
    base_min = 120 / 60.0
    p_base = len(base) / base_min if base else 0.0
    aov = (sum(e.get("amount_inr") or 0 for e in base) / len(base)) if base else 0.0
    # successful payments now: since the incident began (max 60 s), as a per-minute rate
    w0 = max(start_t, now - 60)
    ok_now = [e for e in _gateway(events, w0, now + 1, "/pay") if e.get("status") == 200]
    p_now = len(ok_now) / (max(now - w0, 5.0) / 60.0)
    risk = max(0.0, p_base - p_now) * aov
    return {
        "affected_services": sorted(members),
        "affected_users": len(users),
        "failed_logins_per_min": round(f_login / minutes, 1),
        "failed_payments_per_min": round(f_pay / minutes, 1),
        "slow_payments_per_min": round(slow_pay / minutes, 1),
        "payments_per_min_baseline": round(p_base, 1),
        "payments_per_min_now": round(p_now, 1),
        "avg_order_value_inr": round(aov, 0),
        "est_inr_at_risk_per_min": round(risk, 0),
        "label": "estimated, synthetic traffic",
    }


def severity(events, now, cfg):
    """SEV from fixed thresholds (never from the LLM). Returns (sev, details)."""
    s = cfg["severity"]
    t0 = now - s["window_s"]
    out = {}
    for route in ("/login", "/pay"):
        ev = _gateway(events, t0, now + 1, route)
        ok = sum(1 for e in ev if (e.get("status") or 0) < 500)
        out[route] = {"n": len(ev), "success": (ok / len(ev)) if ev else None,
                      "p95": p95([e["latency_ms"] for e in ev if e.get("latency_ms") is not None])}
    rates = [v["success"] for v in out.values() if v["success"] is not None and v["n"] >= 3]
    p95s = [v["p95"] for v in out.values() if v["p95"] is not None]
    if rates and min(rates) < s["sev1_success"]:
        sev = 1
    elif (rates and min(rates) < s["sev2_success"]) or (p95s and max(p95s) > s["sev2_p95_ms"]):
        sev = 2
    else:
        sev = 3
    return sev, out
