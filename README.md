# AI-Powered Lead Follow-up Management System

An internal tool for an EdTech BD team. After a BD finishes a call with a lead, they upload the
recording (or paste its URL), pick the lead and the caller, and the system:

1. stores the call record,
2. transcribes the audio,
3. analyzes the conversation with **Vertex AI Gemini**,
4. validates the structured result,
5. decides the next action — `FOLLOW_UP_REQUIRED`, `CONVERTED` or `DROPPED`,
6. updates the lead and the dashboard.

Converted and dropped leads leave the active worklist automatically. Follow-ups are bucketed as
Overdue / Due / Upcoming / Unscheduled on every read, so the "who do I call next?" list is never
stale.

> **Scope:** no authentication, no LeadSquared/WhatsApp/SMS/email, no queues, no vector DB. One
> transcription call and one analysis call per recording. That is deliberate (spec §34).

---

## 1. Stack

| | |
|---|---|
| Frontend | React 18 + Vite, plain CSS, axios — talks **only** to the FastAPI backend |
| Backend | Python 3.11, FastAPI, Pydantic v2, PyMongo |
| Database | MongoDB (local or `docker compose`) |
| AI | Vertex AI Gemini via the Google Gen AI Python SDK (`google-genai`) |
| Audio | ffprobe for duration; files stored locally or referenced by URL |

---

## 2. Architecture

```
 React dashboard (:5173)
   └─ axios ─► FastAPI (:8000)
                 routes/calls.py ─────► services/audio_service.py          validate · store · ffprobe
                                  ─────► services/call_analysis_service.py  ORCHESTRATOR
                                            ├─► transcription_service.py    audio → text      (Gemini, Vertex)
                                            ├─► llm_service.py              text → decision   (Gemini, Vertex)
                                            └─► followup_service.py         decision → lead
                 routes/leads.py, dashboard.py, callers.py
                 └─ PyMongo ─► leads · callers · calls · call_transcripts · call_analyses
```

Each arrow is a module boundary. `call_analysis_service.py` is the only file that knows the
order of the steps; moving processing to a background worker later means calling
`process_call()` from the worker instead of the request handler. Swapping the LLM touches
`llm_service.py` only.

### Collections

| Collection | Purpose |
|---|---|
| `leads` | Lead master + a denormalized `follow_up` block copied from the latest analysis |
| `callers` | BD executives who make calls (`caller_id`, name, role) |
| `calls` | One per recording: `source_type` (UPLOAD/URL), audio metadata, processing `status`, `transcript_id`, `analysis_id` |
| `call_transcripts` | Transcript text, language, provider — separate from the call |
| `call_analyses` | Every analysis attempt: outcome, follow-up, intent, summary, key points, confidence, `raw_response`, `status`, `degraded` |

Call statuses: `UPLOADED → PROCESSING → TRANSCRIBING → ANALYZING → COMPLETED` or `FAILED`
(with `error` and `failed_stage`). The orchestrator writes each transition, so the UI stepper
polls real state.

### Follow-up buckets (derived on every read, never stored)

| Bucket | Rule |
|---|---|
| `OVERDUE` | follow-up datetime is in the past and status is `PENDING` |
| `DUE` | today and within the next 2 hours |
| `UPCOMING` | in the future, outside the due window |
| `UNSCHEDULED` | follow-up required but the lead never gave a date (`datetime: null`) |
| `COMPLETED` / `CANCELLED` | from the stored status |

`UNSCHEDULED` exists because the analyzer must **never invent a date** (§12). Those leads still
need action, so they stay on the worklist — sorted last.

### When Gemini fails

If Vertex is unreachable, misconfigured, or returns JSON that fails validation after one repair
retry:

1. A `call_analyses` document with `status: FAILED` and the full `raw_response` is written
   (debugging trail, §13).
2. If `ANALYSIS_FALLBACK_ENABLED=true` (default), the deterministic keyword analyzer runs and
   writes a second document with `status: COMPLETED`, `degraded: true`,
   `model: keyword-fallback-v1`. This one updates the lead, and the UI shows a "fallback" notice.
