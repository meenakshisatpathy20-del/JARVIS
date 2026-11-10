"""Rules for the verified fix: which action answers which cause, and when recovery counts."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jarvis.remediate import decide_kind, recovered  # noqa: E402


def test_fix_matches_cause():
    assert decide_kind("user-db", {"db_full"}) == "db_cap"
    assert decide_kind("auth-service", {"health", "silent"}) == "restart_auth"
    assert decide_kind("payment-service", {"p95"}) is None      # F2: suggestion only, never an automatic action
    assert decide_kind("gateway", {"err"}) is None


def test_recovery_needs_real_evidence():
    assert recovered("db_cap", 4, 17, 1.0, 30, None)
    assert not recovered("db_cap", 15, 17, 1.0, 30, None)       # DB still saturated
    assert not recovered("db_cap", 4, 17, 0.80, 30, None)       # logins still failing
    assert not recovered("db_cap", 4, 17, None, 0, None)        # no traffic = no proof
    assert recovered("restart_auth", None, 17, 0.99, 20, True)
    assert not recovered("restart_auth", None, 17, 0.99, 20, False)
