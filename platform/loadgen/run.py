"""20 simulated users looping login + pay against the gateway (synthetic traffic)."""
import os
import random
import threading
import time

import httpx

GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8001")
HEALTH_URLS = os.getenv("HEALTH_URLS", f"{GATEWAY}/health").split(",")
USERS = int(os.getenv("LOADGEN_USERS", "20"))
stats = {"login_ok": 0, "login_fail": 0, "pay_ok": 0, "pay_fail": 0}
lock = threading.Lock()


def bump(key):
    with lock:
        stats[key] += 1


def user_loop(i: int):
    user_id = f"u_{1000 + i}"
    client = httpx.Client(timeout=8.0)
    rnd = random.Random(i)
    while True:
        try:
            r = client.post(f"{GATEWAY}/login", json={"user_id": user_id, "password": f"pw{1000 + i}"})
            if r.status_code != 200:
                bump("login_fail")
                time.sleep(1.0)
                continue
            bump("login_ok")
            token = r.json().get("token")
            amount = rnd.choice([149, 299, 499, 799, 1299, 1999, 2499, 4999])
            r = client.post(f"{GATEWAY}/pay", json={"user_id": user_id, "token": token, "amount_inr": amount})
            bump("pay_ok" if r.status_code == 200 else "pay_fail")
        except httpx.HTTPError:
            bump("login_fail")
            time.sleep(1.0)
        time.sleep(rnd.uniform(0.6, 1.4))


def wait_for_gateway():
    """Start traffic only once every service answers /health, so start-up is not an incident."""
    while True:
        try:
            if all(httpx.get(u, timeout=2).status_code == 200 for u in HEALTH_URLS):
                return
        except httpx.HTTPError:
            pass
        print("loadgen: waiting for services...", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    wait_for_gateway()
    for i in range(1, USERS + 1):
        threading.Thread(target=user_loop, args=(i,), daemon=True).start()
        time.sleep(0.05)
    print(f"loadgen: {USERS} users running against {GATEWAY}", flush=True)
    while True:
        time.sleep(10)
        with lock:
            print("loadgen:", dict(stats), flush=True)
