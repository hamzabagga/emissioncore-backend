# EmissionCore Backend

Multi-tenant emissions platform backend built with **FastAPI**, **PostgreSQL** and **SQLAlchemy 2.0**.
Organizations manage their companies, versioned emission factors and activity records; emissions
(kg CO2e) are always calculated by the server, never entered directly.

---

## 1. Quick start

Requirements: Docker + Docker Compose.

```bash
docker compose up --build
```

This starts three services:

| Service  | Role |
|----------|------|
| `db`     | PostgreSQL 16 |
| `api`    | FastAPI on http://localhost:8000 — runs `alembic upgrade head` automatically on start |
| `worker` | Background job worker (CSV imports, recalculations) |

API documentation (Swagger): **http://localhost:8000/docs**

### Demo data

```bash
docker compose exec api python -m app.seed
```

Creates two organizations so tenant isolation can be tried by hand (password for all: `password123`):

| Organization | Users |
|--------------|-------|
| GreenAudit   | `admin@greenaudit.io`, `analyst@greenaudit.io`, `viewer@greenaudit.io` |
| EcoConseil   | `admin@ecoconseil.io` |

Then `POST /auth/login`, copy the `access_token` and click **Authorize** in Swagger.

### Try the CSV import

`sample.csv` (repository root) contains 11 rows: 8 valid ones across electricity, fuel and travel,
and 3 invalid ones on purpose (unknown source, non-numeric amount, a date with no valid factor).

1. Log in as `admin@greenaudit.io`, create a company with `POST /companies`.
2. Create the factors with `POST /emission-factors`:

   ```json
   {"source": "electricity", "unit": "kWh",   "kg_co2e_per_unit": "0.40", "valid_from": "2024-01-01", "valid_to": "2025-01-01"}
   {"source": "electricity", "unit": "kWh",   "kg_co2e_per_unit": "0.35", "valid_from": "2025-01-01", "valid_to": null}
   {"source": "fuel",        "unit": "litre", "kg_co2e_per_unit": "2.68", "valid_from": "2024-01-01", "valid_to": null}
   {"source": "travel",      "unit": "km",    "kg_co2e_per_unit": "0.17", "valid_from": "2024-01-01", "valid_to": null}
   ```

3. `POST /companies/{id}/records/import` with `sample.csv` → `202` and a pending job.
4. `GET /imports/{job_id}` → `completed`, 8 rows imported, 3 errors reported with their line
   numbers (10, 11, 12).
5. `GET /companies/{id}/summary?group_by=quarter` → total **25 856.6 kg CO2e**
   (e.g. 2024-Q4 = 13 200 kWh × 0.40 = 5 280, while 2025-Q1 uses the 0.35 version).
6. Upload the same file again → `200` with the **same job**, no duplicate records.

### Running the tests

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d db
python -m pytest -q
```

**115 tests.** They run against a real PostgreSQL database (`emission_test`, created automatically)
on which the **real Alembic migrations** are applied, so database constraints (exclusion constraint,
composite foreign keys, unique constraints) are tested too — not a mocked or `create_all` schema.
Tables are truncated before each test, so tests are independent.

---

## 2. Architecture

```
app/
├── api/
│   ├── deps.py          # current user (JWT) + role checks (ReadUser / WriteUser / AdminUser)
│   ├── scoping.py       # get_<entity>_or_404: every lookup is filtered by organization_id
│   ├── pagination.py    # cursor (keyset) pagination
│   └── routes/          # thin HTTP layer: auth, users, companies, factors, records,
│                        #   summary, imports, jobs, audit
├── services/            # business logic: emission calculation, factor overlap, summary,
│                        #   CSV import, recalculation, audit
├── models/              # SQLAlchemy models
├── schemas/             # Pydantic request / response models
├── core/                # config, security (Argon2 + JWT), uniform error format
├── worker.py            # background worker loop
└── seed.py
alembic/versions/        # 3 migrations
tests/                   # pytest, one file per feature
```

Routes only handle HTTP (validation, status codes); the rules live in `services/`.

### Key design decisions and trade-offs

| Decision | Why | Trade-off |
|----------|-----|-----------|
| **Synchronous SQLAlchemy** | Simple to reason about, test and debug; FastAPI runs sync endpoints in a thread pool | Lower raw throughput than async under very high concurrency |
| **Job queue stored in PostgreSQL** (`jobs` table + `SELECT … FOR UPDATE SKIP LOCKED`) instead of Redis/Celery | One less service to run; the job row *is* the status/progress record exposed by the API; several workers can run safely in parallel | Not designed for very high job throughput; polling every second |
| **`Numeric` for amounts and kg CO2e** | `float` gives rounding errors (`0.1 + 0.2 ≠ 0.3`), unacceptable for reporting figures | Slightly slower arithmetic |
| **User and role re-read from the database on every request** (the JWT only holds the user id) | A role change or account deletion takes effect immediately, not after token expiry | One primary-key lookup per request |
| **Short access token (15 min) + refresh token (7 days)**, token `type` checked | A refresh token cannot be used as an access token and vice versa | No server-side token revocation list (see "What's missing") |
| **Uniform error format** `{"error": {"code", "message", "details"}}` for 401/403/404/409/422 | Predictable for API clients | — |

---

## 3. Data model

```
organizations ─┬─ users             (email unique, role: admin | analyst | viewer)
               ├─ companies         (name unique per organization)
               ├─ emission_factors  (source, unit, kg_co2e_per_unit, valid_from, valid_to)
               ├─ emission_records  (source, unit, activity_amount, date, kg_co2e, factor_id)
               ├─ jobs              (CSV imports and recalculations)
               └─ audit_logs
