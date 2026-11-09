"""Chaos controller: injects real faults and keeps the ground truth to itself.

Rules (see README): chaos actions never enter J.A.R.V.I.S.'s event stream; the truth file
lives only on the chaos volume; /reveal answers only after a diagnosis has been committed.
"""
import datetime
import hashlib
import hmac
import json
import os
import random
import subprocess
import threading
import time
import uuid

import httpx
import psycopg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

CHAOS_DB_DSN = os.getenv("CHAOS_DB_DSN", "host=localhost port=5432 dbname=payments user=chaos password=chaos")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://localhost:8003")
CHAOS_TOKEN = os.getenv("CHAOS_TOKEN", "")
AUTH_CONTAINER = os.getenv("AUTH_CONTAINER", "jarvis-auth-service")
DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
AUTH_STOP_CMD = os.getenv("CHAOS_AUTH_STOP_CMD", "")    # local (non-Docker) fallback
AUTH_START_CMD = os.getenv("CHAOS_AUTH_START_CMD", "")
DATA_DIR = os.getenv("CHAOS_DATA_DIR", "/data")
ORDINARY_SLOTS = int(os.getenv("DB_ORDINARY_SLOTS", "17"))
SIGNING_KEY = os.getenv("DIAGNOSIS_SIGNING_KEY", "").encode()
ALLOWED_ORIGINS = os.getenv("CHAOS_CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",")

FAULTS = {
    "db_pool": ["user-db"],
    "payment_slow": ["payment-service"],
    "auth_down": ["auth-service"],
    "payment_slow+auth_down": ["payment-service", "auth-service"],
}

app = FastAPI(title="chaos")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["*"], allow_headers=["*"])

os.makedirs(DATA_DIR, exist_ok=True)
TRUTH_FILE = os.path.join(DATA_DIR, "truth.json")
_lock = threading.Lock()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _read():
    try:
        with open(TRUTH_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(data):
    tmp = TRUTH_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, TRUTH_FILE)
    os.chmod(TRUTH_FILE, 0o600)


# ---------------- F1: DB connection exhaustion ----------------
class Hog:
    def __init__(self):
        self.active = False
        self.conns = []
        self.thread = None

    def start(self):
        if self.active:
            return
        self.active = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        # Keep grabbing every ordinary slot that frees up, and hold it.
        while self.active:
            try:
                c = psycopg.connect(CHAOS_DB_DSN, connect_timeout=1, application_name="chaos-hog", autocommit=True)
                self.conns.append(c)
            except psycopg.OperationalError:
                time.sleep(0.05)

    def stop(self):
        self.active = False
        if self.thread:
            self.thread.join(timeout=3)
        for c in self.conns:
            try:
                c.close()
            except Exception:
                pass
        self.conns = []


hog = Hog()


def _db_usage():
    with psycopg.connect(CHAOS_DB_DSN, connect_timeout=2, application_name="chaos-heal", autocommit=True) as c:
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE application_name='chaos-hog' AND pid <> pg_backend_pid()")
        n = c.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend'").fetchone()[0]
    return n


def _heal_db():
    hog.stop()
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if _db_usage() < 0.7 * ORDINARY_SLOTS:
                return True
        except psycopg.OperationalError:
            pass
        time.sleep(0.5)
    return False


# ---------------- F2: payment slowdown ----------------
def _payment(path, body=None):
    r = httpx.post(f"{PAYMENT_URL}{path}", json=body or {}, headers={"x-chaos-token": CHAOS_TOKEN}, timeout=3)
    r.raise_for_status()


# ---------------- F3: auth down ----------------
def _docker(path):
    transport = httpx.HTTPTransport(uds=DOCKER_SOCK)
    with httpx.Client(transport=transport, base_url="http://docker", timeout=15) as c:
        r = c.post(path)
        if r.status_code not in (204, 304):
            raise RuntimeError(f"docker {path} -> {r.status_code} {r.text[:200]}")


def _auth(action):
    if os.path.exists(DOCKER_SOCK):
        _docker(f"/containers/{AUTH_CONTAINER}/{action}" + ("?t=1" if action == "stop" else ""))
        return
    cmd = AUTH_STOP_CMD if action == "stop" else AUTH_START_CMD
    if not cmd:
        raise RuntimeError("no docker socket and no CHAOS_AUTH_*_CMD configured")
    subprocess.run(cmd, shell=True, check=True, timeout=20)


def _apply(fault):
    if fault == "db_pool":
        hog.start()
    elif fault == "payment_slow":
        _payment("/admin/slow", {"delay_s": 2.5})
    elif fault == "auth_down":
        _auth("stop")
    elif fault == "payment_slow+auth_down":
        _payment("/admin/slow", {"delay_s": 2.5})
        _auth("stop")


