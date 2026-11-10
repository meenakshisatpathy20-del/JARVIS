"""Verified remediation: preview -> human approval -> allow-listed action -> recovery check.

Safety rules
- Only the actions in ALLOWED can ever run, and only after a person approves them.
- During a blind test the fix stays locked until the hidden truth has been revealed
  (or someone explicitly ends the blind test), so a fix can never leak the answer.
- Every action is written to the audit log with who approved it, what ran and the result.
- If recovery is not confirmed within VERIFY_S seconds the action is reported as
  "escalated", never as a success.
"""
import os
import subprocess
import threading
import time
import uuid

import httpx
import psycopg
from psycopg import sql

from .common import iso

MONITOR_DSN = os.getenv("MONITOR_DB_DSN", "host=localhost port=5432 dbname=payments user=postgres")
AUTH_CONTAINER = os.getenv("AUTH_CONTAINER", "jarvis-auth-service")
DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
FIX_AUTH_START_CMD = os.getenv("FIX_AUTH_START_CMD", "")
ALLOWED = {"db_cap", "restart_auth"}
VERIFY_S = 60
CAP = 2


def decide_kind(top: str, codes: set) -> str | None:
    """Which allow-listed fix (if any) answers this root cause."""
    if top == "user-db" and codes & {"db_full", "db_unreadable", "inferred"}:
        return "db_cap"
    if top == "auth-service" and codes & {"health", "silent", "inferred"}:
        return "restart_auth"
    return None


def recovered(kind: str, conn_used, slots: int, login_success, login_n: int, auth_healthy) -> bool:
    """The recovery rule each fix is checked against."""
    logins_ok = login_success is not None and login_n >= 3 and login_success >= 0.95
    if kind == "db_cap":
        return conn_used is not None and conn_used < 0.7 * slots and logins_ok
    if kind == "restart_auth":
        return auth_healthy is True and logins_ok
    return False


