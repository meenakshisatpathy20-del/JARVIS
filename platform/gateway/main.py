"""API gateway: /login -> auth-service, /pay -> payment-service (5 s timeout)."""
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from telemetry import emit, new_trace_id

AUTH_URL = os.getenv("AUTH_URL", "http://localhost:8002")
PAYMENT_URL = os.getenv("PAYMENT_URL", "http://localhost:8003")
TIMEOUT = float(os.getenv("GATEWAY_TIMEOUT_S", "5"))
SLOW_MS = 1000

app = FastAPI(title="gateway")
client = httpx.Client(timeout=TIMEOUT, limits=httpx.Limits(max_connections=200))


def _forward(route: str, target: str, url: str, body: dict, trace_id: str):
    t0 = time.perf_counter()
    status, error_type, message, payload = 200, None, "ok", {}
    try:
        r = client.post(url, json=body, headers={"x-trace-id": trace_id})
        status = r.status_code
        payload = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if status >= 500:
            error_type, message = "upstream_5xx", f"{target} returned {status}"
            status = 502
    except httpx.TimeoutException:
        status, error_type, message = 504, "upstream_timeout", f"{target} timed out after {TIMEOUT:.0f}s"
    except httpx.TransportError as e:
        status, error_type, message = 502, "upstream_unreachable", f"{target} unreachable: {type(e).__name__}"
    latency = round((time.perf_counter() - t0) * 1000, 1)
    level = "ERROR" if status >= 500 else ("WARN" if latency > SLOW_MS or status >= 400 else "INFO")
    emit(
        level=level, kind="request", route=route, status=status, latency_ms=latency,
        dependency=target, error_type=error_type, trace_id=trace_id,
        user_id=body.get("user_id"), amount_inr=body.get("amount_inr") if route == "/pay" else None,
        message=message,
    )
    return JSONResponse(status_code=status, content=payload or {"error": message})


@app.post("/login")
async def login(request: Request):
    body = await request.json()
    trace_id = request.headers.get("x-trace-id") or new_trace_id()
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_forward, "/login", "auth-service", f"{AUTH_URL}/login", body, trace_id)


@app.post("/pay")
async def pay(request: Request):
    body = await request.json()
    trace_id = request.headers.get("x-trace-id") or new_trace_id()
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_forward, "/pay", "payment-service", f"{PAYMENT_URL}/pay", body, trace_id)


@app.get("/health")
def health():
    return {"ok": True, "service": "gateway"}
