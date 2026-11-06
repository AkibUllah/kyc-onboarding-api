<!-- topics: fastapi,python,kyc,fintech,compliance,state-machine,risk-scoring,file-upload,sqlalchemy -->
# KYC Onboarding API

FastAPI service that runs a mobile-wallet style KYC onboarding workflow: applicant intake, NID and selfie document uploads, pluggable verification checks with weighted risk scoring, a reviewer queue with role-based decisions, and SLA statistics built from the audit trail.

[![CI](https://github.com/AkibUllah/kyc-onboarding-api/actions/workflows/ci.yml/badge.svg)](https://github.com/AkibUllah/kyc-onboarding-api/actions/workflows/ci.yml)
[![CodeQL](https://github.com/AkibUllah/kyc-onboarding-api/actions/workflows/codeql.yml/badge.svg)](https://github.com/AkibUllah/kyc-onboarding-api/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/AkibUllah/kyc-onboarding-api)](https://github.com/AkibUllah/kyc-onboarding-api/releases)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![FastAPI](https://img.shields.io/badge/fastapi-0.110%2B-009688)
![License](https://img.shields.io/badge/license-MIT-green)

## Try it live

| | |
|---|---|
| API reference | https://akibullah.github.io/kyc-onboarding-api/ — Swagger UI built from the exported OpenAPI document (static; no backend behind it) |
| Run in your browser | [![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/AkibUllah/kyc-onboarding-api?quickstart=1) — starts API + PostgreSQL with `docker compose` and runs `scripts/demo_flow.py` (one auto-approved applicant, one sanctions hit in the queue); open forwarded port 8000 at `/docs` with `X-API-Key: agent-dev-key`, `reviewer-dev-key` or `admin-dev-key`. |
| One-click deploy | [![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/AkibUllah/kyc-onboarding-api) — `render.yaml` provisions the web service and a free PostgreSQL; you are prompted for `API_KEYS` (`key:role:name,...`). Uploaded documents live on the container disk, which the free plan does not persist. |
| Local | `docker compose up` (see Quick Start) |

## Screenshots

![Swagger UI API reference](docs/images/api-reference.png)

Live API reference on GitHub Pages: applicants, documents, review queue and stats.

![demo flow against a local instance](docs/images/terminal-demo.png)

A real run of `scripts/demo_flow.py`: a clean applicant is auto-approved, a sanctions hit lands in the review queue at high risk and is rejected, then the daily stats.

## Why this exists

Opening a mobile financial services account in Bangladesh means collecting a national ID (front and back), a selfie, and a handful of profile fields, then deciding within a regulator-mandated window whether the person is who they say they are. In practice the hard parts are not the form: they are validating the three NID formats in circulation, refusing spoofed uploads, catching the same ID or photo being reused across applications, screening names against sanctions and PEP lists with Bangla transliteration variance, and proving to an auditor who moved an application into which state and when. This service is that workflow core, written so each verification check is a small, independently testable plug-in and every state change is a row with an actor.

## Features

- Applicant lifecycle `draft -> submitted -> under_review -> approved | rejected | needs_more_info -> submitted`, enforced by an explicit transition table; every transition stored with actor, role, reason and time spent in the previous state
- Document intake for `nid_front`, `nid_back`, `selfie`: magic-byte type sniffing (JPEG / PNG / PDF), declared-type mismatch rejection, size limit, path-component stripping, SHA-256 hashing, replace-on-reupload
- Storage behind a `Storage` protocol: `LocalStorage` by default, `S3Storage` for any S3-compatible endpoint (boto3 imported lazily via the `[s3]` extra)
- Pluggable checks registered with a decorator: NID format (10 / 13 / 17 digits, birth-year prefix), date-of-birth consistency (adult, NID year agreement), face match (protocol + deterministic stub that fails identical files), sanctions / PEP screening against a CSV with normalised fuzzy name matching and DOB confirmation, duplicate identity (reused document hashes, NID already approved elsewhere)
- Weighted risk scoring to `low` / `medium` / `high` with configurable thresholds and profile factors; low-risk applicants auto-approved by `system` when enabled
- Checks run in FastAPI `BackgroundTasks` after submission; admins can re-run them synchronously
- Reviewer endpoints: queue filtered by risk or unassigned cases, claim (one reviewer per applicant, admin override), decisions that require a reason for reject / needs-more-info
- Auto-assignment with `POST /v1/review/next`: overdue cases first, then risk and wait time, per-reviewer open-case caps, reviewer availability, expiring claims, and no reviewer ever handles an application they submitted (see [Review workflow](#review-workflow))
- Three API-key roles (`agent`, `reviewer`, `admin`) through a single `require_role` dependency
- Daily stats (created, submitted, approved, auto-approved, rejected, by risk level, average decision hours, open backlog) and SLA stats (average and max hours per state, applicants currently breaching the review SLA)
- Uniform error envelope, request-id middleware, `/health` probe with DB check, OpenAPI docs at `/docs`

## Architecture

```mermaid
flowchart LR
    Agent[Onboarding app\nagent key] -->|create / upload / submit| API[FastAPI]
    Reviewer[Compliance officer\nreviewer key] -->|next / queue / claim / decide| API
    API --> WF[workflow.py\nstate machine + audit rows]
    API --> Docs[services/documents\nsniff, hash, store]
    Docs --> Storage[(Local disk or S3)]
    API -->|BackgroundTasks| Runner[checks/runner]
    Runner --> NID[nid_format]
    Runner --> DOB[dob_consistency]
    Runner --> Face[face_match\nFaceMatcher protocol]
    Runner --> Sanc[sanctions_pep\nCSV screen]
    Runner --> Dup[duplicate_identity]
    Runner --> Risk[risk.assess\nweighted score -> level]
    Risk -->|low + auto-approve| WF
    WF --> DB[(PostgreSQL / SQLite)]
    API --> Stats[services/stats\ndaily + SLA]
    API --> Review[services/review\ndispatch + claim expiry]
    Review --> DB
```

Key decisions:

- **Transitions are data.** `workflow.transition` is the only way an applicant changes state. It validates against `TRANSITIONS`, writes a `StateTransition` with `seconds_in_previous_state`, and stamps `submitted_at` / `decided_at`. SLA reporting is a `GROUP BY from_status` over that table, nothing is computed from logs.
- **Checks are plug-ins, failures are points.** A check is `(CheckContext) -> CheckOutcome`; the runner never knows which checks exist. A crashing check is recorded as a failed outcome with a modest penalty instead of blocking the pipeline. Scores are additive and capped, so adding a check does not require re-tuning thresholds.
- **Submission is accepted, processing is asynchronous.** `POST /submit` returns 202 and schedules `process_submission` with its own DB session. The same function is callable synchronously by admins, which is also how the tests exercise it deterministically.
- **Assignment is one statement.** `POST /v1/review/next` picks and assigns in a single `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`, so a case cannot reach two reviewers and concurrent reviewers do not wait on each other. Taking the reviewer's own row first serialises their requests, which is what makes the open-case cap hold.
- **Uploads are untrusted.** Type comes from the bytes, not the header; a PNG declared as JPEG is rejected because that mismatch is a fingerprint of tampering tools. Filenames are reduced to their last path component before storage.
- **Stubs keep the real seams.** `StubFaceMatcher` and the sample sanctions CSV are clearly labelled sample implementations, but the `FaceMatcher` protocol and `SANCTIONS_CSV` setting are the actual integration points for a biometric vendor and a consolidated list.

## Tech Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.10+ |
| Framework | FastAPI, Pydantic v2, pydantic-settings |
| Persistence | SQLAlchemy 2.0 (typed `Mapped` models); PostgreSQL 16 in compose, SQLite for dev and tests |
| Storage | Local filesystem (default) or S3-compatible via boto3 (`[s3]` extra) |
| Server | uvicorn, non-root Docker image |
| Tooling | pytest + httpx TestClient, ruff, GitHub Actions (3.10 / 3.11, PostgreSQL 16) |

## Quick Start

### Docker

```bash
git clone https://github.com/AkibUllah/kyc-onboarding-api.git && cd kyc-onboarding-api
docker compose up --build -d
open http://localhost:8000/docs
python scripts/demo_flow.py http://localhost:8000   # needs: pip install httpx
```

### Local

```bash
make install            # venv + pip install -e ".[dev]"
cp .env.example .env
make run                # uvicorn on :8000, SQLite at ./kyc.db, files under ./storage
make test
```

### The flow in curl

```bash
BASE=http://127.0.0.1:8000
AGENT='X-API-Key: agent-dev-key'; REVIEWER='X-API-Key: reviewer-dev-key'

ID=$(curl -s -X POST $BASE/v1/applicants -H "$AGENT" -H 'Content-Type: application/json' -d '{
  "full_name": "Rahim Uddin Ahmed", "nid_number": "1990123456789", "date_of_birth": "1990-05-14",
  "phone": "01711223344", "email": "rahim@example.com", "address": "Dhanmondi, Dhaka",
  "occupation": "Shop owner", "monthly_income_bdt": 45000}' | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')

for KIND in nid_front nid_back selfie; do
  curl -s -X POST $BASE/v1/applicants/$ID/documents -H "$AGENT" -F kind=$KIND -F file=@$KIND.jpg
done
curl -s -X POST $BASE/v1/applicants/$ID/submit -H "$AGENT"          # 202, checks run in background
curl -s $BASE/v1/applicants/$ID -H "$AGENT"                         # status, risk_level, risk_score
curl -s $BASE/v1/applicants/$ID/checks -H "$REVIEWER"               # per-check outcomes
curl -s $BASE/v1/review/queue -H "$REVIEWER"
curl -s -X POST $BASE/v1/review/$ID/decision -H "$REVIEWER" -H 'Content-Type: application/json' \
  -d '{"decision": "approve", "reason": "Documents verified"}'
```

## Review workflow

Reviewers normally call `POST /v1/review/next` and work what it returns. Picking a specific case from `GET /v1/review/queue` with `POST /v1/review/{id}/claim` still works and follows the same rules.

**Dispatch order.** Cases past `REVIEW_SLA_HOURS` come first, most overdue first, whatever their risk, so a steady stream of high-risk cases cannot starve older ones. Cases inside the SLA go by risk (high, medium, low), then longest wait. The clock is the one `/v1/stats/sla` reports on: time since the applicant entered `under_review`.

**Who can receive a case.**

| Rule | Applies to | Result |
| --- | --- | --- |
| One holder per case | `next`, `claim` | `next` moves on to the following case; `claim` answers 409 |
| Below the open-case cap | `next`, `claim` | 409 `reviewer_at_capacity`, with `open_cases` and `max_open_cases` |
| Reviewer is available | `next`, `claim`, deciding a case they do not hold | 409 `reviewer_unavailable` |
| Not the applicant's creator or any submitter, in any round | `next`, `claim`, decisions | `next` passes over it; the others answer 403. Admins are included |

The cap is `MAX_OPEN_CASES` unless an admin sets a per-reviewer value. Reviewers pause themselves with `PATCH /v1/review/reviewers/me`. Pausing stops new work only: cases already held can be finished, but they cannot be renewed, so anything left expires back to the queue.

**Claim expiry.** A claim lasts `CLAIM_TTL_MINUTES`. Claiming a case you already hold renews it. Expired claims are released at the start of every review operation, with no background worker, and keep their place in line because the SLA clock is not reset. The former holder can no longer decide the case once someone else has it. `GET /v1/applicants/{id}/assignments` shows who held a case and when claims lapsed.

**Re-submission.** An applicant coming back from `needs_more_info` returns to the queue unassigned.

`POST /v1/review/next` answers 204 when nothing is eligible for the caller.

```bash
curl -s -X POST $BASE/v1/review/next -H "$REVIEWER"                        # next case, or 204
curl -s -X PATCH $BASE/v1/review/reviewers/me -H "$REVIEWER" \
  -H 'Content-Type: application/json' -d '{"available": false}'            # pause
curl -s $BASE/v1/review/reviewers -H "$REVIEWER"                           # load per reviewer
```

Guarantees against double assignment and the cap rely on PostgreSQL row locks, or on SQLite's single writer for local use. On SQLite a decision racing an assignment is not protected, which only matters if you run concurrent reviewers on it.

## API

All business routes are under `/v1`. Roles: `agent` (A), `reviewer` (R), `admin` (always allowed).

| Method | Path | Roles | Description |
| --- | --- | --- | --- |
| POST | `/v1/applicants` | A | Create a draft applicant (BD phone and NID normalisation) |
| GET | `/v1/applicants?status=&risk_level=&limit=&offset=` | A R | Paginated listing |
| GET | `/v1/applicants/{id}` | A R | Full record incl. documents and missing documents |
| PATCH | `/v1/applicants/{id}` | A | Edit while `draft` or `needs_more_info` |
| POST | `/v1/applicants/{id}/documents` | A | Multipart `kind` + `file`; replaces an existing document of that kind |
| GET | `/v1/applicants/{id}/documents` | A R | Document metadata |
| GET | `/v1/applicants/{id}/documents/{doc_id}/content` | R | Download the stored file |
| POST | `/v1/applicants/{id}/submit` | A | `-> submitted`, schedules verification checks (202) |
| POST | `/v1/applicants/{id}/checks/run` | admin | Run or re-run checks synchronously |
| GET | `/v1/applicants/{id}/checks` | R | Check outcomes for every run |
| GET | `/v1/applicants/{id}/history` | A R | Audit trail of state transitions |
| GET | `/v1/applicants/{id}/assignments` | R | Who held the applicant: claims, auto-assignments, expiries |
| GET | `/v1/review/queue?risk_level=&mine=&unassigned=` | R | Applicants under review, oldest submission first |
| POST | `/v1/review/next` | R | Assign the next eligible case to the caller; 204 if there is none |
| POST | `/v1/review/{id}/claim` | R | Assign a specific case to the caller, or renew their claim |
| POST | `/v1/review/{id}/decision` | R | `{decision: approve\|reject\|needs_more_info, reason}` |
| GET | `/v1/review/reviewers` | R | Availability, cap and open cases per reviewer |
| PATCH | `/v1/review/reviewers/me` | R | Set your own availability |
| PATCH | `/v1/review/reviewers/{name}` | admin | Set a reviewer's availability and cap (`null` clears the cap) |
| GET | `/v1/stats/daily?day=` | R | Daily throughput and decision timing |
| GET | `/v1/stats/sla` | R | Time-in-state per status and current SLA breaches |
| GET | `/health` | - | Liveness with DB check |

Errors:

```json
{"code": "upload_rejected", "message": "Declared content type does not match the file contents.",
 "details": {"declared": "image/jpeg", "detected": "image/png"}}
```

## Project Structure

```
kyc-onboarding-api/
├── kyc/
│   ├── main.py            # app factory, request-id middleware, error handler
│   ├── config.py          # pydantic-settings, API key parsing
│   ├── database.py        # SQLAlchemy engine/session (SQLite or Postgres)
│   ├── migrations.py      # idempotent startup upgrades for existing databases
│   ├── models.py          # Applicant, Document, StateTransition, AssignmentEvent, ReviewerProfile, CheckResult
│   ├── schemas.py         # request/response models
│   ├── auth.py            # X-API-Key principals and require_role
│   ├── workflow.py        # transition table + audited transition()
│   ├── storage.py         # Storage protocol, LocalStorage, S3Storage
│   ├── checks/            # base registry, nid, dob, face, sanctions, duplicates, risk, runner
│   ├── services/          # applicants, documents, review, reviewers, stats
│   ├── routers/           # health, applicants, review, stats
│   └── data/sanctions_sample.csv
├── scripts/demo_flow.py
├── tests/                 # 94 tests: validators, risk, workflow, API flows, review assignment races
├── Dockerfile · docker-compose.yml · Makefile · .github/workflows/ci.yml
└── pyproject.toml · requirements*.txt · .env.example
```

## Testing

```bash
make test        # pytest -q  (94 tests, in-memory SQLite, temp-dir storage)
make lint

# Same suite on a throwaway PostgreSQL (its tables are dropped between tests); CI does this too
TEST_DATABASE_URL=postgresql+psycopg://kyc:kyc@localhost:5432/kyc_test make test
```

Covered: NID format rules for all three lengths, birth-year prefix extraction, age boundaries, magic-byte sniffing and every upload rejection rule, name normalisation and sanctions screening with DOB confirmation, score thresholds and factor summation, the full transition table including the `needs_more_info` loop, and end-to-end API flows: role enforcement, uploads and downloads, submission gating, background checks with auto-approval, sanctions and PEP outcomes, under-age and NID-year mismatches, identical selfie detection, reused documents and duplicate NIDs, claim conflicts and admin override, re-submission producing a second check run, and daily / SLA statistics. Review assignment is covered for dispatch order, SLA breaches, caps, availability, submitter exclusion, claim expiry and renewal, the schema upgrade, and threaded races on real connections (file-backed SQLite, or PostgreSQL with `SKIP LOCKED` when `TEST_DATABASE_URL` is set).

## Roadmap

- [ ] Real face-match adapter (vendor SDK) and liveness signal behind `FaceMatcher` (#1)
- [ ] NID verification against the Election Commission porichoy-style API as an additional check (#2)
- [ ] Alembic migrations instead of `create_all` on startup (#3)
- [ ] Webhook or message-queue notification to the wallet core on approval (#4)

## License

MIT - see [LICENSE](LICENSE).
