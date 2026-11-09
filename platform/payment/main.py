"""payment-service: /pay -> auth /verify, then writes the payment to user-db.

/admin/slow is used only by the chaos controller (token-protected). It adds a delay before
the handler opens its DB connection, like a slow bank call, and logs nothing about it.
"""
import os
import time

import httpx
import psycopg
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from telemetry import emit, new_trace_id

DB_DSN = os.getenv("DB_DSN", "host=localhost port=5432 dbname=payments user=app password=app")
AUTH_URL = os.getenv("AUTH_URL", "http://localhost:8002")
CHAOS_TOKEN = os.getenv("CHAOS_TOKEN", "")
SLOW_MS = 1000

app = FastAPI(title="payment-service")
client = httpx.Client(timeout=3.0, limits=httpx.Limits(max_connections=100))
_state = {"delay_s": 0.0}


def _emit(route, t0, trace_id, user_id, amount, status, dependency=None, error_type=None, message="ok"):
    latency = round((time.perf_counter() - t0) * 1000, 1)
    level = "ERROR" if status >= 500 else ("WARN" if status >= 400 or latency > SLOW_MS else "INFO")
    emit(level=level, kind="request", route=route, status=status, latency_ms=latency,
         dependency=dependency, error_type=error_type, trace_id=trace_id, user_id=user_id,
         amount_inr=amount, message=message)


@app.post("/pay")
def pay(body: dict, request: Request):
    t0 = time.perf_counter()
    trace_id = request.headers.get("x-trace-id") or new_trace_id()
    user_id, amount = body.get("user_id"), body.get("amount_inr")
    # 1. verify the token with auth-service
    try:
        r = client.post(f"{AUTH_URL}/verify", json={"token": body.get("token")}, headers={"x-trace-id": trace_id})
    except httpx.TimeoutException:
        _emit("/pay", t0, trace_id, user_id, amount, 503, "auth-service", "auth_timeout", "auth /verify timed out")
        return JSONResponse(status_code=503, content={"error": "auth_timeout"})
    except httpx.TransportError as e:
        _emit("/pay", t0, trace_id, user_id, amount, 503, "auth-service", "auth_unreachable", f"auth unreachable: {type(e).__name__}")
        return JSONResponse(status_code=503, content={"error": "auth_unreachable"})
    if r.status_code >= 500:
        _emit("/pay", t0, trace_id, user_id, amount, 503, "auth-service", "auth_verify_failed", f"auth /verify returned {r.status_code}")
        return JSONResponse(status_code=503, content={"error": "auth_verify_failed"})
    if r.status_code != 200:
        _emit("/pay", t0, trace_id, user_id, amount, 401, message="token rejected")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    # 2. (chaos only) slow external call before the DB connection is opened
    if _state["delay_s"] > 0:
        time.sleep(_state["delay_s"])
    # 3. write the payment
    try:
        with psycopg.connect(DB_DSN, connect_timeout=2, application_name="payment-service") as conn:
            conn.execute("INSERT INTO payments (user_id, amount_inr) VALUES (%s, %s)", (user_id, amount))
    except psycopg.OperationalError as e:
        _emit("/pay", t0, trace_id, user_id, amount, 503, "user-db", "db_connect_failed",
              str(e).strip().splitlines()[0][:200] if str(e) else "db connect failed")
        return JSONResponse(status_code=503, content={"error": "db_connect_failed"})
    except psycopg.Error as e:
        _emit("/pay", t0, trace_id, user_id, amount, 503, "user-db", "db_query_failed", type(e).__name__)
        return JSONResponse(status_code=503, content={"error": "db_query_failed"})
    _emit("/pay", t0, trace_id, user_id, amount, 200, message="payment ok")
    return {"ok": True}


@app.post("/admin/slow")
def admin_slow(body: dict, x_chaos_token: str = Header(default="")):
    if not CHAOS_TOKEN or x_chaos_token != CHAOS_TOKEN:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    _state["delay_s"] = float(body.get("delay_s", 2.5))
    return {"ok": True}


@app.post("/admin/clear")
def admin_clear(x_chaos_token: str = Header(default="")):
    if not CHAOS_TOKEN or x_chaos_token != CHAOS_TOKEN:
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    _state["delay_s"] = 0.0
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "service": "payment-service"}
