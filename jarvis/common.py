"""Shared helpers: config loading, time parsing, PII scrub, 'bad event' rule."""
import datetime
import os
import re

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(path: str | None = None) -> dict:
    path = path or os.getenv("JARVIS_CONFIG", os.path.join(ROOT, "config.yaml"))
    with open(path) as f:
        return yaml.safe_load(f)


def parse_ts(ts) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def iso(t: float | None) -> str | None:
    if t is None:
        return None
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def hms(t: float | None) -> str:
    if t is None:
        return "--:--:--"
    return datetime.datetime.fromtimestamp(t).strftime("%H:%M:%S")


# ---- privacy guard: applied before any free text is stored or sent ----
_PII = [
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"), "[REDACTED]"),          # JWT
    (re.compile(r"(?i)\bbearer\s+[\w\-.~+/]+=*"), "Bearer [REDACTED]"),      # bearer token
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED]"),               # email
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "[REDACTED]"),               # card-like 13-19 digits
    (re.compile(r"(?<!\d)(?:\+91[ -]?)?\d{10}(?!\d)"), "[REDACTED]"),      # 10-digit phone
]


def scrub(text):
    if not text or not isinstance(text, str):
        return text
    for rx, rep in _PII:
        text = rx.sub(rep, text)
    return text


BAD_LEVELS = {"ERROR", "FATAL", "CRITICAL"}


def is_bad(ev: dict, slow_ms: float) -> bool:
    """An event that signals trouble: an error, a slow request, a failed check or an alert."""
    kind = ev.get("kind")
    if kind == "alert":
        return True
    if kind == "health":
        return ev.get("status") != 200
    if kind == "metric":
        return False
    status = ev.get("status") or 0
    if status >= 500 or (ev.get("level") or "").upper() in BAD_LEVELS:
        return True
    lat = ev.get("latency_ms")
    return bool(lat and lat > slow_ms)
