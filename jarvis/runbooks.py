"""Runbook map: a suggested fix per root-cause signature, each citing the evidence it answers."""


def recommend(rca: dict, anoms: dict) -> list:
    top = rca.get("top")
    if not top:
        return []
    cand = rca["candidates"][0]
    codes = anoms.get(top, {}).get("codes", set())
    cite = cand["evidence"][0] if cand["evidence"] else ""
    recs = []
    if top == "user-db" or codes & {"db_full", "db_unreadable"}:
        recs.append({
            "title": "Database connections exhausted",
            "steps": ["Find long-running or idle sessions in pg_stat_activity and end them (pg_terminate_backend).",
                      "Cap each service's connection pool so one client cannot take every slot.",
                      "Add a statement_timeout and idle_in_transaction_session_timeout."],
            "addresses": next((e for e in cand["evidence"] if "Own fault" in e), cite),
            "automated": "Final round: end the hog sessions after approval, then verify connections < 70% and login success > 95%.",
        })
    elif codes & {"health", "silent"}:
        recs.append({
            "title": f"{top} is down",
            "steps": [f"Restart {top} and confirm its /health endpoint returns 200.",
                      f"Check {top}'s last deploy and its final log lines before it stopped.",
                      "Make callers fail fast with a clear retry message while it is down."],
            "addresses": next((e for e in cand["evidence"] if "Own fault" in e), cite),
            "automated": "Final round: restart after approval, then check health and error rate.",
        })
    elif codes & {"p95", "z_p95"}:
        recs.append({
            "title": f"{top} slowdown",
            "steps": ["Add a timeout and a circuit breaker on the slow call inside the handler.",
                      "Fail fast with a clear retry message instead of holding the user.",
                      f"Add {top} workers / replicas if the slowness is load-related."],
            "addresses": next((e for e in cand["evidence"] if "Own fault" in e), cite),
            "automated": "None tonight: suggestion only.",
        })
    else:
        recs.append({
            "title": f"Errors originate in {top}",
            "steps": [f"Inspect {top}'s most frequent error type and its last deploy or config change.",
                      "Roll back the last change if the errors started right after it."],
            "addresses": cite,
            "automated": "None tonight: suggestion only.",
        })
    if rca.get("confidence") == "Not sure" and rca.get("not_sure_check"):
        recs.insert(0, {"title": "Confirm the cause first", "steps": [rca["not_sure_check"]],
                        "addresses": "The evidence is split between two candidates.", "automated": ""})
    return recs
