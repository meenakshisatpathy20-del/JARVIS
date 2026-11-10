<div align="center">

# J.A.R.V.I.S.

**Justified Analysis, Reasoning & Visualization of Incident Systems**

*From an alert storm to an evidence-backed root cause, proven on blind, judge-chosen failures.*

![Python](https://img.shields.io/badge/Python-3.12-0057ff?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-0057ff?logo=fastapi&logoColor=white)
![Postgres](https://img.shields.io/badge/PostgreSQL-16-0057ff?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker_Compose-ready-0057ff?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-9_passing-00c26e)
![Blind drill](https://img.shields.io/badge/blind_drill-13%2F13_correct-00c26e)

NITS Hacks 9.0 · Problem Statement 1 · Software Track

### 🔴 [Live demo](https://special-space-engine-jjw7gjv69g6g3jjp6-8000.app.github.dev/) · [Blind test lab](https://special-space-engine-jjw7gjv69g6g3jjp6-8000.app.github.dev/lab)

<sub>Hosted on GitHub Codespaces. If the link does not load, the codespace is asleep: run it locally in 60 seconds with the steps below.</sub>

<img src="docs/screenshots/f1-live-incident.png" alt="J.A.R.V.I.S. dashboard during a database fault: user-db ringed as the likely root cause, auth and gateway marked as symptoms" width="900">

</div>

## Contents
[Try it](#try-it-in-60-seconds) · [Results](#results-measured-on-the-docker-stack-9-nov-2026) · [Architecture](#architecture) · [How the root cause is decided](#how-the-root-cause-is-decided) · [Screenshots](#screenshots) · [Known limits](#known-limits-honest) · [How we used AI](#how-we-used-ai) · [Judge review log](docs/JUDGE_REVIEW.md)

## The problem
When one shared component fails, every service that depends on it raises its own alert. A payment
platform shows **login failures, slow APIs and failed payments at the same time**, and the on-call
engineer chases three fires instead of one cause.

## What J.A.R.V.I.S. does
J.A.R.V.I.S. watches a small but **real** payment platform (gateway, auth, payment, one shared Postgres),
collapses the alert storm into incidents, ranks the root cause **with evidence**, measures the impact
and suggests a fix. A separate chaos controller injects faults **blindly**: J.A.R.V.I.S. sees only
telemetry, and the hidden ground truth is revealed only after it has published a signed diagnosis.

## Try it in 60 seconds
```bash
git clone https://github.com/meenakshisatpathy20-del/J.A.R.V.I.S.git
cd J.A.R.V.I.S
cp .env.example .env      # any random values work; no external API keys needed
docker compose up --build
```
1. Open **http://localhost:8000** and wait for the "Starting up / Warm-up" chip to disappear (~60 s).
2. Open **http://localhost:8000/lab**, pick a fault (or 🎲 *Surprise me*), press **BREAK**.
3. Watch the diagnosis appear, then **LOCK IN & REVEAL** the hidden ground truth.
4. Open the incident on the dashboard → **Verified fix** → **Preview fix** → **Approve & run**, and watch J.A.R.V.I.S.
   check that the platform actually recovered.

Optional: set `LAB_KEY` in `.env` to require a key for Break / Heal / Reveal and fixes on a public link (off by default).

| Blind test lab | Evidence for every point of the score |
|---|---|
| <img src="docs/screenshots/blind-lab-reveal.png" alt="Blind test lab: hidden truth user-db, J.A.R.V.I.S. said user-db, MATCH, 3/3 session scoreboard"> | <img src="docs/screenshots/f2-payment-not-db.png" alt="Payment slowdown diagnosed as payment-service with user-db marked healthy"> |

## What makes it different
| | |
|---|---|
| **Evidence-backed diagnosis** | A deterministic scoring engine picks the root cause. The LLM only explains the decision. |
| **Blind fault test** | Chaos keeps the ground truth on its own volume and network. Pick a fault, Break, diagnose, Reveal. Every diagnosis is **signed** by J.A.R.V.I.S. and verified by chaos, so a blind-test win cannot be faked. |
| **Honest by design** | Confidence is shown as words (High / Medium / Not sure), never an uncalibrated percentage. Money figures are labelled estimates. |
| **Does not always blame the database** | A payment-only fault is diagnosed as payment, with the database marked healthy. |

## Verified fix (final round)
Diagnosis is half the job. J.A.R.V.I.S. now closes the loop, **with a person in charge**:

**Preview → human approves → allow-listed action → recovery check (60 s) → ✅ Resolved or ⚠️ Escalated**

| Root cause | Action | Counts as recovered only when |
|---|---|---|
| **F1** database connections exhausted | Cap the client hogging connections (the non-protected role holding the most *idle, long-held* sessions) at 2 connections and end its idle sessions. Killing sessions alone is not enough: the hog reconnects | DB use < 70 % of 17 slots **and** login success > 95 % |
| **F3** auth-service down | Start auth-service again | Health check green **and** login success > 95 % |
| **F2** payment slowdown | **None.** No safe automatic fix exists; the runbook (timeout + circuit breaker) is shown instead | n/a |

Guardrails: only these actions can ever run · a person approves every one (name recorded in the audit log) ·
protected roles (`app`, `postgres`) are never capped · it refuses to act on weak evidence (fewer than 3 idle
sessions held) · during a blind test the fix stays **locked until the reveal**, so it can never leak the answer ·
**Undo** for the cap, which is also removed automatically when the incident resolves · if recovery is not seen in
60 s the action is reported as **Escalated**, never as success.

## Benchmark vs simple baselines (final round)
15 blind runs (5 per fault, interleaved) compare J.A.R.V.I.S. with four simple rules on the same incidents:
random, most errors, first to break, and most depended-on (always the database).
Full table and every run: **[docs/BENCHMARK.md](docs/BENCHMARK.md)** (generated by `scripts/benchmark.py`).

<!-- BENCHMARK_SUMMARY -->

## Results (measured on the Docker stack, 9 Nov 2026)
Across every drill run today (Docker and local, browser and script) J.A.R.V.I.S. diagnosed **13 of 13**
blind faults correctly after the fixes in [docs/JUDGE_REVIEW.md](docs/JUDGE_REVIEW.md). One recorded series:
Blind fault drill (`scripts/fault_drill.py`): chaos injects, J.A.R.V.I.S. diagnoses from telemetry
only, the diagnosis is committed, then the hidden ground truth is revealed.

| Run | Fault | Ground truth | J.A.R.V.I.S. | Confidence | Severity | Time to diagnose |
|---|---|---|---|---|---|---|
| 1 | F1 DB exhaustion | user-db | user-db ✓ | High | SEV1 | 4.5 s |
| 2 | F2 payment slowdown | payment-service | payment-service ✓ | High | SEV2 | 6.3 s |
| 3 | F1 DB exhaustion | user-db | user-db ✓ | High | SEV1 | 4.2 s |
| 4 | F2 payment slowdown | payment-service | payment-service ✓ | High | SEV2 | 6.0 s |
| 5 | F1 DB exhaustion | user-db | user-db ✓ | High | SEV1 | 3.9 s |
| 6 | F2 payment slowdown | payment-service | payment-service ✓ | High | SEV2 | 6.8 s |
| 7 | F3 auth down | auth-service | auth-service ✓ | High | SEV1 | 1.8 s |

7 of 7 correct, 0 phantom incidents after 7 heals. A small sample: this is a demo result, not a
benchmark (15 runs against 4 baselines is planned for the final round). See
[docs/JUDGE_REVIEW.md](docs/JUDGE_REVIEW.md) for the gaps we found in our own build and how we fixed them.

## Quick start (Docker)
```bash
cp .env.example .env          # set any random values; GEMINI_API_KEY is optional
docker compose up --build
```
On Windows PowerShell use `Copy-Item .env.example .env`. Stop with `Ctrl+C`, then `docker compose down`
(`docker compose down -v` also wipes the database and saved incidents).
- Dashboard: http://localhost:8000
- Blind test lab: http://localhost:8000/lab

Let the load generator run **at least 60 s** before injecting anything, so the z-score baseline can warm up.
```bash
curl -X POST localhost:8090/inject -H "Content-Type: application/json" -d '{"fault":"db_pool"}'
curl -X POST localhost:8090/heal
```
Faults: `db_pool` (F1), `payment_slow` (F2), `auth_down` (F3), `payment_slow+auth_down`, `random`.

### Hosted demo (one public URL)
```bash
docker compose -f docker-compose.yml -f docker-compose.deploy.yml up -d --build
```
Serves the dashboard, lab and chaos behind one Caddy proxy on port 8000 (HTTPS automatically with a domain).
On a fresh Ubuntu cloud VM (Oracle Cloud Always Free, Azure, AWS, GCP) one command does everything:
`curl -fsSL https://raw.githubusercontent.com/meenakshisatpathy20-del/J.A.R.V.I.S/main/scripts/vm_setup.sh | bash`.
Step-by-step guides (Oracle Cloud, GitHub Codespaces): [docs/DEPLOY.md](docs/DEPLOY.md).

### Without Docker (fallback)
Needs Python 3.11+ and a local Postgres 16 started with `max_connections=20`.
```bash
pip install -r jarvis/requirements.txt
bash scripts/setup_local_db.sh
bash scripts/run_local.sh          # 'bash scripts/run_local.sh stop' to stop
python scripts/fault_drill.py db_pool payment_slow auth_down
```

## Architecture
```mermaid
flowchart LR
    LG[Load generator<br/>20 synthetic users] --> GW[gateway]
    GW --> AUTH[auth-service]
    GW --> PAY[payment-service]
    PAY --> AUTH
    AUTH --> DB[(user-db<br/>Postgres 16<br/>max_connections=20)]
    PAY --> DB

    GW -. telemetry .-> J
    AUTH -. telemetry .-> J
    PAY -. telemetry .-> J
    DB -. connection count .-> J

    subgraph J[J.A.R.V.I.S. :8000]
      direction TB
      I[Ingest + PII scrub + dedup] --> D[Detect] --> C[Correlate] --> R[Root-cause score] --> IM[Impact + severity] --> RB[Runbooks + report]
    end

    CH[Chaos controller :8090<br/>hidden ground truth] == faults ==> DB
    CH == faults ==> PAY
    CH == faults ==> AUTH
    J -- signed diagnosis --> LAB[Blind test lab] -- commit, then reveal --> CH
```
J.A.R.V.I.S. and chaos share **no Docker network**: J.A.R.V.I.S. sees only telemetry, and chaos reveals the
truth only after it receives a diagnosis signed by J.A.R.V.I.S. Auth and payment both read the same Postgres,
so one database fault genuinely breaks login and payment together; payment also calls auth's `/verify`.

## Fault scenarios
| Fault | Injection | Correct root cause | Evidence J.A.R.V.I.S. cites |
|---|---|---|---|
| F1 DB connection exhaustion | Chaos (non-superuser role `chaos`) holds every free ordinary slot | `user-db` | All 17 ordinary slots in use; auth errors name `dependency=user-db`; DB saturated first |
| F2 Payment slowdown | Token-protected admin endpoint adds 2.5 s before payment opens its DB connection (logs nothing) | `payment-service` | Payment's own latency up while user-db and auth stay healthy; gateway slow only on `/pay` |
| F3 Auth down | Chaos stops the auth container | `auth-service` | Health check down, auth silent while traffic still arrives; gateway and payment errors name `dependency=auth-service` |
| F2 + F3 | Both | see note | Split into two incidents is proven in unit tests only (see Known limits) |

## How the root cause is decided
**Detection** (every second, per service): error rate, p95 latency and request rate from 1-second
buckets (3-second sliding window). Anomaly when a rule fires 3 seconds in a row: z-score ≥ 3 against
a 120 s baseline (after 60 s warm-up), or a hard rule: error rate > 5 %, p95 > 1 s, health check
down, silent while the gateway still sends it traffic, all 17 ordinary DB slots in use, or the DB
monitor cannot read stats.

**Correlation**: for each anomalous service, look at where its error/slow events point (the
`dependency` field, checked against the dependency graph, counting only events since its own
anomaly began). If ≥ 50 % point to an anomalous dependency D, it is a symptom of D. A service no
anomalous dependency explains is a **source**; each source opens one incident. If ≥ 2 services blame
a dependency whose own metrics are missing, that dependency is marked anomalous (inferred). The same
source within 10 minutes reopens its old incident.

**Root cause score** (weights hand-set in `config.yaml` before testing):

`score(s) = 3·A(s) + 2·O(s) + 2·L(s) + 1·C(s) − 3·X(s)`

| Term | Meaning |
|---|---|
| A attribution | Share of the incident's error/slow events that point to s |
| O onset | s broke first (ties within 2 s count for all) |
| L local fault | s shows its own failure: DB slots full, health down, silent, or its own latency rose while its dependencies stay healthy |
| C change | Real deploy/config change on s (final round; chaos actions never count) |
| X contradiction | s is broken but an anomalous dependency explains its errors |

**Confidence**: High = top score ≥ 5, ≥ 2 points ahead, no contradiction. Medium = ≥ 1 point ahead.
Not sure = closer than 1 point; J.A.R.V.I.S. names both candidates and the check that separates them.

**Baselines** shown with every incident: random, most errors, first anomaly, most depended-on.

## Impact, severity, recommendations
- Affected services and users, failed logins / payments per minute, slow payments per minute.
- **Estimated ₹ at risk per minute** = (successful payments/min in the healthy baseline − now) × average
  payment amount. Labelled *estimated, synthetic traffic* everywhere.
- Severity from fixed thresholds (never the LLM): SEV1 login or payment success < 50 % over 10 s;
  SEV2 50–95 % or p95 > 2 s on a core route; SEV3 otherwise.
- Runbook fix per root-cause signature, each citing the evidence it addresses.

## LLM, privacy and fallbacks
The Gemini API (temperature 0, 3 s timeout) receives one structured summary per incident:
severity, ranked candidates with evidence sentences, impact numbers, ≤ 20 timeline items. **No raw
log lines, no user IDs, no per-user amounts.** Free text is scrubbed (cards, emails, phone numbers,
bearer tokens/JWTs) before it is stored or sent. Without a key, or on timeout/rate-limit, a fixed
template report is used. The dashboard never waits for the LLM.

## Three input routes, one schema
| Route | Endpoint | |
|---|---|---|
| Live telemetry | `POST /ingest` | Services batch events every second; collector polls Postgres and /health |
| Log-file upload | `POST /upload` | `.jsonl` as-is; plain text `<date> <time> <LEVEL> [<service>] <message>` with `key=value` pairs; unmatched lines kept as `service=unknown` |
| External alerts | `POST /webhook/alert` | Alertmanager-style JSON (`alerts[].labels.service`, `alertname`, `severity`, `startsAt`) |

Try the upload with `samples/db_pool_incident.log` (Data sources tab).

## Endpoints
| Component | Port | Endpoints |
|---|---|---|
| J.A.R.V.I.S. | 8000 | `GET /`, `GET /lab`, `POST /ingest`, `POST /upload`, `POST /webhook/alert`, `GET /incidents`, `GET /incidents/{id}`, `GET /incidents/{id}/report`, `GET /incidents/{id}/attestation`, `GET /incidents/{id}/fix/preview`, `POST /incidents/{id}/fix/unlock`, `POST /incidents/{id}/fix`, `GET /api/actions`, `POST /api/actions/{id}/undo`, `WS /ws` |
| gateway | 8001 | `POST /login`, `POST /pay`, `GET /health` |
| auth-service | 8002 | `POST /login`, `POST /verify`, `GET /health` |
| payment-service | 8003 | `POST /pay`, `GET /health` |
| chaos | 8090 | `POST /inject`, `POST /heal`, `POST /commit` (signed diagnoses only), `GET /reveal` |
| user-db | 5432 | Postgres 16, `max_connections=20` |

## Repository
```
docker-compose.yml   config.yaml   .env.example
db/                  init.sh (roles), schema.sql.txt (tables + 20 synthetic users)
platform/common/     telemetry.py: emit() batches and POSTs to /ingest every 1 s
platform/gateway|auth|payment|loadgen/
chaos/main.py        /inject /heal /commit /reveal; truth file on a chaos-only volume
jarvis/              app.py detect.py correlate.py rca.py impact.py runbooks.py explain.py engine.py collector.py static/
samples/             db_pool_incident.log
tests/test_rca.py    F1, F2, F3, F2+F3, inferred dependency, PII scrub
scripts/             run_local.sh, setup_local_db.sh, fault_drill.py
```
Run the tests: `pip install pytest pyyaml && python -m pytest -q tests`

## Security of the demo
- **Optional lab key:** set `LAB_KEY` to require a key (header `x-lab-key`) for Break / Heal / Reveal and fix approval.
  Off by default so judges can use the lab freely; one fault at a time, and Heal restores everything.
- `/ingest` and `/webhook/alert` require the `x-ingest-token` header (blocks fake telemetry).
- The dashboard and chaos are published on `127.0.0.1` only.
- No external API keys are needed. The values in `.env` are passwords you choose; `GEMINI_API_KEY` is optional.
- Incidents are saved to `incidents.json` (Docker volume `jarvis-data`) and survive a restart.

## Screenshots
**Healthy platform**: live topology, traffic counters and the four-step guide.
<img src="docs/screenshots/dashboard-healthy.png" alt="Healthy dashboard with the four-step how-it-works guide">

**F1 · database connection exhaustion**: the alert storm collapses into one SEV1 incident; user-db is ringed as the root cause, auth and gateway are symptoms.
<img src="docs/screenshots/f1-live-incident.png" alt="F1 live incident on the dashboard">

**Why J.A.R.V.I.S. believes it**: every point of the score is a sentence of evidence, with impact in users and estimated rupees.
<img src="docs/screenshots/f1-evidence.png" alt="F1 incident detail: score table, evidence, impact">

**F2 · payment slowdown**: diagnosed as payment-service with user-db and auth marked healthy. The "most depended-on" rule would have blamed the database.
<img src="docs/screenshots/f2-payment-not-db.png" alt="F2 diagnosed as payment-service, database healthy">

**Blind test lab**: hidden ground truth vs J.A.R.V.I.S.'s signed answer, with a session scoreboard.
<img src="docs/screenshots/blind-lab-reveal.png" alt="Blind test lab reveal: MATCH">

**Data sources**: log-file upload and Alertmanager-style webhook feed the same engine.
<img src="docs/screenshots/data-sources.png" alt="Data sources tab: upload and webhook">

<img src="docs/screenshots/mobile.png" alt="Dashboard on a phone" width="260">

## Known limits (honest)
- **F2 + F3 together**: payment cannot verify tokens while auth is down, so its slowdown is masked and
  the live system reports one incident (auth-service). The split of two independent sources is
  proven in `tests/test_rca.py`, not claimed live.
- Detection takes a few seconds by design (3-second runs plus 1-second telemetry batching).
- Everything shares one machine clock. In production we would order events by trace timestamps and
  treat anything within 2 s as a tie.
- All traffic and money figures are synthetic.

## How we used AI
We believe in being open about this.

- **While building:** we used an AI coding assistant to speed up implementation:
  writing and refactoring code, generating tests and fixtures, debugging, and drafting documentation.
- **What the team owned:** the problem framing, the design and build plan (fault scenarios, blind-test rules,
  the evidence-score formula and its weights, the honesty rules such as "confidence in words, never a
  percentage"), every product decision, and the review and testing of the system. Every result in this README
  comes from running the system, not from the assistant's claims.
- **Inside the product:** the root cause is chosen by a deterministic evidence score, never by an AI. The
  optional Gemini API only turns a finished diagnosis into a readable paragraph, and the app falls back to a
  template report without it.

## Team
| Name | Role |
|---|---|
| Meenakshi Satpathy | Developer |
| Manas Girdhar | Developer |

## Roadmap
Done in the final round: verified fix (F1, F3) and the 15-run benchmark. Next: what-if blast-radius preview,
browser voice summary, OpenTelemetry traces for attribution, Kubernetes events as change signals.
