"""Blind benchmark: J.A.R.V.I.S. vs four simple baselines on real injected faults.

Each run: chaos injects a fault (truth hidden) -> J.A.R.V.I.S. diagnoses from telemetry only ->
its signed answer is committed -> truth revealed -> heal -> wait until quiet.
Every baseline sees the same incident J.A.R.V.I.S. saw (recorded at diagnosis time).

Usage (stack running, warmed up >= 60 s):  LAB_KEY=... python scripts/benchmark.py 5
"""
import json, os, statistics, sys, time
import httpx

JARVIS, CHAOS = "http://localhost:8000", "http://localhost:8090"
H = {"x-lab-key": os.getenv("LAB_KEY", "")}
FAULTS = ["db_pool", "payment_slow", "auth_down"]
NAMES = {"db_pool": "F1 DB exhaustion", "payment_slow": "F2 payment slowdown", "auth_down": "F3 auth down"}
METHODS = ["jarvis", "random", "most_errors", "first_anomaly", "most_depended_on"]


def open_incidents(since):
    return [i for i in httpx.get(f"{JARVIS}/incidents", params={"since": since}).json()
            if i["origin"] == "live" and i["status"] == "open"]


def wait_quiet(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if not any(i["status"] == "open" and i["origin"] == "live" for i in httpx.get(f"{JARVIS}/incidents").json()):
            return True
        time.sleep(3)
    return False


def one(fault):
    r = httpx.post(f"{CHAOS}/inject", json={"fault": fault}, headers=H, timeout=20).json()
    end, incs = time.time() + 60, []
    while time.time() < end and not incs:
        time.sleep(1); incs = open_incidents(r["started_at"])
    if incs:
        time.sleep(4); incs = open_incidents(r["started_at"]) or incs
    row = {"fault": fault, "truth": None, "jarvis": None, "confidence": None, "ttd_s": None, "baselines": {}}
    if incs:
        main = max(incs, key=lambda i: i.get("alert_count", 0))
        atts = [httpx.get(f"{JARVIS}/incidents/{i['id']}/attestation").json() for i in incs]
        httpx.post(f"{CHAOS}/commit", json={"run_id": r["run_id"], "attestations": atts}, headers=H)
        rev = httpx.get(f"{CHAOS}/reveal", params={"run_id": r["run_id"]}, headers=H).json()
        row.update(truth=rev["targets"], jarvis=sorted({i["top"] for i in incs}), confidence=main["confidence"],
                   k=len(main.get("members", [])) or None,
                   ttd_s=rev["time_to_diagnose_s"], baselines=main["baselines"])
    httpx.post(f"{CHAOS}/heal", headers=H, timeout=60)
    wait_quiet(); time.sleep(8)
    return row


def score(row, m):
    """1/0 per run; the random baseline is scored by its expected value (1 in k broken services)."""
    if m == "random":
        return (1.0 / row["k"]) if row.get("truth") and row.get("k") else 0.0
    return 1.0 if correct(row, m) else 0.0


def correct(row, m):
    if not row["truth"]:
        return False
    ans = row["jarvis"] if m == "jarvis" else [row["baselines"].get(m)]
    return sorted(set(ans)) == sorted(set(row["truth"]))


def run(n):
    rows = []
    for k in range(n):
        for f in FAULTS:
            row = one(f); rows.append(row)
            print(f"run {len(rows):2d} {NAMES[f]:22s} truth={row['truth']} jarvis={row['jarvis']} "
                  f"ttd={row['ttd_s']}s conf={row['confidence']}", flush=True)
    os.makedirs("docs", exist_ok=True)
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M %Z"), "runs": rows}, open("docs/benchmark.json", "w"), indent=1)
    return rows


def render(rows, n):
    md = ["# Blind benchmark: J.A.R.V.I.S. vs simple baselines", "",
          f"Generated {time.strftime('%d %b %Y, %H:%M')} on the Docker stack by `scripts/benchmark.py`: "
          f"{len(rows)} blind runs ({n} per fault), interleaved F1 → F2 → F3. Chaos injects the fault and hides the truth; "
          "J.A.R.V.I.S. diagnoses from telemetry only; its signed answer is committed before the reveal. "
          "Each baseline is scored on the same incident J.A.R.V.I.S. saw.", "",
          "## Top-1 accuracy", "", "| Method | " + " | ".join(NAMES[f] for f in FAULTS) + " | Overall |", "|---|" + "---|" * (len(FAULTS) + 1)]
    label = {"jarvis": "**J.A.R.V.I.S. (evidence score)**", "random": "Random broken service (expected)", "most_errors": "Most error events",
             "first_anomaly": "First to break", "most_depended_on": "Most depended-on (always the DB)"}
    for m in METHODS:
        cells = []
        fmt = (lambda x: f"{x:.1f}") if m == "random" else (lambda x: f"{x:.0f}")
        for f in FAULTS:
            fr = [r for r in rows if r["fault"] == f]
            cells.append(f"{fmt(sum(score(r, m) for r in fr))}/{len(fr)}")
        tot = sum(score(r, m) for r in rows)
        cells.append(f"**{fmt(tot)}/{len(rows)} ({tot / len(rows):.0%})**" if m == "jarvis" else f"{fmt(tot)}/{len(rows)} ({tot / len(rows):.0%})")
        md.append(f"| {label[m]} | " + " | ".join(cells) + " |")
    ttds = [r["ttd_s"] for r in rows if r["ttd_s"] is not None]
    md += ["", "## Time to diagnose (fault injected → root cause named)", ""]
    for f in FAULTS:
        t = [r["ttd_s"] for r in rows if r["fault"] == f and r["ttd_s"] is not None]
        if t: md.append(f"- {NAMES[f]}: median **{statistics.median(t):.1f} s** (range {min(t):.1f}–{max(t):.1f} s)")
    if ttds: md.append(f"- All runs: median **{statistics.median(ttds):.1f} s**")
    md += ["", "## Every run", "", "| # | Fault | Hidden truth | J.A.R.V.I.S. | Confidence | Time | Random | Most errors | First to break | Most depended-on |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    mark = lambda ok: "✓" if ok else "✗"
    for k, r in enumerate(rows, 1):
        b = r["baselines"]
        md.append(f"| {k} | {NAMES[r['fault']]} | {', '.join(r['truth'] or ['none'])} | {', '.join(r['jarvis'] or ['none'])} {mark(correct(r, 'jarvis'))} | "
                  f"{r['confidence'] or '–'} | {r['ttd_s'] if r['ttd_s'] is not None else '–'} s | "
                  + " | ".join((f"1 in {r.get('k') or '?'}" if m == "random" else f"{b.get(m, '–')} {mark(correct(r, m))}") for m in METHODS[1:]) + " |")
    md += ["", "*Honest notes:* a small sample (15 runs) on one machine with synthetic traffic. The random baseline is scored by its "
           "expected value (picking 1 of the k broken services), not a single seeded draw, which would overstate luck. "
           "Thresholds and weights were set before these runs and were not tuned on them."]
    open("docs/BENCHMARK.md", "w").write("\n".join(md) + "\n")
    print("\n".join(md[:14]))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "render":      # re-render docs from docs/benchmark.json
        data = json.load(open("docs/benchmark.json")); rows = data["runs"]
        render(rows, len(rows) // len(FAULTS))
    else:
        n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
        render(run(n), n)