3. With `ANALYSIS_FALLBACK_ENABLED=false`, the call is marked `FAILED` and the lead is untouched.

Transcription has no equivalent fallback — without Vertex, uploaded audio cannot become text and
the call fails at the `TRANSCRIBING` step with a message naming the missing setting.

---

## 3. Prerequisites

- Python 3.11+, Node.js 18+, MongoDB running locally
- `ffmpeg` (for `ffprobe`) — `sudo apt install ffmpeg`
- A Google Cloud project with **Vertex AI API** enabled and credentials with the
  **Vertex AI User** role (see §5)

---

## 4. Install

```bash
# backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit -- see next section

# frontend
cd ../frontend
npm install
cp .env.example .env
```

---

## 5. Configure Google Cloud (Vertex AI)

**Enable the API** (the only one needed — no Cloud Storage, no Speech-to-Text):

```bash
gcloud services enable aiplatform.googleapis.com --project=<PROJECT_ID>
```

**Grant the role** to whichever identity the backend runs as:
`roles/aiplatform.user` (Vertex AI User). Billing must be enabled on the project.

**Authenticate** — either:

```bash
gcloud auth application-default login          # Application Default Credentials
```

and leave `GOOGLE_APPLICATION_CREDENTIALS` blank, **or** download a service-account key and set
`GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json`. The key is gitignored; never commit it.

**Fill `backend/.env`:**

```ini
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1        # or global / asia-south1
VERTEX_AI_MODEL=gemini-2.5-flash         # or gemini-2.5-pro
```

Everything else in `.env.example` has a working default. `GET /api/health` reports
`vertex_configured` so you can confirm the backend sees the settings.

### All environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CALL_ANALYZER_ENABLED` | `false` | Shows/hides the "Analyze New Call" section and enables its endpoints (upload, URL, process). Off by default; the dashboard runs read-only over seeded data until set to `true` |
| `MONGODB_URL` | — (required) | Mongo connection string |
| `DATABASE_NAME` | — (required) | Database name |
| `GOOGLE_CLOUD_PROJECT` | blank | GCP project with Vertex AI enabled |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex region or `global` |
| `VERTEX_AI_MODEL` | `gemini-2.5-flash` | Gemini model id |
| `GOOGLE_APPLICATION_CREDENTIALS` | blank (ADC) | Path to a service-account key |
| `TRANSCRIPTION_PROVIDER` | `vertex` | `vertex` implemented; `local`, `gcp-stt` raise a clear not-configured error |
| `ANALYSIS_FALLBACK_ENABLED` | `true` | Keyword fallback when Gemini fails |
| `AUDIO_STORAGE_MODE` | `local` | `local` stores uploads; `url` disables uploads, URL input only |
| `AUDIO_PLAYBACK_ENABLED` | `true` | Show the audio player in the modal |
| `AUDIO_UPLOAD_DIR` | `uploads` | Where uploads are written (relative to `backend/`) |
| `MAX_AUDIO_MB` | `20` | Upload cap — Gemini's inline-audio limit |
| `ALLOWED_AUDIO_TYPES` | `mp3,wav,m4a,ogg,webm` | Accepted extensions |
| `CORS_ORIGINS` | localhost:5173 | Allowed browser origins |

The frontend has one variable: `VITE_API_BASE_URL=http://localhost:8000`. No credential ever
reaches the browser; the UI reads non-secret flags from `GET /api/config`.

---

## 6. Seed sample data

```bash
cd backend && python seed.py
```

Drops and recreates all five collections, then inserts **15 leads, 5 callers, 17 calls,
17 transcripts and 17 analyses**. Analyses are pre-baked (`model: "seed"`) so reseeding makes no
API calls. Follow-up times are relative to now, so every bucket is always populated:

| Segment | Leads |
|---|---|
| Overdue | 3 |
| Due today | 2 |
| Upcoming | 3 |
| Unscheduled (follow-up, no date) | 1 |
| Converted | 3 |
| Dropped | 2 |
| New, no calls yet | 1 — **Sandhya Rajan (L015)**, the demo target |

