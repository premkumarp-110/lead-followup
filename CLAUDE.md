# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AI-Powered Lead Follow-up Management System — an internal EdTech BD tool.

Leads and calls come from the **Lead Call API** (the CRM,
`https://lead-call-api.codingpuppet.com`, documented in `Lead-Call-API-Reference.pdf`). The CRM
already holds the recording, usually the transcript, and for ~37% of calls its own analysis. What
it never holds is a follow-up **datetime** — its "What follow-up action was locked in?" answer is
prose like *"call back tomorrow at 11 AM"*. Turning that into a scheduled follow-up is this
system's entire job.

So: analyse a call the CRM already captured → decide FOLLOW_UP_REQUIRED / CONVERTED / DROPPED →
extract a concrete follow-up datetime → drive the worklist and the daily reminder digest.
FastAPI + PyMongo backend, React 18 + Vite frontend, local MongoDB. No auth, no queues — 
deliberate MVP scope.

**There is no lead PII.** The CRM exposes no name, phone or email — verified live. A lead is its
`lead_id` (Superleap) plus `external_id` (the LeadSquared GUID), with product/stage/owner as
context. Do not add `name`/`phone`/`email` back to the model; there is no source for them.

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

MongoDB must be running. `ffprobe` is **no longer needed** — durations come from the CRM's
`durationSec`.

`backend/tools/harvest_recording_ids.py` refreshes `backend/fixtures/crm_recording_ids.json`,
the pool of real CRM `callId`s that seeded `has_recording` calls borrow as `crm_call_id` so audio
playback resolves. Seeding works without the fixture; only playback degrades.

### Tests

No pytest/vitest suite. `backend/check_analyzer.py` is the only automated check (14 cases) — run
it after touching `call_analyzer.py`. It covers the CRM transcript format and the language guard,
both of which exist because real data broke the analyzer.

## Configuration

`app/config.py` loads `backend/.env`. `MONGODB_URL`/`DATABASE_NAME` are required at startup.
Vertex **and** Lead Call API settings are **checked lazily**
(`settings.vertex_config_error()`, `settings.lead_call_config_error()`) so the app boots, seeds
and serves the whole dashboard on an unconfigured machine. Don't add a startup assertion for
either. An unset `LEAD_CALL_API_KEY` costs **only** recording playback — the worklist, leads,
insights and every seeded record keep working.

`LEAD_CALL_API_KEY` is a bearer credential to real customer conversations. It lives in
`backend/.env` only and **must never reach `UIConfig`**. `/api/config` exposes
`recording_source_configured` (a bool) so the player can explain itself, never the key.

The frontend never reads backend `.env`. Non-secret flags reach it through `GET /api/config`
(`UIConfig` in `schemas.py`). Adding a UI-relevant setting means adding it there.

## Architecture

### Pipeline order lives in exactly one file

`services/call_analysis_service.py::process_call()` is the orchestrator: it calls
`transcription_service.transcribe_audio()` → `llm_service.analyze_conversation()` →
`followup_service.apply_analysis_to_lead()`, and writes the call's `status` at every transition
(`PROCESSING → TRANSCRIBING → ANALYZING → COMPLETED` / `FAILED` + `failed_stage`). Routes stay
thin. Moving to a background worker later means calling `process_call()` from the worker.

Two shortcuts, both because the CRM did the work already:
- **Transcription is skipped when `has_transcript` is set** (~55% of analyzable calls). The
  existing `transcript_id` short-circuit does this, so a retry only repeats what failed.
- **`analysis_summary_parsed` is fed to the LLM when present** (~37%), narrowing the call to
  follow-up-datetime extraction rather than re-summarising.

A call with neither a transcript nor a recording is `NOT_ANALYZABLE` — a terminal state, not a
failure. That is ~30% of real calls (the not-connected / zero-duration rows). Never spend an LLM
call on one.

### `llm_service.py` is the LLM swap point

`analyze_conversation()` returns an `AnalysisRun` — a list of `AnalysisAttempt`s. The orchestrator
persists **every** attempt to `call_analyses` (failed ones carry `raw_response` + `error`) and
points `calls.analysis_id` at `run.final`. The Gemini call uses `response_mime_type=application/json`
then validates through `CallAnalysisResult`, with one repair retry that quotes the validation
error back. **There is no response schema** — the analysis endpoint is constrained only by
`response_format: {"type": "json_object"}` in `analysis_llm_client.py`, which is exactly why
`CallAnalysisResult`'s lenient validators carry the weight. Adding a field means editing the
prompt and the model, nothing else. `vertex_client.py` owns client construction and turns SDK
exceptions into user-safe wording (`describe_api_error`) — both Gemini callers go through it.

### Sentiment is part of the analysis, and it reorders the queue

`CallSentiment` on `CallAnalysisResult` carries `label` (POSITIVE/NEUTRAL/NEGATIVE/MIXED/
UNKNOWN), `score` (-1..1), `trajectory` (IMPROVED/STABLE/DECLINED) and a short verbatim
`evidence` quote. The CRM supplies none of it — its analysis rates the *agent* (pitch score,
violations); this rates the *lead*.

