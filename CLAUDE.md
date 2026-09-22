# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AI-Powered Lead Follow-up Management System — an internal EdTech BD tool. A BD uploads a call
recording (or an audio URL), links it to a lead and a caller; the backend transcribes it and
analyzes it with Vertex AI Gemini, validates the result, decides FOLLOW_UP_REQUIRED / CONVERTED /
DROPPED, and updates the lead and dashboard. FastAPI + PyMongo backend, React 18 + Vite frontend,
local MongoDB. No auth, no LeadSquared, no queues — deliberate MVP scope.

`README.md` documents setup, every env var, the API, and the analysis contract.
`MODULES.md` is the build tracker — update it when completing a module of work.

## Commands

Backend (from `backend/`, venv at `backend/.venv`):

```bash
source .venv/bin/activate
pip install -r requirements.txt
python seed.py                      # drops + recreates all 5 collections; no API calls
uvicorn app.main:app --reload       # :8000, docs at /docs
python check_analyzer.py            # 11-case regression check for the fallback analyzer
```

Frontend (from `frontend/`): `npm install`, `npm run dev` (:5173), `npm run build`.

`docker compose up --build` runs mongo + backend + frontend; it needs a root `.env` (see
`.env.example`) and a service-account key mounted from `GOOGLE_APPLICATION_CREDENTIALS_HOST_PATH`.

MongoDB must be running. `ffprobe` (from ffmpeg) is used for durations; absent it degrades to
`duration_seconds: null`, never an error.

### Tests

No pytest/vitest suite. `backend/check_analyzer.py` is the only automated check — run it after
touching `call_analyzer.py`. Vertex code paths cannot be unit-tested offline; the recipe for
verifying the pipeline without Vertex is in the "Fallback" section below.

## Configuration

`CALL_ANALYZER_ENABLED` (default `false`) gates the "Analyze New Call" feature end to end: the
frontend only mounts `CallAnalyzer.jsx` when `config.call_analyzer_enabled` is true (from
`GET /api/config`), and the backend independently enforces it — `_require_analyzer_enabled()` in
`routes/calls.py` returns 403 from `/upload`, `/validate-url`, `/from-url` and `/{id}/process`
when it's off. Read endpoints (status, transcript, analysis, audio, lists) are never gated, so
history produced while it was on stays viewable. Never gate a read endpoint on this flag.

`app/config.py` loads `backend/.env`. `MONGODB_URL`/`DATABASE_NAME` are required at startup.
Vertex settings are **checked lazily** via `settings.vertex_config_error()` so the app boots,
seeds and serves the dashboard on an unconfigured machine; only AI routes fail, with a message
naming the missing setting. Don't add a startup assertion for Vertex.

The frontend never reads backend `.env`. Non-secret flags reach it through `GET /api/config`
(`UIConfig` in `schemas.py`). Adding a UI-relevant setting means adding it there.

## Architecture

### Pipeline order lives in exactly one file

`services/call_analysis_service.py::process_call()` is the orchestrator: it calls
`transcription_service.transcribe_audio()` → `llm_service.analyze_conversation()` →
`followup_service.apply_analysis_to_lead()`, and writes the call's `status` at every transition
(`PROCESSING → TRANSCRIBING → ANALYZING → COMPLETED` / `FAILED` + `failed_stage`). Routes stay
thin. Moving to a background worker later means calling `process_call()` from the worker.

Re-processing a call reuses its stored transcript (`transcript_id` on the call), so a retry only
repeats the step that failed.

### `llm_service.py` is the LLM swap point

`analyze_conversation()` returns an `AnalysisRun` — a list of `AnalysisAttempt`s. The orchestrator
persists **every** attempt to `call_analyses` (failed ones carry `raw_response` + `error`) and
points `calls.analysis_id` at `run.final`. The Gemini call uses `response_mime_type=application/json`
plus `RESPONSE_SCHEMA`, then validates through `CallAnalysisResult`, with one repair retry that
quotes the validation error back. `vertex_client.py` owns client construction and turns SDK
exceptions into user-safe wording (`describe_api_error`) — both Gemini callers go through it.

### `CallAnalysisResult` is the trust boundary (models/analysis.py)

Raw model output never reaches the lead without passing this model. Strictness is deliberately
uneven: `outcome` is strict (it drives `lead_status`); `customer_intent` normalises unknown labels
to `UNCLEAR`; `confidence` accepts `94` as `0.94`. The `model_validator` makes outcome and
follow-up agree: CONVERTED/DROPPED force `follow_up=None`; FOLLOW_UP_REQUIRED with no datetime
gets `NO_DATE_REASON`. `AnalysisFollowUp` derives `date`/`time` from `datetime` in the *returned
offset* (IST), not UTC — the spec's example shape depends on that.

