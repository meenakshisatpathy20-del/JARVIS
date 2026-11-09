"""Root cause engine: score(s) = 3A + 2O + 2L + 1C - 3X, confidence in words, baselines.

The weights are hand-set in config.yaml and were written down before testing.
"""
import random
from collections import Counter

LATENCY_ERR = {"p95", "z_p95", "err", "z_err"}


def _local_fault(s, anoms, parents, counts, graph):
    a = anoms[s]
    codes = a["codes"]
    if "db_full" in codes:
        return 1, next((r for r in a["reasons"] if "slots" in r), "all ordinary DB connection slots are in use")
    if "db_unreadable" in codes:
        return 1, "the database monitor could not read Postgres stats"
    if "health" in codes:
        return 1, f"{s} health check is failing"
    if "silent" in codes:
        return 1, f"{s} went silent while still receiving traffic"
    if "alert" in codes and s not in parents:
        return 1, next((r for r in a["reasons"] if r.startswith("external alert")), "an external alert names it")
    if codes & LATENCY_ERR:
        deps = graph.get(s, [])
        bad_deps = [d for d in deps if d in anoms]
        cnt = counts.get(s, {})
        total = sum(cnt.values())
        local = cnt.get(None, 0)
        if not bad_deps and total and local / total >= 0.5:
            what = (f"its own handler latency rose (p95 {a['peak_p95']:,.0f} ms)" if a.get("peak_p95")
                    else "its own requests fail without a failing dependency")
            healthy = f" while its dependencies ({', '.join(deps)}) stay healthy" if deps else ""
            return 1, what + healthy
    return 0, None


def rank(members: list, corr: dict, bad_events: list, graph: dict, cfg: dict, all_services=None) -> dict:
    r = cfg["rca"]
    w = r["weights"]
    anoms, parents, counts = corr["anoms"], corr["parents"], corr["counts"]
    mset = set(members)
    pointing = [e for e in bad_events if e["service"] in mset and e.get("dependency") and e["dependency"] != e["service"]]
    total = len(pointing)
    pointed = Counter(e["dependency"] for e in pointing)
    onsets = [anoms[m]["onset"] for m in members if anoms[m].get("onset") is not None]
    min_onset = min(onsets) if onsets else 0

    cands = []
    for s in members:
        a = anoms[s]
        A = pointed[s] / total if total else 0.0
        O = 1 if a.get("onset") is not None and a["onset"] <= min_onset + r["onset_tie_s"] else 0
        L, l_reason = _local_fault(s, anoms, parents, counts, graph)
        C = 0  # real deploy/config change events: final round (chaos actions never count)
        X = 1 if s in parents else 0
        score = w["A"] * A + w["O"] * O + w["L"] * L + w["C"] * C - w["X"] * X
        ev = []
        if A:
            ev.append(f"{A:.0%} of the incident's error and slow events point to {s} as the failing dependency "
                      f"({pointed[s]} of {total}).")
        if O:
            ev.append(f"{s} broke first (onset {_hms(a['onset'])}" + (", tied within 2 s)" if
                      sum(1 for o in onsets if o <= min_onset + r['onset_tie_s']) > 1 else ")") + ".")
        if L:
            ev.append(f"Own fault signal: {l_reason}.")
        if X:
            p = parents[s]
            cnt = counts.get(s, {})
            share = cnt.get(p, 0) / max(1, sum(cnt.values()))
            ev.append(f"Contradiction: {share:.0%} of {s}'s errors point to {p}, which is also broken, so {s} "
                      f"looks like a symptom.")
        if a.get("inferred"):
            ev.append(a["reasons"][0][0].upper() + a["reasons"][0][1:] + " (its own metrics are unavailable).")
        cands.append({"service": s, "score": round(score, 2), "terms": {"A": round(A, 2), "O": O, "L": L, "C": C, "X": X},
                      "evidence": ev, "onset": a.get("onset"), "reasons": a.get("reasons", [])})
    cands.sort(key=lambda c: (-c["score"], c["onset"] or 0, c["service"]))

    top = cands[0] if cands else None
    second = cands[1] if len(cands) > 1 else None
    gap = top["score"] - (second["score"] if second else 0) if top else 0
    if top and top["score"] >= r["high_min_score"] and gap >= r["high_min_gap"] and not top["terms"]["X"]:
        conf = "High"
    elif top and gap >= r["medium_min_gap"]:
        conf = "Medium"
    else:
        conf = "Not sure"
    check = None
    if conf == "Not sure" and second:
        check = (f"Check {top['service']} directly (its health endpoint and its own latency). "
                 f"If it is healthy, the cause is more likely {second['service']}.")

    # Healthy dependencies are evidence too (does not always blame the database)
    healthy = []
    if top:
        for d in graph.get(top["service"], []):
            if d not in anoms:
                healthy.append(d)
    return {"top": top["service"] if top else None, "confidence": conf, "gap": round(gap, 2),
            "candidates": cands[:3], "all_candidates": cands, "not_sure_check": check,
            "healthy_dependencies": healthy, "baselines": baselines(members, bad_events, anoms, graph, all_services)}


def baselines(members, bad_events, anoms, graph, all_services=None):
    mset = set(members)
    errs = Counter(e["service"] for e in bad_events if e["service"] in mset)
    rnd = random.Random("|".join(sorted(members)))
    indeg = Counter(d for s, deps in graph.items() for d in deps)
    first = min(members, key=lambda m: (anoms[m].get("onset") or 0, m))
    return {
        "random": rnd.choice(sorted(members)),
        "most_errors": errs.most_common(1)[0][0] if errs else None,
        "first_anomaly": first,
        "most_depended_on": indeg.most_common(1)[0][0] if indeg else None,
    }


def _hms(t):
    from .common import hms
    return hms(t)
