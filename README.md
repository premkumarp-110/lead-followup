# Lead Follow-up Management Dashboard

An MVP dashboard that tells a BD (Business Development executive) **which leads they need to
contact next**, derived from the outcome of each lead's most recent call.

After a call ends, its transcript is analyzed. The analysis produces one of three outcomes —
`FOLLOW_UP_REQUIRED`, `CONVERTED` or `DROPPED` — which updates the lead and, in turn, the
dashboard. Converted and dropped leads drop off the active worklist automatically.

> **No LLM or external API calls are made in this version.** Call analysis is deterministic
> keyword/rule matching, deliberately placed behind the same interface a future LLM analyzer will
> implement. There is no authentication and no LeadSquared integration, by design.

---

## 1. Project overview

| | |
|---|---|
| **Frontend** | React 18 + Vite, plain CSS, axios |
| **Backend** | Python 3.11 + FastAPI + Pydantic v2, PyMongo (sync) |
| **Database** | Local MongoDB, URL supplied via `.env` |
| **Auth** | None (out of scope for the MVP) |

What the dashboard shows:

- **Summary cards** — Total Leads, Follow-ups Required, Due Today, Overdue, Converted, Dropped
- **Follow-ups Required** tab — the active worklist, most overdue first, with Overdue / Due /
  Upcoming badges and Mark Done / Reschedule / Cancel actions
- **No Follow-up Needed** tab — converted, dropped and closed leads
- **Filters** — search, BD, course, outcome, follow-up status, exact date, date range, and
  All / Today / Overdue / Upcoming quick filters. They all combine (AND).
- **Lead details modal** — lead info, latest call, follow-up, latest transcript, action history

---

## 2. Architecture

```
                 ┌──────────────────────────────┐
                 │  React + Vite dashboard      │  :5173
                 │  Dashboard / Tables / Modals │
                 └───────────────┬──────────────┘
                                 │  axios (VITE_API_BASE_URL)
                 ┌───────────────▼──────────────┐
                 │  FastAPI backend             │  :8000
                 │                              │
                 │  routes/leads.py             │  lists, detail, follow-up actions
                 │  routes/calls.py             │  POST /api/calls/{id}/process
                 │  routes/dashboard.py         │  summary + filter options
                 │                              │
                 │  services/call_analyzer.py   │  ◄── LLM SWAP POINT
                 │  services/followup_service.py│  outcome → lead projection, buckets
                 └───────────────┬──────────────┘
                                 │  PyMongo
                 ┌───────────────▼──────────────┐
                 │  MongoDB (local)             │
                 │  leads · calls               │
                 │  call_transcripts            │
                 │  call_outcomes               │
                 └──────────────────────────────┘
```

**Call analysis is a separate flow, not part of the call record.** Storing a call, storing its
transcript, and analyzing that transcript are three distinct steps. Analysis is triggered
explicitly by `POST /api/calls/{call_id}/process`, which is where a future "call completed" worker
or webhook will call in.

### Collections

| Collection | Key fields |
|---|---|
| `leads` | `lead_id`, `name`, `phone`, `email`, `course`, `assigned_bd{id,name}`, `lead_status`, `follow_up{…}`, `follow_up_history[]`, `last_call_at`, `latest_call_id`, `latest_outcome` |
| `calls` | `call_id`, `lead_id`, `started_at`, `ended_at`, `duration_seconds`, `transcript_id`, `status` |
| `call_transcripts` | `transcript_id`, `call_id`, `lead_id`, `transcript`, `created_at` |
| `call_outcomes` | `call_id` (unique), `lead_id`, `outcome`, `reason`, `follow_up{…}`, `processed_at`, `analyzer`, `confidence` |

`call_outcomes` is the source of truth for an analysis result. The `follow_up` block on the lead is
a denormalized copy of the latest outcome, so the dashboard's main query is a single indexed find
instead of a join.

### Stored vs. derived follow-up state

`follow_up.status` stores only durable state: **PENDING**, **COMPLETED**, **CANCELLED**.

The time-sensitive bucket is **computed on every read** from `follow_up.datetime` vs. now, so it
can never go stale in the database:

