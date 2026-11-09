"""Correlation: which broken service explains the others? Each unexplained one is a source.

Pure functions so they can be tested on fixtures (tests/test_rca.py).
"""
from collections import Counter, defaultdict


def correlate(anoms: dict, bad_events: list, graph: dict, cfg: dict, now: float) -> dict:
    c = cfg["correlation"]
    anoms = {k: dict(v) for k, v in anoms.items()}

    # 1. Missing data is never read as healthy: infer a dependency that >=2 services blame.
    by_dep, first = defaultdict(set), {}
    for e in bad_events:
        d = e.get("dependency")
        if not d or d == e["service"] or e["_t"] < now - c["inferred_window_s"]:
            continue
        by_dep[d].add(e["service"])
        first[d] = min(first.get(d, e["_t"]), e["_t"])
    for d, srcs in by_dep.items():
        if d not in anoms and len(srcs) >= c["inferred_min_services"]:
            anoms[d] = {"onset": float(int(first[d])), "codes": {"inferred"}, "inferred": True, "peak_p95": None,
                        "reasons": [f"inferred: {' and '.join(sorted(srcs))} report errors pointing to {d}"]}

    # 2. Where do each anomalous service's bad events point?
    # Only events since the service's current anomaly began count, so leftovers from an earlier,
    # already-healed fault never dilute the attribution of a new one.
    margin = c.get("onset_margin_s", 5)
    counts = defaultdict(Counter)
    for e in bad_events:
        a = anoms.get(e["service"])
        if a and e["_t"] >= (a.get("onset") or 0) - margin:
            counts[e["service"]][e.get("dependency") or None] += 1

    parents = {}
    for s in anoms:
        cnt = counts.get(s)
        if not cnt:
            continue
        total = sum(cnt.values())
        deps = [(d, k) for d, k in cnt.items() if d and d != s]
        if not deps:
            continue
        d, k = max(deps, key=lambda x: x[1])
        allowed = s not in graph or d in graph.get(s, [])
        if allowed and d in anoms and k / total >= c["attribution_share"]:
            parents[s] = d

    # 3. Resolve each service to its source; break cycles at the earliest onset.
    def root(s):
        seen = []
        while s in parents and s not in seen:
            seen.append(s)
            s = parents[s]
        if s in seen:  # cycle
            cyc = seen[seen.index(s):]
            r = min(cyc, key=lambda x: (anoms[x]["onset"] or 0, x))
            parents.pop(r, None)
            return r
        return s

    root_of = {s: root(s) for s in anoms}
    groups = defaultdict(list)
    for s, r in root_of.items():
        groups[r].append(s)
    recent = [e for e in bad_events if e["service"] in anoms and e["_t"] >= (anoms[e["service"]].get("onset") or 0) - margin]
    return {"recent_bad": recent, "anoms": anoms, "parents": parents, "root_of": root_of, "groups": dict(groups),
            "counts": {k: dict(v) for k, v in counts.items()}}