Three things about it are deliberate:

- **Sentiment and outcome are NOT forced to agree.** A polite, warm decline is genuinely
  POSITIVE tone with a DROPPED outcome. Making them consistent would destroy the signal.
- **`trajectory` outranks `label` in `sentiment_rank()`.** A call that ended worse than it
  started is the earliest sign a lead is going cold, even when the tone averaged to NEUTRAL.
- **The keyword fallback never reports sentiment** — always UNKNOWN with no score. Its phrase
  tables can spot a refusal; a tone *score* from keyword counts would be a guess dressed as a
  measurement, written to the lead and used to sort the queue.

`sort_worklist()` keys on `(bucket, sentiment_rank, datetime)` — urgency still dominates, but
within a bucket the worst-feeling lead leads and the date only breaks ties. That is why the
worklist no longer says "oldest first". `latest_sentiment` is denormalized onto the lead by
`apply_analysis_to_lead()` so the sort and the `?sentiment=` filter need no join.

### `CallAnalysisResult` is the trust boundary (models/analysis.py)

Raw model output never reaches the lead without passing this model. Strictness is deliberately
uneven: `outcome` is strict (it drives `lead_status`); `customer_intent` normalises unknown labels
to `UNCLEAR`; `confidence` accepts `94` as `0.94`. The `model_validator` makes outcome and
follow-up agree: CONVERTED/DROPPED force `follow_up=None`; FOLLOW_UP_REQUIRED with no datetime
gets `NO_DATE_REASON`. `AnalysisFollowUp` derives `date`/`time` from `datetime` in the *returned
offset* (IST), not UTC — the spec's example shape depends on that.

Two models have a field literally named `datetime` (`FollowUp`, `AnalysisFollowUp`); both use a
`DateTime = datetime` alias to avoid shadowing. Keep doing that.

**`Call.status` vs `Call.telephony_status` is a deliberate split.** The CRM's `status` is
telephony (`connected` / `not_connected` / `missed_call`); ours is the pipeline
(`PENDING → … → COMPLETED` / `FAILED` / `NOT_ANALYZABLE`). Unrelated axes, both needed.

`callers` is **synthesized** from the `owner_id/owner_name/owner_email` triples on **both leads
and calls** — the CRM has no /users endpoint, and a lead's owner routinely differs from whoever
made its calls. Build it from one source only and `caller_id` lookups 404. `role`, `team` and
`manager_name` are local mock metadata in the LeadSquared user shape; `salesOwner*` is null on
every real lead.

### Stage is free text; `lead_status` is an enum

`services/stage_mapping.py` maps the CRM's ~30 free-text stages onto the 6-value `LeadStatus`.
A raw stage must never reach the enum — it raises `ValidationError` and rejects a real record.
Unknown stages degrade to `NEW` with one logged warning. The raw `stage` is always stored too,
because the **numbered ladders** (`DNP 1..5`, `Follow-up 1..2`) encode attempt count: `DNP 5` is
a very different lead from `DNP 1`, and `ladder_step()` is the only place that signal survives.

### Fallback when Gemini fails

If Vertex is unreachable/misconfigured or JSON fails validation after the retry, and
`ANALYSIS_FALLBACK_ENABLED` (default true), `call_analyzer.py` (deterministic keywords) produces
the result flagged `degraded=True, model="keyword-fallback-v1"`. Rule order DROPPED → CONVERTED →
FOLLOW_UP is load-bearing. It **never invents a date**: no extractable date → `datetime=None`.

Two guards exist because real transcripts broke it:
- **`is_analyzable_language()`** — the phrase tables are English. Real transcripts are frequently
  romanized Tamil/Malayalam ("ippo vendaam, naan paarkala"), which matches nothing and would
  return the *default* outcome as though it were a finding, straight onto the lead. The fallback
  now refuses instead, and `llm_service` raises. A visible failure beats a confident lie.
- **`strip_transcript_markup()`** — transcripts are `[MM:SS] Agent:` lines. The `[MM:SS]` is an
  offset into the recording, not a clock time, but it looks identical to the time regex and
  always appears first. Left in, a call agreed for "8 PM" gets scheduled at 01:20. Strip before
  any date/time extraction.

To exercise the pipeline offline: `python seed.py` already writes transcripts, then
`POST /api/calls/{id}/analyze` on a seeded call — transcription is skipped (the transcript
exists), the analysis LLM fails on config, the fallback runs, and both attempts are persisted.

Transcription has **no** fallback; only `vertex` is implemented in `transcription_service.py`.
`local`/`gcp-stt` are registered so they fail with a specific message (spec §8).

### Stored vs. derived follow-up state

**`COMPLETE` and `CANCEL` are both retired — `RESCHEDULE` is the only action a BD can take.**
`FollowUpActionRequest` refuses the other two with a 422 and no UI offers them. A BD must not
close a follow-up by asserting it is handled: that empties the queue without anything having
happened. Closure belongs to the call.

