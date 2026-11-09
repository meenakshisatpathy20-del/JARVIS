"""J.A.R.V.I.S. API + dashboard.

Endpoints: GET / (dashboard), GET /lab, POST /ingest, POST /upload, POST /webhook/alert,
GET /incidents, GET /incidents/{id}, GET /incidents/{id}/report, WS /ws
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .collector import Collector
from .common import iso, load_config
from .detect import Detector
from .engine import Engine
from .explain import Explainer, markdown_report
from .store import Store, normalize

CFG = load_config()
SLOW = CFG["detection"]["slow_ms"]
STATIC = os.path.join(os.path.dirname(__file__), "static")
CHAOS_PUBLIC_URL = os.getenv("CHAOS_PUBLIC_URL", "http://localhost:8090")
SIGNING_KEY = os.getenv("DIAGNOSIS_SIGNING_KEY", "").encode()
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
MAX_UPLOAD = 5 * 1024 * 1024

START = time.time()
store = Store(CFG)
detector = Detector(CFG, START)
explainer = Explainer(CFG)
engine = Engine(CFG, store, detector, explainer)
webhook_log, uploads = [], []
clients: set = set()


def ingest_one(raw: dict, source: str = "live"):
    ev = normalize(raw, source, time.time(), SLOW)
    with engine.lock:
        store.add(ev)
        detector.ingest(ev)
    return ev


def traffic(now):
    gw = [e for e in store.since(now - 60) if e["service"] == "gateway" and e["kind"] == "request"]
    out = {}
    for r in ("/login", "/pay"):
        ev = [e for e in gw if e.get("route") == r]
        ok = sum(1 for e in ev if (e.get("status") or 0) < 500)
        out[r] = {"per_min": len(ev), "ok_per_min": ok, "success": round(ok / len(ev), 3) if ev else None}
    return out


def state():
    now = time.time()
    snap = detector.snapshot()
    incs = engine.list()
    open_live = [i for i in incs if i["status"] == "open" and i["origin"] == "live"]
    causes = {i["top"] for i in open_live}
    services = {}
    for s in CFG["services"] + [x for x in snap if x not in CFG["services"]]:
        st = snap.get(s, {"anomalous": False, "reasons": [], "metrics": {}})
        inferred = any(s in i["members"] and s not in snap for i in open_live)
        services[s] = {"anomalous": st["anomalous"] or inferred, "reasons": st["reasons"], "metrics": st["metrics"],
                       "is_cause": s in causes, "inferred": inferred}
    f = store.funnel(now)
    f["incidents"] = sum(1 for i in incs if i["origin"] == "live" and i["status"] == "open")
    f["incidents_5m"] = sum(1 for i in incs if i["origin"] == "live" and (
        i["status"] == "open" or (i.get("resolved_at") and now - _t(i["resolved_at"]) < 300)))
    return {"now": iso(now), "uptime_s": round(now - START), "warmup_left_s": max(0, round(CFG["detection"]["warmup_s"] - (now - START))),
            "services": services, "graph": CFG["graph"], "funnel": f, "traffic": traffic(now),
            "incidents": incs, "llm_status": explainer.status, "total_raw": store.total_raw}


def _t(ts):
    from .common import parse_ts
    return parse_ts(ts) or 0


DATA_DIR = os.getenv("JARVIS_DATA_DIR", os.path.join(os.path.dirname(STATIC), "..", ".local", "jarvis-data"))
os.makedirs(DATA_DIR, exist_ok=True)
INCIDENT_FILE = os.path.join(DATA_DIR, "incidents.json")


async def ticker():
    n = 0
    while True:
        await asyncio.sleep(1.0)
        n += 1
        try:
            await asyncio.to_thread(engine.tick, time.time())
            if n % 5 == 0:
                await asyncio.to_thread(engine.save, INCIDENT_FILE)
            if clients:
                payload = json.dumps(await asyncio.to_thread(state), default=list)
                for ws in list(clients):
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        clients.discard(ws)
        except Exception as e:  # never let the loop die
            print("tick error:", repr(e), flush=True)


@asynccontextmanager
async def lifespan(app):
    loaded = engine.load(INCIDENT_FILE)
    if loaded:
        print(f"loaded {loaded} incidents from {INCIDENT_FILE}", flush=True)
    if os.getenv("JARVIS_DISABLE_COLLECTOR") != "1":
        Collector(ingest_one).start()
    task = asyncio.create_task(ticker())
    yield
    task.cancel()
    try:
        engine.save(INCIDENT_FILE)
    except Exception:
        pass


app = FastAPI(title="J.A.R.V.I.S.", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def dashboard():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/lab")
def lab():
    return FileResponse(os.path.join(STATIC, "lab.html"))


@app.get("/api/config")
def config():
    return {"chaos_url": CHAOS_PUBLIC_URL, "services": CFG["services"], "graph": CFG["graph"]}


@app.get("/api/state")
def api_state():
    return JSONResponse(json.loads(json.dumps(state(), default=list)))


def _authorized(request: Request) -> bool:
    return not INGEST_TOKEN or hmac.compare_digest(request.headers.get("x-ingest-token", ""), INGEST_TOKEN)


@app.post("/ingest")
async def ingest(request: Request):
    if not _authorized(request):
        return JSONResponse(status_code=401, content={"error": "missing or wrong x-ingest-token"})
    payload = await request.json()
    items = payload if isinstance(payload, list) else [payload]
    for raw in items:
        if isinstance(raw, dict):
            ingest_one(raw, "live")
    return {"accepted": len(items)}


LINE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)Z?\s+(?P<level>[A-Z]+)\s+\[(?P<service>[^\]]+)\]\s+(?P<msg>.*)$")
KV = re.compile(r"(\w+)=(\"[^\"]*\"|\S+)")


def parse_upload(text: str) -> list:
    out = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
                continue
            except ValueError:
                pass
        m = LINE.match(line)
        if not m:
            out.append({"service": "unknown", "kind": "log", "level": "INFO", "message": line})
            continue
        raw = {"ts": f"{m['date']}T{m['time'].replace(',', '.')}Z", "service": m["service"], "kind": "log",
               "level": m["level"], "message": m["msg"]}
        for k, v in KV.findall(m["msg"]):
            v = v.strip('"')
            if k in ("status", "latency_ms", "dependency", "error_type", "route", "trace_id", "user_id", "amount_inr"):
                raw[k] = v
        if raw.get("status") or raw.get("route"):
            raw["kind"] = "request"
        out.append(raw)
    return out


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        return JSONResponse(status_code=413, content={"error": "file larger than 5 MB"})
    text = data.decode("utf-8", errors="replace")
    now = time.time()
    events = [normalize(r, "upload", now, SLOW) for r in parse_upload(text)]
    events.sort(key=lambda e: e["_t"])
    ids = await asyncio.to_thread(engine.analyze_upload, events, file.filename)
    rec = {"name": file.filename, "lines": len(events), "unknown_lines": sum(1 for e in events if e["service"] == "unknown"),
           "incidents": ids, "at": iso(now)}
    uploads.insert(0, rec)
    del uploads[10:]
    return rec


@app.get("/api/sources")
def sources():
    return {"uploads": uploads, "webhook": webhook_log[:20]}


@app.post("/webhook/alert")
async def webhook(request: Request):
    """Alertmanager-style JSON: alerts[].labels.service, alertname, severity, startsAt."""
    if not _authorized(request):
        return JSONResponse(status_code=401, content={"error": "missing or wrong x-ingest-token"})
    payload = await request.json()
    n = 0
    for a in payload.get("alerts", []):
        if a.get("status", "firing") != "firing":
            continue
        labels = a.get("labels", {})
        sev = (labels.get("severity") or "warning").lower()
        raw = {"ts": iso(time.time()), "service": labels.get("service") or labels.get("job") or "unknown",
               "kind": "alert", "level": "ERROR" if sev in ("critical", "page", "error") else "WARN",
               "error_type": labels.get("alertname", "alert"),
               "message": (a.get("annotations", {}) or {}).get("summary") or labels.get("alertname", "alert") +
               f" (startsAt {a.get('startsAt', 'n/a')})"}
        ev = ingest_one(raw, "webhook")
        webhook_log.insert(0, {"service": ev["service"], "alertname": ev["error_type"], "severity": sev, "at": ev["ts"]})
        n += 1
    del webhook_log[50:]
    return {"accepted": n}


@app.get("/incidents")
def incidents(since: str | None = None):
    items = engine.list()
    if since:
        t0 = _t(since)
        items = [i for i in items if max(_t(i["opened_at"]), _t(i.get("reopened_at"))) >= t0]
    return items


@app.get("/incidents/{iid}")
def incident(iid: str):
    inc = engine.get(iid)
    return inc if inc else JSONResponse(status_code=404, content={"error": "not found"})


@app.get("/incidents/{iid}/attestation")
def attestation(iid: str):
    """A signed statement of J.A.R.V.I.S.'s diagnosis. The chaos controller verifies the signature
    before it accepts a commit, so nobody can commit an answer J.A.R.V.I.S. did not give."""
    inc = engine.get(iid)
    if not inc or not inc.get("top"):
        return JSONResponse(status_code=404, content={"error": "no diagnosis"})
    body = {"incident_id": iid, "root_cause": inc["top"], "diagnosed_at": inc["top_since"],
            "confidence": inc["confidence"]}
    msg = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    body["signature"] = hmac.new(SIGNING_KEY, msg, hashlib.sha256).hexdigest() if SIGNING_KEY else None
    return body


@app.get("/incidents/{iid}/report")
def report(iid: str):
    inc = engine.get(iid)
    if not inc:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return PlainTextResponse(markdown_report(inc), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{iid}.md"'})


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        await websocket.send_text(json.dumps(state(), default=list))
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
    except Exception:
        clients.discard(websocket)