| Bucket | Rule |
|---|---|
| `OVERDUE` | follow-up time is in the past and still PENDING |
| `DUE` | follow-up time is today and within the next 2 hours |
| `UPCOMING` | follow-up time is in the future, outside the due window |
| `COMPLETED` / `CANCELLED` | from the stored status |

It is returned to the frontend as `follow_up.bucket`. All datetimes are stored as timezone-aware
UTC; the browser formats them in local time.

---

## 3. Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- MongoDB running locally (no auth needed for the MVP)

---

## 4. MongoDB setup

Make sure a local `mongod` is running and reachable:

```bash
sudo systemctl start mongod      # or: mongod --dbpath /your/data/path
mongosh --eval 'db.runCommand({ping: 1})'
```

The database (`lead_followup_db` by default) is created automatically on first seed. If MongoDB is
unreachable, the backend fails at startup with a clear message rather than erroring per request.

---

## 5. `.env` setup

The MongoDB URL is **never hardcoded**. Copy the examples and edit if needed:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

`backend/.env`:

```
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=lead_followup_db
ANALYZER_BACKEND=keyword
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

`frontend/.env`:

```
VITE_API_BASE_URL=http://localhost:8000
```

`.env` is gitignored; only `.env.example` is committed.

---

## 6. Backend installation

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 7. Frontend installation

```bash
cd frontend
npm install
```

---

## 8. Seed sample data

```bash
cd backend
python seed.py
```

This drops and recreates the four collections, then inserts **15 leads, 17 calls and 17
transcripts** and runs the *real* analyzer over them — the dashboard you see is produced by
`services/call_analyzer.py`, not by hardcoded outcomes.

Follow-up times are generated **relative to now**, so Due Today / Overdue / Upcoming are always
populated whenever you reseed:

| Segment | Count |
|---|---|
| Overdue follow-ups | 3 |
| Due today | 2 |
| Upcoming follow-ups | 3 |
| Converted | 3 |
| Dropped | 2 |
| New leads, call **left unprocessed** | 2 |

The last two exist so you can demo the processing flow live — the seed prints the exact curl
commands for them.

---

## 9. Start the backend

```bash
cd backend
uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

---

## 10. Start the frontend

```bash
cd frontend
npm run dev
```

Open http://localhost:5173.

---

## 11. API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/leads` | All leads (filterable) |
| GET | `/api/leads/follow-ups` | Leads that require follow-up (required + PENDING), overdue first |
| GET | `/api/leads/non-follow-ups` | Converted / dropped / closed leads |
| GET | `/api/leads/{lead_id}` | Lead detail incl. latest call, transcript and outcome |
| GET | `/api/leads/{lead_id}/calls` | All calls for a lead |
| GET | `/api/leads/{lead_id}/transcript/latest` | Latest transcript |
| GET | `/api/leads/{lead_id}/outcome` | Latest call outcome |
| PATCH | `/api/leads/{lead_id}/follow-up` | Mark done / cancel / reschedule (reason required) |
| GET | `/api/calls` | All calls (optional `?lead_id=`) |
| GET | `/api/calls/{call_id}` | One call |
| GET | `/api/calls/{call_id}/transcript` | That call's transcript |
| GET | `/api/calls/{call_id}/outcome` | That call's outcome |
| **POST** | **`/api/calls/{call_id}/process`** | **Analyze the transcript and update the lead** |
| GET | `/api/dashboard/summary` | Summary card counts |
| GET | `/api/dashboard/filters` | Distinct BDs / courses for the filter dropdowns |
| GET | `/api/health` | Liveness + DB check |

### Filter query parameters

Supported on `/api/leads`, `/api/leads/follow-ups`, `/api/leads/non-follow-ups` and
`/api/dashboard/summary`, and they combine with AND:

| Param | Example |
|---|---|
| `search` | `?search=divya` (name, email, phone or lead id) |
| `bd` | `?bd=Rahul` |
| `course` | `?course=Data Science` |
| `outcome` | `?outcome=CONVERTED` |
| `status` | `?status=PENDING` (also accepts `OVERDUE`) |
| `date` | `?date=2026-09-22` |
| `date_from` / `date_to` | `?date_from=2026-09-21&date_to=2026-09-28` |
| `bucket` | `?bucket=ALL\|OVERDUE\|DUE\|DUE_TODAY\|UPCOMING\|COMPLETED\|CANCELLED` |