Seeded calls have transcripts but no audio file; the modal says so.

---

## 7. Run

```bash
# terminal 1
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload
# terminal 2
cd frontend && npm run dev
```

Backend: http://localhost:8000 (docs at `/docs`). Frontend: http://localhost:5173.

Or with Docker:

```bash
cp .env.example .env    # fill GOOGLE_CLOUD_PROJECT and the service-account path
docker compose up --build
```

---

## 8. Using the dashboard

**Analyze New Call** (top of the page) — only shown when `CALL_ANALYZER_ENABLED=true` in
`backend/.env`. With it unset or `false`, the section is hidden and its four endpoints
(`/upload`, `/validate-url`, `/from-url`, `/{id}/process`) return `403`; everything else
(dashboard, tables, filters, the lead details modal, history already on disk) keeps working:

1. **Upload Audio** (default) — choose an mp3/wav/m4a/ogg/webm up to 20 MB. Filename, size and
   duration are shown; unsupported types are rejected before upload. Or switch to **Audio URL**,
   paste a link and click **Validate Audio URL** — the backend checks the scheme, refuses
   private/loopback hosts, and confirms the content type and size without downloading.
2. Select the **Lead** and the **Caller / BD**. Both are required; the button stays disabled
   until then.
3. **Analyze Call.** The stepper shows real pipeline state:
   `✓ Audio uploaded → ⏳ Transcribing → Analyzing → Updating lead → ✓ Completed`.
4. The result panel shows outcome, follow-up date/time, reason, customer intent, confidence,
   summary, key points, and the transcript. The tables refresh automatically.

**Summary cards** (Total / Follow-ups / Due Today / Overdue / Converted / Dropped) and **quick
filters** (All / Due Today / Overdue / Upcoming / Unscheduled / Converted / Dropped) combine with
the search, BD, course, outcome, status and date filters (AND).

**Tabs:** *Leads Requiring Follow-up* (Lead · Course · BD · Last Call · Follow-up · Status ·
Reason · Actions) and *Completed / Closed* (converted, dropped, completed, cancelled).

**Click a lead** for the modal: lead details, latest call (date, caller, duration, source,
processing status, audio player), AI analysis (outcome, intent, confidence, summary, key points),
follow-up (required, date, time, status, reason), full transcript, and action history.

---

## 9. API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/leads` | All leads, filterable |
| GET | `/api/leads/follow-ups` | Active worklist: overdue → due → upcoming → unscheduled |
| GET | `/api/leads/closed` | Converted / dropped / completed / cancelled |
| GET | `/api/leads/{id}` | Lead + latest call, caller, transcript, analysis |
| GET | `/api/leads/{id}/calls` | All calls for the lead |
| GET | `/api/leads/{id}/latest-transcript` | Latest transcript |
| GET | `/api/leads/{id}/latest-analysis` | Latest completed analysis |
| PATCH | `/api/leads/{id}/follow-up` | `COMPLETE` / `CANCEL` / `RESCHEDULE` (reason required) |
| GET | `/api/callers` | BD list for the dropdown |
| POST | `/api/calls/upload` | multipart: `file`, `lead_id`, `caller_id` → call record |
| POST | `/api/calls/validate-url` | `{audio_url}` → reachability + content type + size |
| POST | `/api/calls/from-url` | `{audio_url, lead_id, caller_id}` → call record |
| POST | `/api/calls/{id}/process` | Transcribe → analyze → validate → store → update lead |
| GET | `/api/calls/{id}/status` | Poll target for the stepper |
| GET | `/api/calls/{id}` · `/transcript` · `/analysis` · `/analyses` | Call reads (`/analyses` includes failed attempts) |
| GET | `/api/calls/{id}/audio` | Streams the recording by call id (never a path); 404 when playback is disabled |
| GET | `/api/dashboard/summary` · `/filters` | Card counts, dropdown options |
| GET | `/api/config` · `/api/health` | Non-secret UI flags; liveness + `vertex_configured` |