So a follow-up now leaves the worklist **exactly one way**: a newer call for that lead is
analysed and yields CONVERTED/DROPPED (clearing `follow_up`) or a fresh FOLLOW_UP_REQUIRED with
a new datetime. `apply_analysis_to_lead()`'s `is_latest_call` guard already orders that.

Both enum values survive on purpose: `follow_up_history` rows written before the change carry
`action: "COMPLETE"` / `"CANCEL"`, and deleting them would 500 the detail endpoint for exactly
the leads with the most history. `RETIRED_ACTIONS` in `models/lead.py` is the list.
`FollowUpStatus.COMPLETED` / `CANCELLED` and their buckets stay for the same reason — on new
data `follow_up.status` only ever holds `PENDING`, so the `follow_up.status` half of
`CLOSED_QUERY` matches historical rows only.

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

The filter set mirrors the CRM's own documented lead filters, because every CRM field is stored
and **filtering is driven from that data alone**: `stage`, `product`, `language`, `segmentation`,
`city/state/country`, `lead_source`, dispositions, `attempts_*`/`connected_*`/`win_probability_*`
ranges, and the CRM date ranges. List filters accept comma-separated "any of these".

`bd` matches **`owner_email` or `owner_id`, never `owner_name`** — against the real CRM there are
100+ distinct owners, first names collide, and emails are unique. `search` matches
`lead_id`/`external_id` only; there is no name, phone or email to search.

⚠️ **Seeded data deliberately gives every BDA the same mailbox** (`SEED_CALLER_EMAIL` in
`seed_data.py`), because the reminder digest emails a BD at their own address and the CRM's real
owners are live mailboxes. `seed.py` asserts it. Consequence: `?bd=<email>` matches every seeded
lead, so filter on `caller_id` — which the UI already does everywhere. Never seed a real address.

### The CRM client

`services/lead_call_client.py` owns all CRM I/O: one pooled `httpx.Client`, a shared token bucket
at **6 rps** (the documented limit is 60 requests / 10 s per key), `Retry-After` honoured on 429,
and the CRM's `errorCode` mapped onto `main.py`'s handler taxonomy (`INVALID_API_KEY` → operator
error not a 500; `CRM_UNAVAILABLE` → 503; `*_NOT_AVAILABLE` → skip, don't fail).
`from_epoch_ms()` is the single conversion point — every CRM timestamp is **milliseconds**, and a
seconds mix-up silently puts records in 1970.

### Denormalization and the latest-call guard

`call_analyses` is the source of truth; `leads.follow_up` is a denormalized copy of the winning
analysis. `apply_analysis_to_lead()` only updates the lead when no **COMPLETED** call for that lead
ended later (`is_latest_call`). Unprocessed/failed calls don't count — they have no outcome to
protect. Replaying an older recording therefore can't overwrite a newer outcome, but an abandoned
upload can't block a real one either.

### Audio

There is **no upload path**. `audio_service.py` fetches recordings from the CRM and caches them.

`GET /api/calls/{id}/audio` must **proxy, never redirect**: a browser cannot attach `X-API-Key`
to an `<audio src>`, so redirecting upstream returns 401 (measured, not theoretical). Upstream
also sends no `content-length` and ignores `Range`, so a live passthrough gives the player no
duration and no seeking. The first request buffers to disk under `AUDIO_CACHE_DIR`; every later
one serves the cached file. Recordings are immutable, so the cache never expires.

Cached filenames derive from our own `call_id`; `resolve_cached_path` refuses anything outside the
cache dir, and `crm_call_id` is `exclude=True` so it never reaches the browser. Writes go to a
`.partial` file first — an interrupted fetch must not leave a truncated file that later reads
happily serve as complete.

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
`/{lead_id}`. `routes/calls.py` has only `/{call_id}` sub-paths now, but keep any literal path
ahead of it.

### Frontend

Three views only: **Worklist · Leads · Insights**. `AppShell.jsx` holds `view` in local state
(no router) and `safeView` falls back to `worklist`, so a stale persisted view self-heals.

There is **no "Analyze Call" view and no "System" view**. Analysis is an action on a call, in
`LeadDetailsModal.jsx` — each call card offers Analyse/Re-analyse only when `has_transcript` or
`has_recording` is set, and says so plainly when neither is. The modal also surfaces the CRM's
own `pitch_score`, `violations` and `improvement_tips`.

`BDSelector.jsx` is a hand-rolled searchable combobox (no dependency — the project has no UI
libraries). It matches on **name and email**, shows the email as a secondary line, and is used by
both the topbar and `FilterBar`. Its value is `caller_id`; the backend's `bd` filter takes either
that or the email.

`AudioPlayer.jsx` renders nothing when `config.audio_playback_enabled` is false, and explains
itself when `recording_source_configured` is false rather than failing silently. `api.js` uses a
5-minute client for analyse and digest sends, 15 s for everything else. Quick-filter chips
`CONVERTED`/`DROPPED` switch to the closed tab.

⚠️ `.truncate` sets `display: block`. On a `<td>` that removes the cell from the table formatting
context and the column breaks out of its row. It must live on an inner `<div>` — same trap as
`.clamp-2`.
