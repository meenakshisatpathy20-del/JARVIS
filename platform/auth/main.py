"""auth-service: /login and /verify, both read user-db.

One short-lived DB connection per request (connect_timeout=2). A long-lived pool would
keep working during a connection-exhaustion fault and hide it.
"""
import os
import time

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from telemetry import emit, new_trace_id

DB_DSN = os.getenv("DB_DSN", "host=localhost port=5432 dbname=payments user=app password=app")
SLOW_MS = 1000

app = FastAPI(title="auth-service")


def _connect():
    return psycopg.connect(DB_DSN, connect_timeout=2, application_name="auth-service")


def _db_error(route, t0, trace_id, user_id, e, error_type):
    latency = round((time.perf_counter() - t0) * 1000, 1)
    emit(level="ERROR", kind="request", route=route, status=503, latency_ms=latency,
         dependency="user-db", error_type=error_type, trace_id=trace_id, user_id=user_id,
         message=str(e).strip().splitlines()[0][:200] if str(e) else type(e).__name__)
    return JSONResponse(status_code=503, content={"error": error_type})


def _ok(route, t0, trace_id, user_id, status, message):
    latency = round((time.perf_counter() - t0) * 1000, 1)
    level = "WARN" if status >= 400 or latency > SLOW_MS else "INFO"
    emit(level=level, kind="request", route=route, status=status, latency_ms=latency,
         trace_id=trace_id, user_id=user_id, message=message)


@app.post("/login")
def login(body: dict, request: Request):
    t0 = time.perf_counter()
    trace_id = request.headers.get("x-trace-id") or new_trace_id()
    user_id = body.get("user_id")
    try:
        with _connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE id=%s AND password=%s",
                               (user_id, body.get("password"))).fetchone()
    except psycopg.OperationalError as e:
        return _db_error("/login", t0, trace_id, user_id, e, "db_connect_failed")
    except psycopg.Error as e:
        return _db_error("/login", t0, trace_id, user_id, e, "db_query_failed")
    if not row:
        _ok("/login", t0, trace_id, user_id, 401, "invalid credentials")
        return JSONResponse(status_code=401, content={"error": "invalid credentials"})
    _ok("/login", t0, trace_id, user_id, 200, "login ok")
    return {"token": f"tok_{user_id}", "user_id": user_id}


@app.post("/verify")
def verify(body: dict, request: Request):
    t0 = time.perf_counter()
    trace_id = request.headers.get("x-trace-id") or new_trace_id()
    token = body.get("token") or ""
    user_id = token[4:] if token.startswith("tok_") else None
    try:
        with _connect() as conn:
            row = conn.execute("SELECT id FROM users WHERE id=%s", (user_id,)).fetchone()
    except psycopg.OperationalError as e:
        return _db_error("/verify", t0, trace_id, user_id, e, "db_connect_failed")
    except psycopg.Error as e:
        return _db_error("/verify", t0, trace_id, user_id, e, "db_query_failed")
    if not row:
        _ok("/verify", t0, trace_id, user_id, 401, "invalid token")
        return JSONResponse(status_code=401, content={"error": "invalid token"})
    _ok("/verify", t0, trace_id, user_id, 200, "token ok")
    return {"user_id": user_id}


@app.get("/health")
def health():
    return {"ok": True, "service": "auth-service"}