Filter params on the list endpoints and the summary: `search`, `bd`, `course`, `outcome`,
`status`, `date`, `date_from`, `date_to`,
`bucket=ALL|OVERDUE|DUE|DUE_TODAY|UPCOMING|UNSCHEDULED|COMPLETED|CANCELLED|CONVERTED|DROPPED`.

```bash
# Rahul's overdue leads
curl "http://localhost:8000/api/leads/follow-ups?bd=Rahul&bucket=OVERDUE"

# Full flow from the command line
curl -F file=@call.mp3 -F lead_id=L015 -F caller_id=BD003 http://localhost:8000/api/calls/upload
curl -X POST http://localhost:8000/api/calls/CALL-XXXXXXXX/process
```

---

## 10. The analysis contract

Gemini is asked for exactly this JSON and the response is validated by
`backend/app/models/analysis.py::CallAnalysisResult` before anything is stored:

```json
{
  "outcome": "FOLLOW_UP_REQUIRED",
  "follow_up_required": true,
  "follow_up": {
    "date": "2026-09-23", "time": "11:00",
    "datetime": "2026-09-23T11:00:00+05:30",
    "reason": "Lead requested a callback after discussing the course with parents."
  },
  "customer_intent": "INTERESTED",
  "summary": "Lead is interested but wants to discuss the course with family.",
  "key_points": ["Interested in Full Stack Development", "Needs to discuss fees with parents"],
  "confidence": 0.94
}
```

Rules enforced by the prompt **and** the validator: `outcome` must be one of the three values
(anything else fails validation); `CONVERTED`/`DROPPED` force `follow_up = null`; a required
follow-up with no inferable date gets `datetime: null` and the reason
*"Lead requested follow-up but did not specify a date/time."*; relative phrases ("tomorrow",
"next week") resolve against the call's timestamp in Asia/Kolkata; dates are never invented.

---

## 11. Error handling

Every failure returns `{"detail": "<readable message>"}`, never a stack trace:

| Situation | Response |
|---|---|
| Unsupported type, too large, empty file, bad/private URL | `422` |
| Unknown lead or caller | `404` |
| Call already processing | `409` |
| Transcription or Vertex failure | `503`, call marked `FAILED` with `failed_stage` |
| Invalid LLM JSON after retry | `FAILED` analysis stored with `raw_response`; fallback or `503` |
| MongoDB unreachable | startup refuses; per-request `503` |
| Missing `MONGODB_URL` | startup refuses with instructions |
| Missing Vertex settings | boots, warns, AI routes return `503` naming the setting |

---

## 12. Regression check

```bash
cd backend && python check_analyzer.py     # 11 cases, no network
```

Covers the spec's five sample transcripts (follow-up with date, converted, dropped, follow-up
without date, payment follow-up) plus edge cases, against the fallback analyzer.

---

## Project structure

```
backend/
  app/
    main.py                       app, CORS, error handlers, /api/health, /api/config
    config.py                     .env settings; vertex_config_error()
    database.py                   MongoClient, collection names, indexes
    models/   lead.py · caller.py · call.py · analysis.py · schemas.py
    routes/   leads.py · calls.py · callers.py · dashboard.py · filters.py
    services/
      audio_service.py            validation, storage, ffprobe, SSRF guard
      transcription_service.py    provider registry; VertexGeminiTranscriber
      llm_service.py              Gemini prompt + validation + repair retry + fallback
      call_analyzer.py            deterministic keyword fallback
      call_analysis_service.py    pipeline orchestrator + status transitions
      followup_service.py         buckets, lead projection, BD actions
      vertex_client.py            shared google-genai client + error wording
  seed.py · check_analyzer.py · requirements.txt · Dockerfile · .env.example · uploads/
frontend/
  src/components/  CallAnalyzer · ProcessingSteps · AudioPlayer · Dashboard · SummaryCards ·
                   FilterBar · LeadTable · LeadDetailsModal · FollowUpActionModal · format.js
  src/services/api.js · App.jsx · main.jsx · index.css · Dockerfile · .env.example
docker-compose.yml · .env.example · .gitignore · CLAUDE.md · MODULES.md
```