class Remediator:
    def __init__(self, cfg, engine, store, detector):
        self.cfg, self.engine, self.store, self.detector = cfg, engine, store, detector
        self.slots = cfg["detection"]["db_ordinary_slots"]
        self.actions = []          # audit log, newest last
        self.unlocked = set()      # incidents whose blind test is over
        self.previews = {}
        self.lock = threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _incident(self, iid):
        inc = self.engine.incidents.get(iid)
        if not inc or not inc.get("top"):
            return None, set()
        anoms = (inc.get("_corr") or {}).get("anoms", {})
        codes = set(anoms.get(inc["top"], {}).get("codes", set()))
        return inc, codes

    def _login_success(self, window=10):
        now = time.time()
        ev = [e for e in self.store.since(now - window)
              if e["service"] == "gateway" and e["kind"] == "request" and e.get("route") == "/login"]
        ok = sum(1 for e in ev if (e.get("status") or 0) < 500)
        return (ok / len(ev) if ev else None), len(ev)

    def _db(self):
        return psycopg.connect(MONITOR_DSN, connect_timeout=3, autocommit=True, application_name="jarvis-remediation")

    def _conn_used(self):
        with self._db() as c:
            return c.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend'").fetchone()[0]

    def _noisiest_role(self):
        """The non-superuser role holding the most idle connections (open > 5 s without work)."""
        q = """SELECT a.usename, count(*) AS idle_held
               FROM pg_stat_activity a JOIN pg_roles r ON r.rolname = a.usename
               WHERE a.backend_type = 'client backend' AND NOT r.rolsuper
                 AND a.state = 'idle' AND now() - a.state_change > interval '5 seconds'
               GROUP BY a.usename ORDER BY idle_held DESC LIMIT 1"""
        with self._db() as c:
            row = c.execute(q).fetchone()
        return (row[0], int(row[1])) if row else (None, 0)

    # ------------------------------------------------------------------ public API
    def unlock(self, iid):
        with self.lock:
            self.unlocked.add(iid)

    def preview(self, iid):
        with self.engine.lock:
            inc, codes = self._incident(iid)
        if not inc:
            return {"available": False, "reason": "No diagnosis for this incident yet."}
        kind = decide_kind(inc["top"], codes)
        base = {"incident_id": iid, "root_cause": inc["top"], "kind": kind, "locked": iid not in self.unlocked}
        if kind is None:
            rec = (inc.get("recommendations") or [{}])[0]
            return {**base, "available": False,
                    "reason": f"No safe automatic fix exists for this cause. Suggested: {rec.get('title', 'see runbook')}.",
                    "steps": rec.get("steps", [])}
        if kind == "db_cap":
            try:
                role, held = self._noisiest_role()
                used = self._conn_used()
            except psycopg.Error as e:
                return {**base, "available": False, "reason": f"Cannot read Postgres right now ({type(e).__name__})."}
            if not role or held == 0:
                return {**base, "available": False, "reason": "No role is holding idle connections any more."}
            plan = {**base, "available": True, "role": role,
                    "title": "Cap the client that is hogging database connections",
                    "steps": [f"Limit database role '{role}' to {CAP} connections (it holds {held} idle of {used} in use).",
                              f"End the idle sessions held by '{role}' so other services can connect again."],
                    "verify": f"Database use below 70% of {self.slots} slots and login success above 95%, within {VERIFY_S} s.",
                    "undo": f"Undo removes the limit on '{role}'. It is also removed automatically when the incident resolves."}
        else:
            plan = {**base, "available": True,
                    "title": "Restart auth-service",
                    "steps": ["Start the auth-service container again."],
                    "verify": f"auth-service health check green and login success above 95%, within {VERIFY_S} s.",
                    "undo": "Nothing to undo."}
        with self.lock:
            self.previews[iid] = plan
        return plan

    def run(self, iid, approver):
        if iid not in self.unlocked:
            return {"error": "Fix is locked until the blind test is revealed (or ended)."}
        plan = self.previews.get(iid) or self.preview(iid)
        if not plan.get("available") or plan.get("kind") not in ALLOWED:
            return {"error": plan.get("reason") or "No allow-listed fix for this incident."}
        with self.lock:
            if any(a["incident_id"] == iid and a["status"] in ("running", "verifying") for a in self.actions):
                return {"error": "A fix for this incident is already running."}
            act = {"id": "ACT-" + uuid.uuid4().hex[:6], "incident_id": iid, "kind": plan["kind"],
                   "title": plan["title"], "steps": plan["steps"], "approved_by": (approver or "on-call engineer")[:60],
                   "approved_at": iso(time.time()), "status": "running", "result": None, "checks": [],
                   "role": plan.get("role"), "undone": False, "finished_at": None}
            self.actions.append(act)
        threading.Thread(target=self._execute, args=(act,), daemon=True).start()
        return act

    def _execute(self, act):
        try:
            if act["kind"] == "db_cap":
                role = act["role"]
                with self._db() as c:
                    c.execute(sql.SQL("ALTER ROLE {} CONNECTION LIMIT {}").format(sql.Identifier(role), sql.Literal(CAP)))
                    n = c.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                                  "WHERE usename = %s AND state = 'idle' AND pid <> pg_backend_pid()", (role,)).fetchone()[0]
                act["checks"].append(f"capped role '{role}' at {CAP} connections and ended {n} idle sessions")
            elif act["kind"] == "restart_auth":
                if os.path.exists(DOCKER_SOCK):
                    with httpx.Client(transport=httpx.HTTPTransport(uds=DOCKER_SOCK), base_url="http://docker", timeout=20) as d:
                        r = d.post(f"/containers/{AUTH_CONTAINER}/start")
                        if r.status_code not in (204, 304):
                            raise RuntimeError(f"docker start -> {r.status_code}")
                elif FIX_AUTH_START_CMD:
                    subprocess.run(FIX_AUTH_START_CMD, shell=True, check=True, timeout=20)
                else:
                    raise RuntimeError("no way to restart auth-service in this environment")
                act["checks"].append("started auth-service")
        except Exception as e:
            act.update(status="escalated", result=f"Action failed: {e}", finished_at=iso(time.time()))
            return
        act["status"] = "verifying"
        deadline = time.time() + VERIFY_S
        last = ""
        while time.time() < deadline:
            time.sleep(2)
            try:
                used = self._conn_used()
            except psycopg.Error:
                used = None
            succ, n = self._login_success()
            healthy = self.detector.last_health.get("auth-service")
            last = (f"db {used}/{self.slots} in use · login success "
                    f"{'n/a' if succ is None else f'{succ:.0%}'} ({n} logins, last 10 s)")
            if recovered(act["kind"], used, self.slots, succ, n, healthy):
                took = round(VERIFY_S - (deadline - time.time()), 1)
                act["checks"].append(f"recovered after {took} s: {last}")
                act.update(status="resolved", result=f"Recovery verified in {took} s ({last}).", finished_at=iso(time.time()))
                return
        act["checks"].append(f"not recovered after {VERIFY_S} s: {last}")
        act.update(status="escalated",
                   result=f"Recovery NOT confirmed within {VERIFY_S} s ({last}). Escalate to the on-call engineer.",
                   finished_at=iso(time.time()))

    def undo(self, aid, reason="manual undo"):
        with self.lock:
            act = next((a for a in self.actions if a["id"] == aid), None)
        if not act or act["kind"] != "db_cap" or act["undone"]:
            return {"error": "Nothing to undo."}
        try:
            with self._db() as c:
                c.execute(sql.SQL("ALTER ROLE {} CONNECTION LIMIT -1").format(sql.Identifier(act["role"])))
        except psycopg.Error as e:
            return {"error": f"Undo failed: {type(e).__name__}"}
        act["undone"] = True
        act["checks"].append(f"limit on '{act['role']}' removed ({reason}) at {iso(time.time())}")
        return act

    def tick(self):
        """Remove a connection cap automatically once its incident has resolved."""
        for act in list(self.actions):
            if act["kind"] == "db_cap" and not act["undone"] and act["status"] in ("resolved", "escalated"):
                inc = self.engine.incidents.get(act["incident_id"])
                if inc and inc.get("status") != "open":
                    self.undo(act["id"], reason="incident resolved")

    def for_incident(self, iid):
        return {"unlocked": iid in self.unlocked,
                "actions": [a for a in self.actions if a["incident_id"] == iid]}
