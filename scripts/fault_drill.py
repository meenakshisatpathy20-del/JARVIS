"""Fault drill: inject each fault blindly, wait for J.A.R.V.I.S.'s diagnosis, commit, reveal, heal.

Usage (stack running, load generator warmed up >= 60 s):
    python scripts/fault_drill.py db_pool payment_slow auth_down
"""
import sys
import time

import httpx

JARVIS = "http://localhost:8000"
CHAOS = "http://localhost:8090"


def wait_diagnosis(t0_iso, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        incs = [i for i in httpx.get(f"{JARVIS}/incidents", params={"since": t0_iso}).json()
                if i["origin"] == "live" and i["status"] == "open"]
        if incs:
            time.sleep(4)  # let the diagnosis settle
            return [i for i in httpx.get(f"{JARVIS}/incidents", params={"since": t0_iso}).json()
                    if i["origin"] == "live" and i["status"] == "open"]
        time.sleep(1)
    return []


def wait_quiet(timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        st = httpx.get(f"{JARVIS}/api/state").json()
        if not any(i["status"] == "open" and i["origin"] == "live" for i in st["incidents"]):
            return True
        time.sleep(2)
    return False


def run(fault):
    r = httpx.post(f"{CHAOS}/inject", json={"fault": fault}, timeout=20).json()
    print(f"\n=== {fault}: injected run {r['run_id']} at {r['started_at']}")
    incs = wait_diagnosis(r["started_at"])
    causes = sorted({i["top"] for i in incs})
    for i in incs:
        print(f"  {i['id']} SEV{i['severity']} [{i['confidence']}] {i['title']}")
        for c in i["candidates"]:
            print(f"     {c['service']:16s} score={c['score']:5} terms={c['terms']}")
        print(f"     impact: users={i['impact']['affected_users']} failed_logins/min={i['impact']['failed_logins_per_min']} "
              f"failed_pay/min={i['impact']['failed_payments_per_min']} slow_pay/min={i['impact']['slow_payments_per_min']} "
              f"est_inr/min={i['impact']['est_inr_at_risk_per_min']}")
    ok = False
    if incs:
        diag_at = min(i["top_since"] for i in incs)
        httpx.post(f"{CHAOS}/commit", json={"run_id": r["run_id"], "root_causes": causes, "diagnosed_at": diag_at,
                                             "incident_ids": [i["id"] for i in incs]})
        rev = httpx.get(f"{CHAOS}/reveal", params={"run_id": r["run_id"]}).json()
        ok = rev["match"]
        print(f"  REVEAL truth={rev['targets']} diagnosis={rev['diagnosis']} match={rev['match']} "
              f"time_to_diagnose={rev['time_to_diagnose_s']} s")
    else:
        print("  no incident within 60 s")
    httpx.post(f"{CHAOS}/heal", timeout=40)
    print("  healed; quiet:", wait_quiet())
    return ok


if __name__ == "__main__":
    results = {f: run(f) for f in (sys.argv[1:] or ["db_pool", "payment_slow", "auth_down"])}
    print("\nRESULTS:", results)
