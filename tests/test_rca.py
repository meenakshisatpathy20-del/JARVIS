"""RCA fixtures for F1, F2, F3 and F2+F3: correlation + evidence score, no services needed."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jarvis.common import load_config  # noqa: E402
from jarvis.correlate import correlate  # noqa: E402
from jarvis.rca import rank  # noqa: E402

CFG = load_config()
G = CFG["graph"]
NOW = 1_000_000.0


def ev(svc, t, dep=None, status=503, route="/x", latency=10.0):
    return {"service": svc, "_t": NOW - 20 + t, "dependency": dep, "status": status, "route": route,
            "latency_ms": latency, "kind": "request", "level": "ERROR" if status >= 500 else "WARN", "_bad": True}


def anom(onset, codes, reasons=None, p95=None):
    return {"onset": NOW - 20 + onset, "codes": set(codes), "reasons": reasons or ["x"], "inferred": False, "peak_p95": p95}


def diagnose(anoms, events):
    corr = correlate(anoms, events, G, CFG, NOW)
    out = {}
    for root, members in corr["groups"].items():
        out[root] = rank(members, corr, corr["recent_bad"], G, CFG)
    return corr, out


def test_f1_db_exhaustion_blames_user_db_not_gateway():
    events = []
    for i in range(30):
        events += [ev("auth-service", 1 + i * 0.3, "user-db", route="/login"),
                   ev("gateway", 1 + i * 0.3, "auth-service", 502, "/login")]
    anoms = {"user-db": anom(0, {"db_full"}, ["all 17 ordinary connection slots in use (17/17)"]),
             "auth-service": anom(1, {"err"}), "gateway": anom(1, {"err"})}
    corr, res = diagnose(anoms, events)
    assert list(res) == ["user-db"], res.keys()
    r = res["user-db"]
    assert r["top"] == "user-db" and r["confidence"] == "High"
    # the most-errors baseline would be wrong here (gateway or auth logs as many errors)
    assert r["baselines"]["most_errors"] != "user-db"


def test_f2_payment_slowdown_blames_payment_and_db_stays_healthy():
    events = []
    for i in range(20):
        events += [ev("payment-service", 1 + i, None, 200, "/pay", 2600.0),
                   ev("gateway", 1 + i, "payment-service", 200, "/pay", 2620.0)]
    anoms = {"payment-service": anom(0, {"p95"}, ["p95 latency 2,600 ms"], 2600.0), "gateway": anom(1, {"p95"}, p95=2620.0)}
    corr, res = diagnose(anoms, events)
    assert list(res) == ["payment-service"]
    r = res["payment-service"]
    assert r["top"] == "payment-service" and r["confidence"] == "High"
    assert "user-db" in r["healthy_dependencies"]
    assert r["baselines"]["most_depended_on"] == "user-db"   # "always blame the DB" would be wrong


def test_f3_auth_down():
    events = []
    for i in range(20):
        events += [ev("gateway", 1 + i, "auth-service", 502, "/login"),
                   ev("payment-service", 1 + i, "auth-service", 503, "/pay"),
                   ev("gateway", 1 + i, "payment-service", 502, "/pay")]
    anoms = {"auth-service": anom(0, {"health", "silent"}, ["health check failing"]),
             "gateway": anom(1, {"err"}), "payment-service": anom(1, {"err"})}
    corr, res = diagnose(anoms, events)
    assert list(res) == ["auth-service"]
    assert res["auth-service"]["top"] == "auth-service"


def test_f2_plus_f3_split_into_two_incidents():
    # payment is slow on its own (local events), auth is down independently: two sources
    events = []
    for i in range(20):
        events += [ev("payment-service", 1 + i, None, 200, "/pay", 2600.0),
                   ev("gateway", 1 + i, "auth-service", 502, "/login")]
    anoms = {"payment-service": anom(0, {"p95"}, p95=2600.0), "auth-service": anom(0, {"health"}),
             "gateway": anom(1, {"err"})}
    corr, res = diagnose(anoms, events)
    assert set(res) == {"payment-service", "auth-service"}


def test_inferred_dependency_when_its_metrics_are_missing():
    events = []
    for i in range(10):
        events += [ev("auth-service", 15 + i * 0.3, "user-db"), ev("payment-service", 15 + i * 0.3, "user-db")]
    anoms = {"auth-service": anom(15, {"err"}), "payment-service": anom(15, {"err"})}
    corr, res = diagnose(anoms, events)
    assert corr["anoms"]["user-db"]["inferred"] is True
    assert list(res) == ["user-db"]


def test_not_sure_when_evidence_is_split():
    # two candidates with identical evidence and no local fault signal
    events = [ev("gateway", 1 + i, "auth-service" if i % 2 else "payment-service", 502) for i in range(20)]
    anoms = {"gateway": anom(0, {"err"})}
    corr, res = diagnose(anoms, events)
    r = res["gateway"]
    assert r["confidence"] in ("Medium", "Not sure")


def test_pii_scrub():
    from jarvis.common import scrub
    s = scrub("card 4111 1111 1111 1111 mail a.b@x.com phone 9876543210 Bearer abc.def")
    assert "4111" not in s and "@" not in s and "9876543210" not in s and "abc.def" not in s


def test_recovery_tail_does_not_open_phantom_incident():
    # user-db has just recovered; auth still shows tail errors pointing to it
    events = [ev("auth-service", 15 + i * 0.3, "user-db", route="/login") for i in range(10)]
    anoms = {"auth-service": anom(0, {"err"})}
    recovering = {"user-db": {"cleared_t": NOW - 3, "onset": NOW - 20, "codes": {"db_full"},
                              "reasons": ["all 17 ordinary connection slots in use (17/17)"], "peak_p95": None}}
    corr = correlate(anoms, events, G, CFG, NOW, recovering=recovering)
    assert list(corr["groups"]) == ["user-db"]
