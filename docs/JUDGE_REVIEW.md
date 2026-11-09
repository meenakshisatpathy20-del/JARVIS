# Judge-style review log

We reviewed our own build the way a judge would: try to break it, look for anything scripted or
over-claimed, and fix what we found. Every row below was found by testing the running system.

## Gaps found and fixed during the build
| # | What a judge would see | Root cause | Fix | Verified by |
|---|---|---|---|---|
| 1 | F1 (DB exhaustion) blamed **auth-service** instead of user-db | The monitor's own superuser session takes an ordinary slot while free ones exist, so the hog held 16 and the count (which excluded the monitor) never reached 17/17 | Count every client connection, the monitor's included | Fault drill: F1 → user-db, High |
| 2 | F2 (payment slowdown) opened **two** incidents (gateway and payment) | Gateway's login errors from the previous fault were still in the 60 s window and diluted its attribution to payment below 50 % | Attribute only events since each service's own anomaly began | Fault drill: F2 → 1 incident, payment-service, High |
| 3 | A **phantom incident** opened ~10 s after every heal | The cause recovers first; its symptoms' last tail errors then looked like a new, unexplained source | A dependency that recovered in the last 60 s still explains errors pointing at it | Unit test `test_recovery_tail_does_not_open_phantom_incident` + drill |
| 4 | Impact showed failed logins **left over from the previous fault**, and 1,165 failures/min from a 5-second window | Impact used a fixed 60 s window and divided by tiny elapsed times | Impact counts from the incident's own start, with a minimum window | Drill: F2 shows 0 failed logins |
| 5 | F1 title said only "Login failures", while the PS story is login **and** payment | Payments do not fail in F1: users cannot log in, so payments stall | Title says "payments stalled" when successful payments drop below half the baseline, never "payment failures" unless requests failed | Dashboard screenshot |
| 6 | Dashboard graph blank without internet | Cytoscape loaded from a CDN | Cytoscape 3.28.1 (MIT) vendored and served locally | Screenshots with the CDN blocked |
| 7 | Clicking an incident during an active fault sometimes did nothing | Cards re-rendered every second and swallowed the click | Re-render only on change; open on pointer-down via delegation | Playwright click test |
| 8 | "Most depended-on" baseline said auth-service | Tie on direct callers (auth and user-db both have 2) | Count direct and indirect dependents (user-db: 3) | Unit test F2 |
| 9 | Report showed literal `**bold**` markers | Markdown in a plain-text panel | Minimal bold rendering after HTML escaping | Screenshot |
| 10 | `/ingest` and `/webhook/alert` rejected valid JSON (422) | FastAPI did not treat a `list \| dict` parameter as the body | Read the raw request JSON | curl + live telemetry |

## Claims we checked before making them
- **Blindness**: `docker compose exec jarvis` cannot resolve `chaos` (no shared network) and has no `/data`
  volume; chaos actions never enter the event stream; Reveal needs a committed diagnosis first.
- **Does not always blame the DB**: F2 is diagnosed as payment-service with user-db listed as healthy,
  while the "most depended-on" baseline says user-db.
- **Confidence**: High only when the top score is ≥ 5, ≥ 2 ahead and uncontradicted; never a percentage.

## Still open (stated honestly, not hidden)
- **F2 + F3 together** merge into one live incident (auth-service): payment cannot verify tokens while
  auth is down, so its slowdown is masked. The split of independent sources is proven in unit tests only.
- The Gemini narrative could not be exercised from our build sandbox (no outbound access); the
  template report is the tested path and the fallback is automatic.
- Chaos mounts the Docker socket to stop the auth container. It is published on 127.0.0.1 only and is a
  demo tool, not something to run in production.
- Detection needs ~4–7 s (3-second runs + 1-second telemetry batches). That is a deliberate trade
  against false alarms.
