# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Lead Follow-up Management Dashboard — an MVP that tells a BD executive which leads to contact
next, derived from the outcome of each lead's most recent call. FastAPI + PyMongo backend,
React 18 + Vite frontend, local MongoDB. No auth, no LeadSquared integration.

`README.md` documents the full API surface, seed data, filter params and LLM-integration plan.
`MODULES.md` is the build tracker — update it when completing a module of work.

## Commands

Backend (from `backend/`, venv already exists at `backend/.venv`):

```bash
source .venv/bin/activate
pip install -r requirements.txt
python seed.py                      # drops + recreates all 4 collections, reseeds
uvicorn app.main:app --reload       # :8000, docs at /docs
python check_analyzer.py            # analyzer regression check (see below)
```

Frontend (from `frontend/`):

```bash
npm install
npm run dev                         # :5173
npm run build
```

MongoDB must be running (`sudo systemctl start mongod`); the backend pings it at startup and
fails fast with a readable message if it is not.

Copy `backend/.env.example` → `backend/.env` and `frontend/.env.example` → `frontend/.env` before
first run. There is no Mongo URL default in code — a missing `MONGODB_URL` is a startup error by
design, not something to "fix" with a fallback.

### Tests

There is no pytest/vitest suite. `backend/check_analyzer.py` is the only automated check: 9 cases
(the spec's sample transcripts plus edge cases) asserting outcome and resolved follow-up datetime
against a fixed reference time. Run it after any change to `call_analyzer.py`; to check one case,
edit or trim the `CASES` list.

## Architecture

### Call analysis is a separate flow from the call record

Storing a call, storing its transcript, and analyzing that transcript are three distinct steps.
Analysis only happens on `POST /api/calls/{call_id}/process` — that endpoint is the seam where a
future "call completed" worker or webhook plugs in.

### `call_analyzer.py` is the LLM swap point — keep it sealed

Everything downstream depends only on `AnalysisResult`, the `CallAnalyzer` protocol, and
`get_analyzer()`. Never import `KeywordCallAnalyzer` (or any concrete analyzer) from routes,
services or `seed.py`; go through `get_analyzer()` / the module-level `analyze_call()`. A new
backend is added by registering a class in `_ANALYZERS` and setting `ANALYZER_BACKEND` in `.env` —
no route, schema or frontend change. `AnalysisResult`'s shape is deliberately 1:1 with the JSON an
LLM will return.

This version makes **no network or LLM calls of any kind**; that is an explicit acceptance
criterion, not an oversight.

Analyzer rule order is `DROPPED → CONVERTED → FOLLOW_UP → fallback`, and that order is load-bearing
("I paid for another course, not interested in this one" must resolve to DROPPED). The fallback
always returns `FOLLOW_UP_REQUIRED` with low `confidence` so a lead is never silently dropped from
the worklist.

### Stored vs. derived follow-up state

`follow_up.status` stores only durable state: `PENDING` / `COMPLETED` / `CANCELLED`.
The time-sensitive bucket (`OVERDUE` / `DUE` / `UPCOMING`) is **computed on every read** by
`followup_service.compute_bucket()` and attached as `follow_up.bucket` by `decorate_lead()`.
Never persist a bucket. `FollowUpStatus.OVERDUE` exists only so the `?status=` query param accepts
it; `filters.py` routes it to the derived-bucket path and never writes it.

### Filtering is two-phase — both halves are required

`routes/filters.py` owns one shared `LeadFilters` dependency used by the leads and dashboard
routes, so filters AND together identically everywhere:

1. `filters.mongo_query(base)` — the part Mongo can evaluate.
2. `apply_bucket_filter(docs, filters, now)` — the derived-bucket part Mongo cannot.

Any new list endpoint must call both, or `?bucket=` is silently ignored. `compute_bucket` is
called with a single `now` per request so rows in one response can't disagree about time.

### Denormalization and idempotency

`call_outcomes` is the source of truth for an analysis result; the lead's `follow_up` block is a
denormalized copy of the latest outcome so the dashboard's main query is one indexed find.
`followup_service.apply_analysis()` enforces two invariants:

- Upsert keyed on `call_id` (uniquely indexed in `database.py`) — reprocessing overwrites instead
  of duplicating, and the response reports `already_processed`.
- The lead is only updated when this call is the lead's **most recent** call (`_is_latest_call`),
  so replaying an older call cannot resurrect a stale follow-up. The response reports
  `applied_to_lead: false` in that case.

Every BD action (`COMPLETE` / `CANCEL` / `RESCHEDULE`) requires a `reason` and appends an entry to
`follow_up_history`. Acting on a lead with no active follow-up raises `ValueError` → 409/422.

### Time

All datetimes are tz-aware UTC. `MongoClient` is created with `tz_aware=True`, and
`followup_service._as_utc()` guards anything that might come back naive. The browser does the local
formatting (`frontend/src/components/format.js`). The `DUE` window is a fixed 2 hours
(`DUE_WINDOW` in `followup_service.py`).

### Error handling

`main.py` registers global handlers: `ValueError` → 422, `PyMongoError` → 503. That is why domain
code in `filters.py` and `followup_service.py` raises plain `ValueError` for bad input rather than
`HTTPException` — routes only raise `HTTPException` for 404/409.

### Route ordering

In `routes/leads.py`, `/follow-ups` and `/non-follow-ups` are declared **before** `/{lead_id}`.
Adding a new literal sub-path below `/{lead_id}` would make it match as a lead id instead.

### Frontend

`components/Dashboard.jsx` holds all state and all API calls; every other component is
presentational and receives props. Summary cards, both tables and the filter options are refetched
together by one `load()` whenever `filters` changes — there is no polling or websocket.
`services/api.js` wraps axios, strips empty filter values via `cleanParams` (so `?bd=` is never
sent), and converts axios failures into user-readable messages. The `bucket` filter is applied to
the follow-ups list only and deliberately stripped before fetching the closed-leads list.