Two models have a field literally named `datetime` (`FollowUp`, `AnalysisFollowUp`); both use a
`DateTime = datetime` alias to avoid shadowing. Keep doing that.

### Fallback when Gemini fails

If Vertex is unreachable/misconfigured or JSON fails validation after the retry, and
`ANALYSIS_FALLBACK_ENABLED` (default true), `call_analyzer.py` (deterministic keywords) produces
the result flagged `degraded=True, model="keyword-fallback-v1"`. Rule order DROPPED → CONVERTED →
FOLLOW_UP is load-bearing. It **never invents a date**: no extractable date → `datetime=None`.

To exercise the whole pipeline offline: upload a file, insert a `call_transcripts` doc, set
`transcript_id` on the call, then `POST /process` — transcription is skipped, Gemini fails on
config, fallback runs, two analysis docs are written, lead updates.

Transcription has **no** fallback; only `vertex` is implemented in `transcription_service.py`.
`local`/`gcp-stt` are registered so they fail with a specific message (spec §8).

### Stored vs. derived follow-up state

`follow_up.status` stores only `PENDING` / `COMPLETED` / `CANCELLED`. The bucket (`OVERDUE` /
`DUE` / `UPCOMING` / `UNSCHEDULED`) is computed on every read by
`followup_service.compute_bucket()` and attached by `decorate_lead()`. Never persist a bucket.
`UNSCHEDULED` = required + PENDING + `datetime: None`; it counts in `follow_ups_required` but not
in due/overdue/upcoming, and `sort_worklist()` puts it last (Mongo sorts nulls *first*, so the
worklist is re-sorted in Python). `FollowUpStatus.OVERDUE` exists only so `?status=` accepts it.

### Filtering is two-phase — both halves are required

`routes/filters.py` owns the shared `LeadFilters` dependency: `mongo_query(base)` for what Mongo
can evaluate, then `apply_bucket_filter(docs, filters, now)` for derived buckets. Any new list
endpoint must call both or `?bucket=` is silently ignored. `CONVERTED`/`DROPPED` are accepted as
`bucket` values and become a `lead_status` clause in `mongo_query` (the frontend switches to the
closed tab for them). One `now` per request.

### Denormalization and the latest-call guard

`call_analyses` is the source of truth; `leads.follow_up` is a denormalized copy of the winning
analysis. `apply_analysis_to_lead()` only updates the lead when no **COMPLETED** call for that lead
ended later (`is_latest_call`). Unprocessed/failed calls don't count — they have no outcome to
protect. Replaying an older recording therefore can't overwrite a newer outcome, but an abandoned
upload can't block a real one either.

### Audio

`audio_service.py` owns validation and storage. Stored filenames derive from `call_id`, never the
client's name. `audio_file_path` is `exclude=True` on the `Call` model — the browser only ever gets
`GET /api/calls/{id}/audio`, which resolves the path from the call doc and refuses anything outside
`settings.upload_path`. URL calls redirect (307). `validate_audio_url` resolves DNS and rejects
private/loopback/link-local hosts before any request (SSRF guard). MIME checks are lenient when the
extension is allow-listed — hosts serve `.ogg` as `application/ogg` — but `text/html` etc. are
always refused. `MAX_AUDIO_MB=20` tracks Gemini's inline-audio cap.

### Time

All datetimes tz-aware UTC in Mongo (`tz_aware=True` client, `_as_utc()` guard). The LLM prompt
states the call time in Asia/Kolkata and asks for `+05:30` offsets; the browser formats locally.
`DUE_WINDOW` is 2 hours.

### Error handling

`main.py` handlers: `AudioValidationError`/`ValueError` → 422, `TranscriptionError`/
`VertexUnavailable` → 503, `PyMongoError` → 503, bare `Exception` → 500 with a generic message.
Domain code raises those types; routes raise `HTTPException` only for 404/409. Never let a stack
trace into a response body.

### Route ordering

In `routes/leads.py`, `/follow-ups`, `/closed` (alias `/non-follow-ups`) are declared **before**
`/{lead_id}`. In `routes/calls.py`, `/upload`, `/validate-url`, `/from-url` precede `/{call_id}`.

### Frontend

`Dashboard.jsx` holds dashboard state; `CallAnalyzer.jsx` holds analyzer state and reports
completion via `onCompleted` so the dashboard refetches. The stepper (`ProcessingSteps.jsx`)
is driven by polling `GET /api/calls/{id}/status` *during* the synchronous `POST /process` — it
reflects real backend state. `AudioPlayer.jsx` renders nothing when
`config.audio_playback_enabled` is false. `api.js` uses a 5-minute timeout client for upload and
process, 15 s for everything else. Quick-filter chips `CONVERTED`/`DROPPED` switch to the closed
tab; the closed list ignores other bucket values so tab switches never show an empty table.