```

### Versioned emission factors

A factor is valid on the half-open period **`[valid_from, valid_to)`**; `valid_to = NULL` means
"still valid".

```
electricity / kWh   v1: 0.40  [2024-01-01, 2025-01-01)
                    v2: 0.35  [2025-01-01, ∞)
```

- A record dated `2024-12-31` uses v1 → 1000 kWh × 0.40 = **400 kg**.
- A record dated `2025-01-01` uses v2 → 1000 kWh × 0.35 = **350 kg**.

`valid_to` is **exclusive** so that consecutive versions share a boundary date without both
being valid on that day: exactly one factor applies to any date, no gaps, no ambiguity.

The factor is matched on **organization + source + unit + date**. If no factor is valid,
the record is rejected with `422 no_valid_factor`. Each record stores `factor_id`, so we always
know which factor produced a number, and which records to recalculate when a factor changes.

**No overlapping periods** for the same (organization, source, unit) is enforced twice:

1. **Application level** — a clear `409` that names the conflicting factor.
2. **Database level** — a PostgreSQL exclusion constraint (needs the `btree_gist` extension):

   ```sql
   EXCLUDE USING gist (organization_id WITH =, source WITH =, unit WITH =,
                       daterange(valid_from, valid_to, '[)') WITH &&)
   ```

   This also holds when two requests arrive at the same time (see section 6).

### Constraints

- `CHECK`: allowed sources, allowed roles, `kg_co2e_per_unit >= 0`, `activity_amount >= 0`,
  `valid_to > valid_from`, allowed job types/statuses, allowed audit entities/actions.
- `UNIQUE`: user email; company name per organization; `(company_id, file_sha256)` on jobs
  (import idempotency); `(import_job_id, import_row)` on records (no duplicate imported rows).
- Composite foreign keys for tenant safety (section 4).
- A factor used by records cannot be deleted (`ON DELETE RESTRICT` → `409`).

### Indexes and why

| Index | Used by |
|-------|---------|
| `emission_records (organization_id, company_id, date)` | Record filters by company + date range, cursor pagination, and the summary aggregation |
| `emission_records (factor_id)` | Finding the records produced by a factor |
| Exclusion constraint's GiST index on `emission_factors` | Overlap check, and finding the factor valid at a date |
| `companies (organization_id, name)` (unique) | Listing an organization's companies; duplicate-name check |
| `users (email)` (unique), `users (organization_id)` | Login; listing an organization's users |
| `jobs (status, id)` | The worker picking the oldest pending job |
| `audit_logs (organization_id, entity, entity_id, id)` | `GET /audit?entity=&entity_id=`, newest first |

---

## 4. Tenant isolation

A user must never read or modify another organization's data, even by guessing IDs. This is
guaranteed at **two levels**:

1. **Application** — every query is filtered by the authenticated user's `organization_id`
   (`app/api/scoping.py`). The organization always comes from the token, never from the request
   body. An ID belonging to another organization returns **`404`, not `403`**, so the API does not
   even reveal that the object exists.
2. **Database** — `organization_id` is stored on every business table, and **composite foreign
   keys** make cross-tenant links impossible even if application code had a bug:

   ```
   emission_records (company_id, organization_id) → companies (id, organization_id)
   emission_records (factor_id,  organization_id) → emission_factors (id, organization_id)
   jobs             (company_id, organization_id) → companies (id, organization_id)
   ```

Tests cover guessed IDs on GET / PATCH / DELETE for companies, factors, records, users, import
jobs and summaries; listings only returning the caller's data; and a recalculation in one
organization not touching another.

### Roles

| Role | Permissions |
|------|-------------|
| `viewer`  | Read only |
| `analyst` | Read + create / update / delete records, CSV import |
| `admin`   | Everything: users, companies, emission factors, recalculation, audit log |

---

## 5. Endpoints overview

| Area | Endpoints |
|------|-----------|
| Auth | `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me` |
| Users (admin) | `GET/POST /users`, `GET/PATCH/DELETE /users/{id}` |
| Companies | `GET/POST /companies`, `GET/PATCH/DELETE /companies/{id}` |
| Emission factors | `GET/POST /emission-factors`, `GET/PATCH/DELETE /emission-factors/{id}` |
| Emission records | `GET/POST /records`, `GET/PATCH/DELETE /records/{id}` |
| Summary | `GET /companies/{id}/summary?from=&to=&group_by=month\|quarter\|source` |
| CSV import | `POST /companies/{id}/records/import`, `GET /imports/{job_id}` |
| Recalculation | `POST /emission-factors/recalculate`, `GET /jobs/{job_id}` |
| Audit (admin) | `GET /audit?entity=&entity_id=` |

Lists use **cursor pagination** (`limit`, `cursor` → `next_cursor`): keyset on
`(sort column, id)`, so pages stay stable while rows are inserted and stay fast on large tables
(no `OFFSET`). Records can be filtered by `company_id`, `source`, `date_from`, `date_to` and sorted
by `date`, `kg_co2e` or `id`.

### Summary

Aggregation runs **in PostgreSQL** in a single `SUM … COUNT … GROUP BY` query
(`to_char(date, 'YYYY-MM')`, `'YYYY-"Q"Q'`, or `source`). `from` and `to` are **inclusive**.
Tests check the exact numbers, an empty range, boundary dates, and a range that crosses a factor
version change.

---

## 6. Async CSV import and recalculation

### CSV import

```
POST /companies/{id}/records/import   (CSV: source,unit,activity_amount,date)
   → checks columns and row count (max 100,000), stores the file, creates a "pending" job
   → returns 202 + job immediately
