# EVE Healthcare Diagnostic Booking API

## Overview

A backend for **booking diagnostic tests and paying for them with a simulated payment provider**.
Users sign up, browse diagnostic centres and the tests (with prices) each centre offers, book an
appointment, pay, and the (simulated) payment provider confirms the payment through a webhook.

The design goal is correctness under failure: duplicate webhooks, racing requests, failed
payments, unauthorised access and infrastructure outages all leave the data in a consistent state,
and the important guarantees are enforced by **PostgreSQL constraints and transactions**, not just
Python `if` statements.

**Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL 16 · Redis 7 ·
Celery · Argon2 + JWT · Pytest · Docker Compose · Ruff + Black.

## Features

### Core assignment features

| Requirement | Where |
|---|---|
| Signup / login / JWT auth (`/auth/me`) | `services/auth_service.py`, `core/security.py` |
| Request validation (Pydantic v2) | `schemas/*` |
| Diagnostic centres, tests and per-centre pricing | `services/centre_service.py` |
| Authenticated bookings with states `PENDING → CONFIRMED / FAILED / CANCELLED` | `db/models/booking.py`, `services/booking_service.py` |
| Mock payment endpoint with SUCCESS / FAILED result | `services/payment_service.py` |
| Payment webhook with **idempotent** processing | `services/webhook_service.py`, `repositories/webhook_repository.py` |
| Edge cases: failed payments, duplicate webhooks, invalid requests, unauthorised access | see [Edge Cases](#edge-cases) |

### Bonus engineering features

| Feature | Why it is here |
|---|---|
| **Docker + Compose** (api, worker, beat, postgres, redis, health checks) | `docker compose up --build` runs everything |
| **OpenAPI / Swagger** (`/docs`, `/redoc`) with examples, auth and error responses | self-documenting API |
| **Unit + integration tests** (200+ tests, ~97 % coverage, real PostgreSQL) | concurrency/idempotency can only be proven on a real DB |
| **Structured JSON logging** with request ids | every log line is machine-parsable and correlatable |
| **Pagination** (limit/offset, max 100, deterministic ordering) | all list endpoints |
| **Redis caching** of centre/test reads with graceful fallback | read-heavy public catalogue |
| **Rate limiting** (Redis, per IP) on signup, login, payment, webhook | brute-force / abuse protection |
| **Celery** retries of webhooks with exponential backoff + periodic stale-payment cleanup | only work that truly benefits from being async |
| **Webhook signature (HMAC)**, optional | the webhook endpoint is otherwise unauthenticated |

## Architecture

```mermaid
flowchart TD
    Client([Client / Swagger UI]) -->|HTTPS + JWT| API

    subgraph API["FastAPI app  (uvicorn)"]
        MW["Middleware: request id · JSON access log · CORS"]
        R["Routers (thin): auth · centres · tests · bookings · payments"]
        S["Services (business logic): Auth · Centre · Booking · Payment · Webhook"]
        Repo["Repositories (SQLAlchemy queries)"]
        MW --> R --> S --> Repo
    end

    Repo --> PG[("PostgreSQL<br/>constraints · row locks · transactions")]
    S -. "cache read/write<br/>(optional)" .-> Redis[("Redis<br/>cache · rate limits · Celery broker")]
    R -. "rate limit<br/>(fail-open)" .-> Redis
    S -- "transient webhook failure<br/>enqueue retry" --> Redis
    Redis --> Worker["Celery worker"]
    Beat["Celery beat<br/>(every 5 min)"] --> Redis
    Worker -->|"WebhookService.process()<br/>(same idempotent code path)"| PG
    Worker -->|"expire stale PENDING payments"| PG
    Provider(["Payment provider (simulated)"]) -->|"POST /payments/webhook/"| API
```

**Request flow:** router (validates input, checks auth, no logic) → service (business rules,
transactions, state machine) → repository (queries only) → PostgreSQL.

### Payment & webhook flow

```mermaid
sequenceDiagram
    participant U as User
    participant A as API
    participant DB as PostgreSQL
    participant P as Provider (simulated)
    participant W as Celery worker

    U->>A: POST /bookings
    A->>DB: price read from centre_tests, INSERT booking (PENDING)
    U->>A: POST /payments/ {booking_id}
    A->>DB: lock booking, INSERT payment, simulate → SUCCESS/FAILED/PENDING
    Note over A,DB: SUCCESS → booking CONFIRMED<br/>FAILED → booking FAILED<br/>PENDING → wait for webhook
    P->>A: POST /payments/webhook/ {event_id, payment_id, ...}
    A->>DB: BEGIN
    A->>DB: INSERT webhook_events ... ON CONFLICT (event_id) DO NOTHING
    alt event_id already stored
        A-->>P: 200 {"result": "duplicate"}   (nothing else touched)
    else first delivery
        A->>DB: lock booking + payment, apply transitions, mark event PROCESSED
        A->>DB: COMMIT
        A-->>P: 200 {"result": "processed"}
    else transient failure (deadlock, DB blip)
        A->>DB: ROLLBACK (claim disappears too)
        A->>W: enqueue retry (Redis)
        A-->>P: 202 {"result": "queued"}
        W->>DB: retries after 1s, 5s, 15s, 30s, 60s using the same idempotent code
    end
```

## Project Structure

```
app/
├── main.py                  # app factory: middleware, error handlers, routers, health checks
├── core/                    # cross-cutting: config, security (Argon2/JWT/HMAC), logging,
│   │                        #   exceptions + handlers, redis client, cache
├── db/
│   ├── database.py          # engine, session factory, get_db dependency
│   └── models/              # user, diagnostic_centre, diagnostic_test (+centre_tests),
│                            #   booking (state machine), payment, webhook_event
├── schemas/                 # Pydantic request/response models (never expose ORM models)
├── api/
│   ├── deps.py              # current user, cache, webhook service, signature check
│   └── v1/                  # thin routers: auth, centres, tests, bookings, payments
├── services/                # business logic: auth, centre, booking, payment, webhook
├── repositories/            # persistence: user, centre, booking, payment, webhook
├── tasks/                   # Celery app + tasks (webhook retry, stale-payment cleanup)
├── middleware/              # request_id (+access log), rate_limit
├── utils/pagination.py
└── scripts/seed.py          # idempotent demo data
alembic/                     # migrations (versions/0001 = initial schema)
tests/
├── unit/                    # state machine, security, services (real DB), tasks, config
└── integration/             # full HTTP flows: auth, centres+cache, bookings, payments,
                             #   webhooks (incl. concurrency), rate limit, logging, errors
docker/entrypoint.sh · Dockerfile · docker-compose.yml · .env.example · pyproject.toml
```

## Database Schema

```mermaid
erDiagram
    users ||--o{ bookings : places
    diagnostic_centres ||--o{ centre_tests : offers
    diagnostic_tests ||--o{ centre_tests : "offered as"
    centre_tests ||--o{ bookings : "booked as"
    bookings ||--o{ payments : "paid by"

    users {
        uuid id PK
        string name
        string email UK
        string password_hash
        bool is_active
        timestamptz created_at
        timestamptz updated_at
    }
    diagnostic_centres {
        uuid id PK
        string name
        string location
        bool is_active
    }
    diagnostic_tests {
        uuid id PK
        string name UK
        text description
    }
    centre_tests {
        uuid id PK
        uuid centre_id FK
        uuid test_id FK
        numeric price "CHECK price >= 0"
        bool is_available
    }
    bookings {
        uuid id PK
        uuid user_id FK
        uuid centre_test_id FK
        timestamptz appointment_at
        numeric amount "CHECK amount >= 0 (price snapshot)"
        string status "CHECK IN (PENDING, CONFIRMED, FAILED, CANCELLED)"
    }
    payments {
        uuid id PK
        uuid booking_id FK
        string provider_payment_id UK
        numeric amount
        string status "CHECK IN (PENDING, SUCCESS, FAILED)"
        string failure_reason
    }
    webhook_events {
        uuid id PK
        string event_id UK "the idempotency key"
        string event_type
        jsonb payload
        string status "PROCESSING, PROCESSED, IGNORED, FAILED"
        int retry_count
        text error_message
        timestamptz processed_at
        timestamptz created_at
    }
```

(`created_at` / `updated_at` exist on every table except `webhook_events`, which is append-only.)

**Constraints that carry business rules (all in the migration):**

| Constraint | Guarantees |
|---|---|
| `UNIQUE (centre_id, test_id)` on `centre_tests` | a centre lists a test once |
| `CHECK price >= 0`, `CHECK amount >= 0` | no negative money |
| `UNIQUE event_id` on `webhook_events` | **webhook idempotency** – one event is processed once, even concurrently |
| `UNIQUE provider_payment_id` on `payments` | provider ids can't collide |
| partial `UNIQUE (booking_id) WHERE status IN ('PENDING','SUCCESS')` on `payments` | at most one live payment per booking, even if two requests race |
| partial `UNIQUE (user_id, centre_test_id, appointment_at) WHERE status IN ('PENDING','CONFIRMED')` on `bookings` | a double-click can't create two live bookings for the same slot |
| `CHECK status IN (...)` on every status column | invalid states can't be stored |
| FKs `ON DELETE RESTRICT` | history can't be orphaned |

## Booking State Machine

```mermaid
stateDiagram-v2
    [*] --> PENDING: POST /bookings
    PENDING --> CONFIRMED: payment SUCCESS
    PENDING --> FAILED: payment FAILED / timed out
    PENDING --> CANCELLED: user cancels
    CONFIRMED --> CANCELLED: user cancels
    FAILED --> [*]
    CANCELLED --> [*]
```

```
PENDING
  | \  \
  |  \  \-----------> CANCELLED
  v   v
CONFIRMED  FAILED
  |
  v
CANCELLED
```

| From | Allowed to |
|---|---|
| `PENDING` | `CONFIRMED`, `FAILED`, `CANCELLED` |
| `CONFIRMED` | `CANCELLED` |
| `FAILED` | – (terminal) |
| `CANCELLED` | – (terminal) |

The table lives in `app/db/models/booking.py` (`ALLOWED_BOOKING_TRANSITIONS`). `Booking.transition_to()`
is the **only** code that writes `booking.status`; anything else raises
`409 INVALID_BOOKING_TRANSITION`. No endpoint accepts a status from a client (`PUT/PATCH` don't
exist), and the payment/webhook/cancel services call `transition_to()`.
Payments have their own tiny machine: `PENDING → SUCCESS | FAILED`, both terminal.

## Authentication

* `POST /auth/signup` – name, email (validated, stored lowercase), password (8–128 chars, at least
  one letter and one digit). Hashed with **Argon2id**; the hash is never returned or logged.
* `POST /auth/login` – JSON `{email, password}` → `{access_token, token_type, expires_in}`.
  Unknown email and wrong password produce the *same* 401 (and the same amount of hashing work),
  so accounts can't be enumerated.
* `POST /auth/token` – the same login as an OAuth2 password **form**, so the **Authorize** button in
  Swagger works (`username` = your email).
* JWT (HS256) with `sub`, `iat`, `exp`; secret and lifetime come from the environment. Send it as
  `Authorization: Bearer <token>`.
* `401` = missing/invalid/expired token (with `WWW-Authenticate: Bearer`); `403` = authenticated but
  not allowed (disabled account, someone else's booking).

## API Endpoints

Base path `/api/v1`. All errors use `{"error": {"code": "...", "message": "...", "request_id": "..."}}`.

| Method | Endpoint | Authentication | Description |
|---|---|---|---|
| POST | `/auth/signup` | Public (rate-limited) | Create an account → `201` |
| POST | `/auth/login` | Public (rate-limited) | Email + password (JSON) → JWT |
| POST | `/auth/token` | Public (rate-limited) | OAuth2 form login (Swagger *Authorize*) |
| GET | `/auth/me` | Bearer | Current user |
| GET | `/centres` | Public | List centres. Query: `search`, `include_inactive`, `limit`, `offset` (cached) |
| GET | `/centres/{centre_id}` | Public | Centre details (cached) |
| GET | `/centres/{centre_id}/tests` | Public | Tests + prices at a centre. Query: `search`, `only_available`, `limit`, `offset` (cached) |
| GET | `/tests` | Public | Test catalogue. Query: `search`, `limit`, `offset` |
| GET | `/tests/{test_id}` | Public | A test and the active centres offering it (cheapest first) |
| POST | `/bookings` | Bearer | Create a `PENDING` booking (price computed by the server) → `201` |
| GET | `/bookings` | Bearer | My bookings, newest first. Query: `status`, `limit`, `offset` |
| GET | `/bookings/{booking_id}` | Bearer (owner) | One of my bookings |
| POST | `/bookings/{booking_id}/cancel` | Bearer (owner) | Cancel (`PENDING`/`CONFIRMED` only) |
| POST | `/payments/` | Bearer (owner) (rate-limited) | Simulated payment for one of my `PENDING` bookings → `201` |
| POST | `/payments/webhook/` | Provider (optional HMAC signature; rate-limited) | Idempotent payment event → `200` (`processed`/`duplicate`/`ignored`) or `202` (`queued`) |
| GET | `/health` · `/health/ready` | Public | Liveness · readiness (PostgreSQL required, Redis optional) |

Interactive docs: **http://localhost:8000/docs** · ReDoc: **/redoc** · Raw spec: `/openapi.json`.

<details>
<summary>Error codes</summary>

| HTTP | Code | When |
|---|---|---|
| 400 | `INVALID_APPOINTMENT` | appointment in the past / too soon / too far ahead |
| 400 | `WEBHOOK_PAYMENT_BOOKING_MISMATCH` | webhook `payment_id` belongs to a different booking |
| 400 | `SIMULATION_OVERRIDE_DISABLED` | `simulate_outcome` sent while disabled |
| 401 | `NOT_AUTHENTICATED` · `INVALID_TOKEN` · `INVALID_CREDENTIALS` · `INVALID_WEBHOOK_SIGNATURE` | auth failures |
| 403 | `BOOKING_ACCESS_DENIED` · `USER_INACTIVE` | not the owner / disabled account |
| 404 | `CENTRE_NOT_FOUND` · `TEST_NOT_FOUND` · `CENTRE_TEST_NOT_FOUND` · `BOOKING_NOT_FOUND` · `PAYMENT_NOT_FOUND` | missing resources |
| 409 | `EMAIL_ALREADY_REGISTERED` · `DUPLICATE_BOOKING` | uniqueness conflicts |
| 409 | `CENTRE_UNAVAILABLE` · `TEST_UNAVAILABLE` | centre closed / test switched off |
| 409 | `INVALID_BOOKING_TRANSITION` · `BOOKING_NOT_PAYABLE` · `PAYMENT_ALREADY_EXISTS` · `PAYMENT_IN_PROGRESS` | state conflicts |
| 422 | `VALIDATION_ERROR` | bad JSON, bad UUID, bad email/password, unknown enum value… (with per-field `details`) |
| 429 | `RATE_LIMIT_EXCEEDED` | includes a `Retry-After` header |
| 500 / 503 | `INTERNAL_ERROR` · `DATABASE_ERROR` · `SERVICE_UNAVAILABLE` | never contains stack traces |

</details>

## Example API Requests

Start the stack first (see [Running Locally](#running-locally)); the seed data is loaded automatically.

```bash
BASE=http://localhost:8000/api/v1

# 1. Signup
curl -s -X POST $BASE/auth/signup -H 'Content-Type: application/json' \
  -d '{"name":"Asha Verma","email":"asha@example.com","password":"Str0ngPassw0rd"}'

# 2. Login (keep the token)
TOKEN=$(curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"asha@example.com","password":"Str0ngPassw0rd"}' | jq -r .access_token)

# 3. List centres (search + pagination)
curl -s "$BASE/centres?search=city&limit=10&offset=0"
CENTRE_ID=$(curl -s "$BASE/centres?search=city" | jq -r '.items[0].id')

# 4. List tests (with prices) offered by that centre
curl -s "$BASE/centres/$CENTRE_ID/tests"
TEST_ID=$(curl -s "$BASE/centres/$CENTRE_ID/tests" | jq -r '.items[] | select(.name=="CBC") | .test_id')

# 5. Create a booking (any "amount" you send is ignored; the server uses the DB price)
WHEN=$(date -u -d "+3 days" +%Y-%m-%dT%H:%M:%SZ)      # macOS: date -u -v+3d +%Y-%m-%dT%H:%M:%SZ
BOOKING=$(curl -s -X POST $BASE/bookings -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"centre_id\":\"$CENTRE_ID\",\"test_id\":\"$TEST_ID\",\"appointment_at\":\"$WHEN\"}")
echo $BOOKING | jq .
BOOKING_ID=$(echo $BOOKING | jq -r .id)

# 6. Pay: immediate simulated result (simulate_outcome: success | failure)
curl -s -X POST $BASE/payments/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"booking_id\":\"$BOOKING_ID\",\"simulate_outcome\":\"success\"}" | jq .
#     -> {"status":"SUCCESS","booking_status":"CONFIRMED", ...}

# 7. Webhook flow: a second booking whose payment stays PENDING until the provider calls back
WHEN2=$(date -u -d "+4 days" +%Y-%m-%dT%H:%M:%SZ)
BOOKING2_ID=$(curl -s -X POST $BASE/bookings -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"centre_id\":\"$CENTRE_ID\",\"test_id\":\"$TEST_ID\",\"appointment_at\":\"$WHEN2\"}" | jq -r .id)
PAYMENT_ID=$(curl -s -X POST $BASE/payments/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"booking_id\":\"$BOOKING2_ID\",\"simulate_outcome\":\"pending\"}" | jq -r .provider_payment_id)

# Send the same event three times - only the first changes anything (event ids are global,
# so use a fresh one per scenario)
EVENT_ID=evt_$(date +%s)
for i in 1 2 3; do
  curl -s -X POST $BASE/payments/webhook/ -H 'Content-Type: application/json' \
    -d "{\"event_id\":\"$EVENT_ID\",\"event_type\":\"payment.success\",\"payment_id\":\"$PAYMENT_ID\",\"booking_id\":\"$BOOKING2_ID\",\"status\":\"SUCCESS\"}"
  echo
done
# {"event_id":"evt_1759168000","result":"processed"}
# {"event_id":"evt_1759168000","result":"duplicate"}
# {"event_id":"evt_1759168000","result":"duplicate"}

curl -s $BASE/bookings/$BOOKING2_ID -H "Authorization: Bearer $TOKEN" | jq '{status, amount}'
#     -> {"status":"CONFIRMED","amount":"350.00"}
```

To send a **failed** payment event use `"event_type":"payment.failed"` with `"status":"FAILED"`.

**Signed webhooks** (when `WEBHOOK_SIGNATURE_REQUIRED=true`):

```bash
BODY='{"event_id":"evt_signed_'$(date +%s)'","event_type":"payment.success","payment_id":"'$PAYMENT_ID'","booking_id":"'$BOOKING2_ID'","status":"SUCCESS"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | sed 's/^.* //')
curl -s -X POST $BASE/payments/webhook/ -H 'Content-Type: application/json' \
  -H "X-Webhook-Signature: sha256=$SIG" -d "$BODY"
```

## Running Locally

**Requirements:** Docker with Compose v2. Nothing else.

```bash
docker compose up --build
```

That starts PostgreSQL, Redis, the API (which first runs `alembic upgrade head` and loads the seed
data), a Celery worker and Celery beat. Wait until `docker compose ps` shows the services healthy,
then open **http://localhost:8000/docs**.

```bash
curl localhost:8000/health          # {"status":"ok"}
curl localhost:8000/health/ready    # {"status":"ok","checks":{"postgres":"ok","redis":"ok"}}
docker compose logs -f api worker   # JSON logs
docker compose down                 # stop   (add -v to also delete the database volume)
```

Every setting has a working development default; to change any, `cp .env.example .env` and edit.
Seed data (idempotent, fictional): centres *CityCare Diagnostics*, *Apollo Diagnostics*,
*HealthPlus Labs*; tests *CBC, Lipid Profile, Thyroid Profile, HbA1c, Liver Function Test* with
per-centre prices. (Apollo's Liver Function Test is intentionally *unavailable*, and HealthPlus doesn't
offer Thyroid Profile, so those error paths can be tried by hand.)

Running the API without Docker (needs local PostgreSQL + Redis):

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql+psycopg://eve:eve@localhost:5432/eve REDIS_URL=redis://localhost:6379/0
alembic upgrade head && python -m app.scripts.seed
uvicorn app.main:app --reload
celery -A app.tasks.celery_app worker -l info      # in another shell
celery -A app.tasks.celery_app beat -l info        # optional: periodic cleanup
```

## Environment Variables

All are optional in development. See `.env.example` for a ready-to-copy template.

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` refuses to start with a weak JWT secret or with `PAYMENT_ALLOW_SIMULATION_OVERRIDE=true` |
| `DATABASE_URL` | `postgresql+psycopg://eve:eve@localhost:5432/eve` | SQLAlchemy URL (compose builds it from `POSTGRES_*`) |
| `REDIS_URL` | `redis://localhost:6379/0` | cache, rate limiting, Celery broker/result backend |
| `JWT_SECRET_KEY` | placeholder | **set a strong random value (≥ 32 chars) outside development** |
| `JWT_ALGORITHM` / `ACCESS_TOKEN_EXPIRE_MINUTES` | `HS256` / `60` | token settings |
| `CORS_ORIGINS` | `http://localhost:3000` | comma-separated allowed origins |
| `LOG_LEVEL` | `INFO` | JSON log level |
| `BOOKING_MIN_LEAD_MINUTES` / `BOOKING_MAX_DAYS_AHEAD` | `60` / `90` | valid appointment window |
| `PAYMENT_SIMULATION_MODE` | `success` | default simulated outcome: `success`, `failure`, `pending` |
| `PAYMENT_ALLOW_SIMULATION_OVERRIDE` | `true` | lets clients pass `simulate_outcome` (demo/test hook; **must be false in production**) |
| `PAYMENT_STALE_AFTER_MINUTES` | `15` | PENDING payments older than this are failed by the cleanup task |
| `WEBHOOK_SECRET` / `WEBHOOK_SIGNATURE_REQUIRED` | `dev-webhook-secret` / `false` | HMAC-SHA256 verification of the raw webhook body |
| `WEBHOOK_MAX_RETRIES` / `WEBHOOK_RETRY_BACKOFF_SECONDS` | `5` / `1,5,15,30,60` | retry bound and delays (list length must equal the bound) |
| `CACHE_TTL_SECONDS` | `60` | Redis cache lifetime |
| `RATE_LIMIT_ENABLED` | `true` | master switch |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | fixed window length |
| `RATE_LIMIT_AUTH_PER_WINDOW` / `_PAYMENT_` / `_WEBHOOK_` | `10` / `20` / `120` | requests per window per client IP |
| `POSTGRES_USER/PASSWORD/DB`, `API_PORT`, `SEED_ON_STARTUP` | `eve`/`eve`/`eve`, `8000`, `true` | compose-level settings |

## Running Tests

The tests use a **real PostgreSQL** (the schema is built by running the Alembic migrations, and
the concurrency tests need real row locks); Redis is replaced by `fakeredis`.

```bash
# 1. a throwaway database (skip if you already run PostgreSQL locally)
docker run -d --name eve-test-db -e POSTGRES_USER=eve -e POSTGRES_PASSWORD=eve \
  -p 5432:5432 postgres:16-alpine

# 2. install and run
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # 200+ tests, ~15 s
pytest --cov=app --cov-report=term-missing
pytest -m concurrency                    # only the real-concurrency tests

# 3. code quality
ruff check . && black --check .
```

`TEST_DATABASE_URL` (default `postgresql+psycopg://eve:eve@localhost:5432/eve_test`) selects the
database; it is created automatically and **wiped at the start of every run**, so never point it at
data you care about.

What is covered: signup/login/JWT rules · centres, pagination, search · booking creation and
server-side pricing · all 16 state-machine transitions · cancellation · ownership checks ·
success/failed/duplicate/concurrent payments · webhooks (success, failed, duplicate ×N,
10 concurrent deliveries, invalid ids, mismatches, conflicting late events, signatures) · retry
scheduling, backoff and give-up · stale-payment cleanup · cache hit/miss/invalidation/Redis outage ·
rate limiting (allowed, exceeded, fail-open) · logging (JSON, request ids, no secrets) · error
envelope and no stack-trace leaks.

## Database Migrations

Schema changes are only made through Alembic; the app never calls `create_all()`.

```bash
alembic upgrade head                          # apply all migrations (Docker does this on start)
alembic current                               # show the current revision
alembic downgrade -1                          # roll back one revision
alembic revision --autogenerate -m "message"  # new migration from model changes (then review it!)
alembic check                                 # fails if models and migrations have drifted
```

Inside Docker: `docker compose exec api alembic upgrade head`.
Set `RUN_MIGRATIONS=false` to skip the automatic run at container start.

## Design Decisions

**Why PostgreSQL?** The core guarantees are relational: unique constraints (idempotency, one live
payment per booking), partial indexes, `CHECK` constraints, `SELECT … FOR UPDATE` row locks and
transactional DDL migrations. `NUMERIC` is exact for money and `JSONB` stores raw webhook payloads.

**Why Redis?** Three jobs, all of them *optional accelerators*, never a source of truth: the read cache,
the rate-limit counters, and the Celery broker. If Redis dies the API keeps working (see Edge Cases).

**Why Celery (and only for two things)?** (1) Webhook retries with exponential backoff must survive
a request ending and a process restarting, so they need a durable queue and a worker. (2) A periodic
job that fails payments that never received a webhook. Everything else (bookings, the simulated
payment itself) stays synchronous: moving a simple DB write into a queue would only add latency and
failure modes.

**Why UUIDs?** IDs appear in URLs. Random UUIDs can't be enumerated or guessed
(`/bookings/1`, `/bookings/2`…), leak no volume information, and can be generated without a DB round-trip.

**Why services + repositories?** Routers only translate HTTP ⇄ Python (validate, authenticate,
call a service, shape the response). Services own business rules and transaction boundaries
(`commit()` is called explicitly at the end of a use case). Repositories own SQL. So a rule change
(e.g. the payable check) is one function in one service, and the service tests need no HTTP.
There is no generic repository/unit-of-work framework: just plain classes, one per aggregate.

**Webhook idempotency (the important one).** Inside one transaction the service runs
`INSERT INTO webhook_events (event_id, …) ON CONFLICT (event_id) DO NOTHING RETURNING id`.
The `UNIQUE` index on `event_id` decides, not Python: if a row comes back this delivery *owns* the
event; if not, it is a duplicate and the transaction is rolled back untouched. When two deliveries
race, PostgreSQL makes the second `INSERT` **wait** for the first transaction: if that commits, the
second gets "conflict → duplicate"; if it rolls back (e.g. crash), the second inserts and processes.
Because the claim and the state change commit *atomically*, there is no window in which an event is
recorded but not applied (or applied twice). A separate guard covers *different* event ids for the
same payment: the payment state machine allows a settled payment to move no further, so a second
event is a no-op (`processed`, same state) or `ignored` (conflicting late event; logged as a warning).

**Transaction & locking strategy.** Each use case is one transaction. Mutating flows lock rows in a
single fixed order, **booking first, then payment**, so concurrent requests serialise instead of
deadlocking. `populate_existing` on locking queries ensures we read the row's latest committed
values after waiting for a lock. Expected constraint violations (duplicate email, duplicate live
booking, second live payment) are translated to clean 409s.

**Never trusting the client.** `amount` is copied from `centre_tests.price` at booking time
(a *snapshot*: later price changes don't alter an existing booking) and payments copy the
booking's amount. The request schemas simply have no `amount` or `status` fields; extra fields are
ignored. Catalogue *reads* are cached but booking always reads the price from PostgreSQL, so a stale
cache can never cause a wrong charge.

**State machine.** An explicit `{state: allowed_next_states}` table plus one `transition_to()`
method; 16 parametrised tests cover every (from, to) pair. Chosen over a library/pattern because it
is ~20 lines, obvious in a review, and impossible to bypass accidentally.

**Cache invalidation.** Cache keys embed a *namespace version* (`eve:cache:catalogue:v7:…`).
Changing a price/availability (`CentreService.update_centre_test`, also used by the seed script)
`INCR`s the version, instantly orphaning every cached page for all query variants (no `SCAN`/`DEL`);
orphans expire via TTL (60 s).

**Rate limiting.** A fixed-window counter in Redis (`SET NX EX` + `INCR`) per client IP and scope.
It **fails open** on Redis errors (availability over strictness for a lightweight limiter). Behind a
proxy, uvicorn runs with `--proxy-headers`.

**Logging.** One JSON object per line. A per-request context (request id, then user id, booking
id, payment id, webhook event id as they become known) is attached to *every* log line automatically
and the middleware emits an access line with method, path, status and duration. Passwords, tokens
and hashes are never logged (validation errors drop the offending input; there is a test).

## Edge Cases

| Situation | Behaviour |
|---|---|
| **Duplicate webhook** (1×, 2×, 10×, or 10 at once) | first → `200 processed`; repeats → `200 duplicate`; state changes exactly once (unit + HTTP concurrency tests). |
| **Webhook for unknown payment / wrong booking / invalid event id or type** | `404 PAYMENT_NOT_FOUND` / `400 …MISMATCH` / `422`. Permanent errors are **not** retried and leave no trace (the claim is rolled back), so a corrected redelivery works. |
| **Conflicting late webhook** (e.g. `failed` after `success`) | recorded as `IGNORED`, `200`, warning logged; payment and booking untouched. |
| **Transient failure while processing a webhook** (deadlock, DB restart) | transaction rolled back; API answers `202 queued`; Celery retries after 1 s, 5 s, 15 s, 30 s, 60 s (bounded to 5); each retry is logged; after the last one the event is stored as `FAILED` for inspection. Retries reuse the idempotent path, so no duplicate transitions. If the broker is also down → `503` so the provider redelivers. *Demonstrated live: stopped PostgreSQL, sent a webhook (202), restarted it, and the worker completed it on retry.* |
| **Invalid booking** | unknown id → `404`; someone else's → `403`; malformed UUID → `422`; unknown centre/test/test not at that centre → `404`; closed centre / unavailable test → `409`; past / too-soon / too-far appointment → `400`; naive datetime → `422`; same slot twice → `409 DUPLICATE_BOOKING`. |
| **Failed payment** | `201` with `status: FAILED` in the body (the request was processed; the *payment* failed); booking → `FAILED` (terminal); the user books again. |
| **Unauthorised access** | no/expired/garbled token → `401`; another user's booking (read, cancel, pay) → `403`; disabled account → `403`; there is no endpoint that lets a client set a status or amount. |
| **Duplicate payment** | second attempt on a paid/failed/cancelled booking → `409 BOOKING_NOT_PAYABLE`; while one is pending → `409 PAYMENT_ALREADY_EXISTS`. Racing requests are serialised by the booking row lock and backstopped by the partial unique index (6 concurrent requests → exactly 1 payment, tested). |
| **Invalid state transition** | `409 INVALID_BOOKING_TRANSITION`, e.g. cancelling a cancelled or failed booking. Cancelling while a payment is in flight → `409 PAYMENT_IN_PROGRESS` (the payment must settle or expire first). |
| **Payment never confirmed** | Celery beat runs every 5 min: PENDING payments older than `PAYMENT_STALE_AFTER_MINUTES` become `FAILED` and the booking `FAILED`. |
| **Redis outage** | catalogue endpoints fall back to PostgreSQL; rate limiting fails open; `/health/ready` reports `degraded`; only new webhook *retries* need the broker (→ 503 if it's down). Verified live by stopping the Redis container. Redis calls use 0.5 s timeouts so an outage can't hang requests. |
| **Unexpected exception** | generic `500 INTERNAL_ERROR` JSON with the request id; the stack trace goes only to the server log. |

## Assumptions

* Amounts are a single currency with 2 decimal places; money is serialised as a **string** (`"350.00"`)
  to avoid float rounding on the client.
* `appointment_at` must be timezone-aware and between `BOOKING_MIN_LEAD_MINUTES` (60) and
  `BOOKING_MAX_DAYS_AHEAD` (90) days from now. There is no per-slot capacity or working-hours model;
  the only slot rule is that one user can't hold two live bookings for the same test/centre/time.
* A payment is created at payment time, not at booking time, so an abandoned booking leaves no
  orphan payment rows. One booking → at most one live payment. A **failed payment ends the booking**
  (`FAILED` is terminal per the required state machine); the user makes a new booking to retry.
* Cancelling a `CONFIRMED` booking does not trigger a refund (no refund flow is in scope).
* "Not your booking" is `403` (as the assignment lists 403 for unauthorised actions); IDs are random
  UUIDs so this reveals nothing useful. A nonexistent id is `404`.
* The simulator is deterministic. By default every payment succeeds (`PAYMENT_SIMULATION_MODE`);
  callers may force `success` / `failure` / `pending` via `simulate_outcome` while
  `PAYMENT_ALLOW_SIMULATION_OVERRIDE` is on. `pending` models an asynchronous provider that
  confirms later through the webhook.
* The webhook references an *existing* payment (created by `POST /payments/`); it never creates payments or bookings.
* Signature verification is off by default so the endpoint is easy to try from Swagger/curl; turn it on
  with `WEBHOOK_SIGNATURE_REQUIRED=true`.
* Catalogue data (centres, tests, prices) is managed outside the public API (seed script /
  `CentreService.update_centre_test`); there is no admin API or roles.
* Rate limits are per client IP; a proxy in front must forward the client IP.

## Future Improvements

* Integrate a **real payment gateway** (Stripe/Razorpay): hosted checkout, signed webhooks with
  timestamp/replay protection, refunds for cancelled confirmed bookings.
* **Notification service** (email/SMS) for confirmations and reminders, driven by an outbox table
  written in the same transaction as the state change.
* **Appointment slot management**: working hours, per-centre capacity, holds with expiry.
* **Audit trail**: an append-only `booking_events` table recording who/what/when for each transition.
* Admin API and roles for managing centres, tests and prices (using the existing cache-invalidating service method).
* Refresh tokens / token revocation, email verification, account lockout.
* **Observability**: Prometheus metrics, OpenTelemetry tracing across API → Celery, alerting on
  `FAILED` webhook events and retry exhaustion; a dead-letter/replay tool for them.
* Read replicas and keyset pagination for very large tables; multi-instance rate limiting with a sliding window.