```bash
# "Rahul's overdue leads"
curl "http://localhost:8000/api/leads/follow-ups?bd=Rahul&bucket=OVERDUE"
```

### Follow-up actions

Every action requires a `reason`, which is appended to the lead's `follow_up_history`:

```bash
curl -X PATCH http://localhost:8000/api/leads/L004/follow-up \
  -H 'Content-Type: application/json' \
  -d '{"action":"COMPLETE","reason":"Spoke to the lead; demo session booked."}'

curl -X PATCH http://localhost:8000/api/leads/L001/follow-up \
  -H 'Content-Type: application/json' \
  -d '{"action":"RESCHEDULE","reason":"Lead was travelling.","new_datetime":"2026-09-24T09:30:00Z"}'

curl -X PATCH http://localhost:8000/api/leads/L008/follow-up \
  -H 'Content-Type: application/json' \
  -d '{"action":"CANCEL","reason":"Duplicate lead; handled under L006."}'
```

`COMPLETE` and `CANCEL` remove the lead from the active worklist; `RESCHEDULE` keeps it PENDING at
the new time.

---

## 12. Example call-processing flow

The seed leaves two calls unprocessed on purpose. Pick one (e.g. `CALL016`, lead `L014`):

```bash
# 1. Before: the lead is NOT on the follow-up list
curl -s "http://localhost:8000/api/dashboard/summary"
# → {"total_leads":15,"follow_ups_required":8, ... ,"unprocessed_calls":2}

# 2. "Call completed" -> analyze the transcript
curl -X POST http://localhost:8000/api/calls/CALL016/process
```

```json
{
  "call_id": "CALL016",
  "lead_id": "L014",
  "outcome": "FOLLOW_UP_REQUIRED",
  "reason": "Lead asked to be contacted again (\"call me on\"). Callback scheduled for 22 Sep 2026 22:14 UTC.",
  "follow_up": { "required": true, "date": "2026-09-22", "time": "22:14", "status": "PENDING" },
  "lead_status": "FOLLOW_UP",
  "analyzer": "keyword-v1",
  "already_processed": false,
  "message": "Call processed and lead updated."
}
```

```bash
# 3. After: the lead now appears on the dashboard
curl -s "http://localhost:8000/api/dashboard/summary"
# → follow_ups_required is now 9, unprocessed_calls is now 1
```

What the endpoint does:

1. Loads the call (404 if unknown).
2. Loads its transcript (422 if the call has no transcript yet).
3. Passes the transcript to the configured analyzer.
4. Upserts `call_outcomes` keyed on `call_id`.
5. Updates the lead's `lead_status` and `follow_up` block.
6. `FOLLOW_UP_REQUIRED` → status `FOLLOW_UP` + follow-up date/time, status `PENDING`.
   `CONVERTED` → status `CONVERTED`, no follow-up. `DROPPED` → status `DROPPED`, no follow-up.

**Idempotency:** `call_outcomes.call_id` is uniquely indexed, so re-posting the same call
overwrites the outcome instead of creating a duplicate and returns `already_processed: true`.
A lead is only updated from its *most recent* call, so replaying an older call cannot resurrect a
stale follow-up.

### Deterministic analysis rules (MVP)

Evaluated in order — **DROPPED → CONVERTED → FOLLOW_UP → fallback** — so
*"I paid for another course, not interested in this one"* resolves to DROPPED.

| Outcome | Phrases |
|---|---|
| `DROPPED` | not interested, no longer interested, not interested anymore, don't want the course, please don't call, don't contact me, cancel |
| `CONVERTED` | i want to enroll, i have completed the payment, payment completed, i want to proceed, i have registered, decided to join, send me the enrollment details |
| `FOLLOW_UP_REQUIRED` | call me tomorrow, call me later, call me next week, contact me tomorrow, follow up tomorrow, get back to me, i will discuss and let you know, please call again, call me on/at/back |