def _heal_all():
    notes = []
    if hog.active or hog.conns:
        notes.append("db: " + ("ok" if _heal_db() else "still above 70%"))
    try:
        _payment("/admin/clear")
    except Exception as e:
        notes.append(f"payment clear failed: {type(e).__name__}")
    truth = _read()
    if truth.get("fault") in ("auth_down", "payment_slow+auth_down") and not truth.get("healed_at"):
        try:
            _auth("start")
        except Exception as e:
            notes.append(f"auth start failed: {e}")
    if not notes or all("ok" in n for n in notes):
        # also make sure no leftover hog sessions exist
        try:
            _db_usage()
        except Exception:
            pass
    return notes


@app.post("/inject")
def inject(body: dict):
    fault = body.get("fault")
    hidden = fault == "random"
    if hidden:
        # "Surprise me" draws only single faults: the F2+F3 split is not claimed live (see README)
        fault = random.choice(["db_pool", "payment_slow", "auth_down"])
    if fault not in FAULTS:
        return JSONResponse(status_code=400, content={"error": f"unknown fault; choose one of {list(FAULTS)} or random"})
    with _lock:
        truth = _read()
        if truth.get("run_id") and not truth.get("healed_at"):
            return JSONResponse(status_code=409, content={"error": "a fault is already active; POST /heal first"})
        run_id = uuid.uuid4().hex[:8]
        started_at = _now()
        _apply(fault)
        _write({"run_id": run_id, "fault": fault, "targets": FAULTS[fault], "started_at": started_at,
                "hidden": hidden, "commit": None, "healed_at": None})
    return {"run_id": run_id, "started_at": started_at, "fault": "hidden" if hidden else fault}


@app.post("/heal")
def heal():
    with _lock:
        notes = _heal_all()
        truth = _read()
        if truth:
            truth["healed_at"] = truth.get("healed_at") or _now()
            _write(truth)
    return {"ok": True, "notes": notes}


@app.post("/commit")
def commit(body: dict):
    """Lock in J.A.R.V.I.S.'s published diagnosis before the truth can be revealed."""
    with _lock:
        truth = _read()
        if not truth or truth.get("run_id") != body.get("run_id"):
            return JSONResponse(status_code=404, content={"error": "unknown run_id"})
        if truth.get("commit"):
            return {"ok": True, "already": True}
        atts = body.get("attestations") or []
        if not atts:
            return JSONResponse(status_code=400, content={"error": "no signed diagnosis to commit"})
        started = _parse(truth["started_at"])
        for a in atts:
            sig = a.get("signature") or ""
            msg = json.dumps({k: a.get(k) for k in ("incident_id", "root_cause", "diagnosed_at", "confidence")},
                             sort_keys=True, separators=(",", ":")).encode()
            good = SIGNING_KEY and hmac.compare_digest(sig, hmac.new(SIGNING_KEY, msg, hashlib.sha256).hexdigest())
            if not good:
                return JSONResponse(status_code=403, content={"error": "diagnosis signature invalid: not issued by J.A.R.V.I.S."})
            if not a.get("diagnosed_at") or _parse(a["diagnosed_at"]) < started:
                return JSONResponse(status_code=409, content={"error": "diagnosis predates this fault"})
        causes = sorted({a["root_cause"] for a in atts})
        truth["commit"] = {"root_causes": causes, "diagnosed_at": min(a["diagnosed_at"] for a in atts),
                           "incident_ids": [a["incident_id"] for a in atts], "verified": True,
                           "committed_at": _now()}
        _write(truth)
    return {"ok": True}


def _parse(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


@app.get("/reveal")
def reveal(run_id: str):
    truth = _read()
    if not truth or truth.get("run_id") != run_id:
        return JSONResponse(status_code=404, content={"error": "unknown run_id"})
    if not truth.get("commit"):
        return JSONResponse(status_code=409, content={"error": "J.A.R.V.I.S. has not published a diagnosis yet"})
    c = truth["commit"]
    match = sorted(set(c["root_causes"])) == sorted(set(truth["targets"]))
    ttd = None
    if c.get("diagnosed_at"):
        ttd = round((_parse(c["diagnosed_at"]) - _parse(truth["started_at"])).total_seconds(), 1)
    return {"run_id": run_id, "fault": truth["fault"], "targets": truth["targets"],
            "started_at": truth["started_at"], "diagnosis": c["root_causes"], "match": match,
            "incident_ids": c.get("incident_ids", []), "signature_verified": bool(c.get("verified")),
            "time_to_diagnose_s": ttd}


@app.get("/status")
def status():
    t = _read()
    return {"active": bool(t.get("run_id") and not t.get("healed_at")), "run_id": t.get("run_id")}


@app.get("/health")
def health():
    return {"ok": True}