worker
   → claims the job (FOR UPDATE SKIP LOCKED), validates each row,
     computes kg CO2e, inserts in batches of 5,000, updates progress
GET /imports/{job_id}   → status, progress, per-row errors [{"row": 7, "error": "..."}]
```

- **Invalid rows don't fail the import**: they are reported with their line number and reason
  (unknown source, bad amount, bad date, no valid factor, …).
- **Efficient**: the organization's factors are loaded once in memory instead of one query per row;
  rows are inserted with multi-row `INSERT` in batches.
- **Idempotent, two levels**:
  1. Same file for the same company → same SHA-256 → unique `(company_id, file_sha256)` →
     the **existing job is returned** (`200`), no new job.
  2. Each imported record keeps `(import_job_id, import_row)`, which is unique; inserts use
     `ON CONFLICT DO NOTHING`. If the worker dies mid-import, the job is picked up again after
     10 minutes and re-processed **without duplicates**.

### Recalculation

`POST /emission-factors/recalculate` (admin) creates a background job. The worker runs **one
`UPDATE … FROM` statement** that re-attaches every record of the organization to the factor valid
on its date and recomputes kg CO2e. It only updates rows whose factor or amount actually changes,
so it is **idempotent**: a second run updates 0 rows. Records that no longer have any valid factor
are left unchanged and reported in the job.

---

## 7. Advanced requirements chosen

### #3 Concurrency safety

- **Overlapping factors**: the database exclusion constraint makes it impossible for two concurrent
  transactions to both insert overlapping periods, even though both pass the application check.
  Test: two real transactions in two threads — the second one is blocked, then rejected; exactly one
  factor remains.
- **Duplicate imports**: the unique constraint on `(company_id, file_sha256)` lets only one of two
  simultaneous uploads of the same file create a job; the other receives the existing job.
  Test: two threads start the upload at the same time (barrier) → one job.

Why: it follows directly from the data model — correctness is guaranteed by PostgreSQL constraints
and transactions rather than by application locks.

### #1 Audit log

Every create / update / delete of an **emission record** or **emission factor** writes who
(`user_id`), what (`changes`) and when (`created_at`). Updates store only the changed fields as
`{"field": {"old": …, "new": …}}`. The audit entry is written **in the same transaction** as the
change: a rejected change (e.g. `409`) leaves no trace, and no change can happen without a trace.
Queryable by admins with `GET /audit?entity=&entity_id=` (newest first, paginated, isolated per
organization).

Why: emission figures are reporting data — being able to explain who changed a factor or a record,
and from which value, is essential.

---

## 8. What's missing / what I would improve with more time

- **Advanced #2 – Summary caching**: cache per (company, range, grouping), invalidated on any
  record or factor write of the organization; benchmark with ~500k records.
- **Advanced #4 – Rate limiting & observability**: per-user rate limiting, structured JSON logs,
  and a `/health` that also checks the database and the worker (today `/health` only says the API
  is up).
- **Advanced #5 – Soft delete & restore** for companies and records.
- **Token revocation** (logout / refresh-token rotation stored server-side).
- **Bulk operations in the audit log**: CSV imports and recalculations are traced through the
  `jobs` table (who launched them, counts, errors) rather than one audit row per record.
- **Recalculation scope**: currently the whole organization; it could be limited to the
  source/unit of the changed factor (it is already cheap thanks to "only update what changed").
- **Uploaded files are stored in the database** until processed (then cleared). For much larger
  files, object storage (S3) would be better.
- CI pipeline (GitHub Actions running the tests), linting (ruff) and type checking (mypy).

---

## 9. Time spent

Approximate active time per part (breaks excluded).

| Part | Approx. time |
|------|--------------|
| Reading the brief, planning the architecture | ~30 min |
| Setup: FastAPI, Docker Compose, PostgreSQL | ~1 h |
| Data model + first migration (composite FKs, exclusion constraint, indexes) | ~1 h 15 |
| Authentication (JWT login/refresh), roles, error format | ~1 h 30 |
| Auth tests + test setup | ~45 min |
| CRUD companies / factors / records, cursor pagination, tests | ~1 h 15 |
| User management (admin) | ~30 min |
| Summary endpoint + edge-case tests | ~20 min |
| CSV import, job queue, worker | ~30 min |
| Recalculation + audit log | ~1 h 10 |
| README + final verification (clean clone, `docker compose up`, tests) | ~2 h |
| **Total active work** | **~11 h** |

The later parts went much faster than the first ones: the structure (scoping helpers, pagination,
error format, test fixtures) was in place and reused, and AI assistance generated most of the code
(see below). Most of my time went into running, debugging, verifying and understanding.
---

## 10. AI Assistance

The brief allows AI assistance, which I used as a development support tool throughout the test.

**Claude (Opus 5.5, in the Claude app)** — used as an AI-assisted pair programming tool to help me work through the implementation:

- Helped me explore the architecture, refine the data model and evaluate key design decisions, including tenant isolation, factor overlap constraints, background job processing and import idempotency.
- Assisted with implementing application features and tests step by step, with explanations to help me understand the code and the reasoning behind the implementation.
- Reviewed my files and Git commits, helping me identify issues and correct implementation mistakes, configuration problems and test setup inconsistencies.
- Helped draft and refine parts of the implementation, including emission records and calculations, CSV imports, recalculation and audit logging. I integrated these changes into my project, reviewed them, ran the tests and committed the results myself.
- Assisted with drafting this README, which I subsequently reviewed and adapted.


**Claude Code** (CLI in my terminal) — used occasionally to assist with authentication tests. I reviewed the generated changes.
**What I did myself:**

- Set up and managed the development environment, including Docker Desktop, Docker Compose, PostgreSQL, Alembic, the Python virtual environment and the GitHub repository.
- Coordinated the implementation, integrated the proposed code into the project, checked file placement and configuration, and ran the application and tests throughout development.
- Investigated and resolved the issues encountered, including Docker and database startup problems, incorrect file locations, unsaved editor changes, syntax errors, outdated configuration, HTTP 500 responses and stale test database state.
- Tested the API manually through Swagger, including login, protected routes and invalid credentials returning HTTP 401.  
- Made the implementation and delivery decisions, organized the work into focused commits, checked that tests passed before committing, and prioritized implementing selected advanced requirements thoroughly rather than covering more requirements incompletely.
- Reviewed the final code and documentation to ensure I could explain the implementation and the reasoning behind the main design decisions.

**My approach was to use AI as a development assistant while remaining responsible for integration, debugging, testing, technical decisions and the final result.** I reviewed the implementation so that I can explain the design choices and trade-offs during the follow-up discussion.