No phrase matched → `FOLLOW_UP_REQUIRED` with low `confidence`, so a lead is never silently lost.

Date/time extraction is intentionally simple (explicit dates like `22 Sep 2026` or `2026-09-22`,
clock times like `11 AM` / `2:30 pm` / `14:00`, and the keywords `tomorrow` / `day after tomorrow`
/ `next week`) resolved against the call's `ended_at`. Building a real natural-language date parser
is the LLM's job, not the MVP's.

Check the rules against the spec's sample transcripts at any time:

```bash
cd backend && python check_analyzer.py
```

---

## 13. Future LLM integration

```
Call completed → Transcript stored → Call Analysis Worker → LLM → structured JSON
        → call_outcomes → lead updated → dashboard reflects it automatically
```

Everything downstream of analysis already exists. The only piece to replace is the analyzer.
`app/services/call_analyzer.py` defines the contract:

```python
class AnalysisResult(BaseModel):
    outcome: Outcome                      # FOLLOW_UP_REQUIRED | CONVERTED | DROPPED
    reason: str
    follow_up_required: bool = False
    follow_up_datetime: datetime | None = None
    analyzer: str = "keyword-v1"
    confidence: float = 1.0

class CallAnalyzer(Protocol):
    name: str
    def analyze_call(self, transcript: str, *, context: dict | None = None) -> AnalysisResult: ...
```

That shape is deliberately identical to the JSON an LLM will return:

```json
{
  "outcome": "FOLLOW_UP_REQUIRED",
  "reason": "Lead requested a callback after discussing the course with parents.",
  "follow_up_required": true,
  "follow_up_datetime": "2026-09-22T11:00:00Z"
}
```

**To plug in an LLM:**

1. Add `app/services/llm_call_analyzer.py` with an `LLMCallAnalyzer` class implementing
   `analyze_call` (call the model, parse its JSON into `AnalysisResult`).
2. Register it in the `_ANALYZERS` registry in `call_analyzer.py`:
   `_ANALYZERS = {"keyword": KeywordCallAnalyzer, "llm": LLMCallAnalyzer}`
3. Set `ANALYZER_BACKEND=llm` in `backend/.env`.

No route, service, schema or frontend change is required. Routes only ever call `get_analyzer()`,
never a concrete class, and each outcome records which analyzer produced it in
`call_outcomes.analyzer` so keyword- and LLM-derived results stay distinguishable.

Running the analysis asynchronously later (queue or background worker) means calling the same
`analyze_call` + `followup_service.apply_analysis` pair from the worker instead of from the
request; the persistence and dashboard layers are unchanged.

---

## Project structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py                 FastAPI app, CORS, lifespan, error handlers
│   │   ├── config.py               .env-driven settings (no hardcoded URLs)
│   │   ├── database.py             MongoClient, collections, indexes
│   │   ├── models/                 lead.py, call.py, schemas.py
│   │   ├── routes/                 leads.py, calls.py, dashboard.py, filters.py
│   │   └── services/
│   │       ├── call_analyzer.py    ← deterministic today, LLM later
│   │       └── followup_service.py outcome → lead projection, buckets, actions
│   ├── seed.py                     sample data + runs the real analysis flow
│   ├── check_analyzer.py           analyzer sanity check
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── components/             Dashboard, SummaryCards, FilterBar, LeadTable,
│   │   │                           LeadDetailsModal, FollowUpActionModal, format.js
│   │   ├── services/api.js
│   │   ├── App.jsx, main.jsx, index.css
│   ├── package.json, vite.config.js, .env.example
├── MODULES.md                      build progress tracker
├── .env.example
└── README.md
```

## Notes and limitations (MVP)

- No authentication, no LeadSquared integration, no audio/speech-to-text, no notifications.
- `unprocessed_calls` in the summary is a global pipeline indicator and is not narrowed by the
  lead filters.
- The `DUE` window is fixed at 2 hours (`DUE_WINDOW` in `followup_service.py`).
- Dashboard data refreshes on filter change or via the Refresh button; there is no polling or
  websocket push.
