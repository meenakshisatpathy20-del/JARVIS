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

## Round 2: loophole hunt (trying to cheat our own blind test)
| # | Probe | Before | Fix | After |
|---|---|---|---|---|
| 11 | POST a forged answer straight to chaos `/commit` | Accepted, Reveal said MATCH | J.A.R.V.I.S. signs every diagnosis (HMAC-SHA256, key shared only through `.env`, never the network); chaos verifies the signature and rejects any diagnosis made before the fault started | `403 diagnosis signature invalid` |
| 12 | "Surprise me" could draw F2+F3 | Would MISS live (known merge) | Random draws single faults only | 3/3 MATCH in a browser run incl. Surprise |
| 13 | POST fake telemetry to `/ingest` | Accepted | Shared `x-ingest-token` on `/ingest` and `/webhook/alert` | `401` |
| 14 | Dashboard reachable from the venue Wi-Fi | `0.0.0.0:8000` | Published on `127.0.0.1` only | |
| 15 | Restart J.A.R.V.I.S. mid-demo | All incidents lost | Saved to `incidents.json` every 5 s and on shutdown; reloaded on start (playbook requirement) | 2 saved → 2 reloaded |
| 16 | Upload a huge file | No limit | 5 MB cap | `413` |
| 17 | A 2-second auth blip blamed the **gateway** | Only one caller saw it, inference needed two | Single-caller inference when one dependency clearly dominates a caller's errors (ties never inferred) | Unit test `test_single_caller_blip_blames_the_callee_not_the_caller` |

## Round 3: first run on a Windows laptop
| # | Seen | Fix |
|---|---|---|
| 18 | Two incidents opened by themselves right after `docker compose up` | Load generator waits for every service's `/health`; no new incidents in J.A.R.V.I.S.'s first 45 s; a new incident needs ≥ 5 correlated events unless a member is down or an external alert names it. Verified: 0 incidents during start-up on Docker |
| 19 | Evidence said 11:59:09 while cards said 5:29 PM | Containers run on UTC; J.A.R.V.I.S. and chaos now use India time (`TZ=IST-5:30`, works without tzdata; override with `DISPLAY_TZ`) |
| 20 | Scripts broke after a Windows Git checkout (CRLF) | `.gitattributes` forces LF for shell, Python and YAML files |

## Round 4: final round (verified fix + benchmark)
| # | Gap a judge would raise | Fix | Verified by |
|---|---|---|---|
| 21 | "It only diagnoses; does it fix anything?" | Verified fix: preview → human approval → allow-listed action → 60 s recovery check → Resolved / Escalated, with audit log and Undo | Live on Docker: F1 recovered in 10.1 s, F3 in 10.1 s |
| 22 | Anyone with the public link could Break / Heal the demo | **Optional lab key** for inject / heal / commit / reveal and fix approval (off by default so judges can use the lab freely) | 401 without the key when set |
| 23 | A fix could leak the blind-test answer (e.g. the hog's role name) | Fix stays locked, and its preview hides details, until the reveal | Preview returns "Locked" before reveal |
| 24 | Capping "whichever role has most connections" could hit the app itself | Only roles holding idle, long-held sessions; protected roles (`app`, `postgres`) never capped; refuses below 3 idle sessions | Unit tests + live run capped only the hog |
| 25 | A cap left behind would break the next test | Cap removed automatically when the incident resolves (and by Undo) | Roles back to unlimited after heal |
| 26 | "Is 13/13 just luck vs a simple rule?" | 15-run blind benchmark against 4 baselines on the same incidents | `docs/BENCHMARK.md` |
| 27 | F3 recovery message showed DB numbers | F3 reports auth-service health + login success instead | Code review |

## UI/UX improvements in round 2
- Empty state explains the pipeline in four steps (Collect, Correlate, Score, Prove) with a link to the lab.
- Every live incident card shows "diagnosed N s after the first anomaly".
- Lab result shows "signed by J.A.R.V.I.S. · verified by chaos" and a link that opens the incident on the dashboard (`/?incident=ID`).
- Lab session scoreboard (N / M correct this session).
- Open incidents listed first; phone layout tuned.

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
