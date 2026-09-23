# AI-Powered Lead Follow-up Management System

An internal tool for an EdTech BD team, built on top of the **Lead Call API** — the CRM that
holds the team's leads, call logs, recordings and transcripts
(`https://lead-call-api.codingpuppet.com`, documented in `Lead-Call-API-Reference.pdf`).

### Why this exists

The CRM already does a lot. Measured against the live API, of the calls worth analysing:

- **~55%** already ship a transcript,
- **~37%** already carry the CRM's own analysis — a summary, a pitch score, violations, coaching
  tips and a fixed ten-question findings block.

What the CRM does **not** produce is a follow-up **datetime**. Its own answer to *"What follow-up
action was locked in?"* is prose:

> `"Google Meet tomorrow at 11 AM and WhatsApp details"`
> `"Send resume via WhatsApp and callback at 8 PM"`
> `"Call back within 30 minutes and share course details"`

That is a commitment nobody scheduled. This system reads the call, decides the outcome
(`FOLLOW_UP_REQUIRED` / `CONVERTED` / `DROPPED`), extracts a **concrete datetime** from that
prose, and turns it into a worklist and a daily reminder digest.

So the pipeline is:

1. read the call and its transcript (transcribing only when the CRM has no transcript),
2. analyse it — reusing the CRM's own analysis as context when it exists,
3. validate the structured result,
4. extract the follow-up datetime, or record honestly that none was stated,
5. update the lead, the worklist and the reminder queue.

Converted and dropped leads leave the active worklist automatically. Follow-ups are bucketed as
Overdue / Due / Upcoming / Unscheduled on every read, so the "who do I call next?" list is never
stale.

### There is no lead PII

The CRM exposes **no name, no phone and no email** — verified live against the API. A lead is its
`lead_id` (Superleap) plus `external_id` (the LeadSquared GUID), with product, stage and owner as
context. The lead table, the search box, the worklist and the email digest are all keyed that
way. There is nothing to add them back from.

Three other fields are null on **every** lead upstream and are stored but never invented:
`conversion_date`, `sales_qualified`, `sales_owner_id` / `sales_owner_name`. Conversion is
derived from `stage == "Converted"`, never from the date field.

> **Scope:** no authentication, no WhatsApp/SMS, no queues, no vector DB, no write-back to the
> CRM. Reads from the CRM are recordings only; leads and calls are seeded locally.

---

## 1. Stack

| | |
|---|---|
| Frontend | React 18 + Vite, plain CSS, axios — talks **only** to the FastAPI backend |
| Backend | Python 3.11, FastAPI, Pydantic v2, PyMongo |
| Database | MongoDB (local or `docker compose`) |
| AI | Vertex AI Gemini via the Google Gen AI Python SDK (`google-genai`) |
| Data source | Lead Call API (the CRM) — read-only, `X-API-Key` |
| Audio | Recordings proxied from the CRM and cached on disk. Duration comes from the CRM, so no ffprobe |

---

## 2. Architecture

```
 React dashboard (:5173)
   └─ axios ─► FastAPI (:8000)
                 routes/calls.py ─────► services/audio_service.py          CRM fetch · disk cache
                                            └─► lead_call_client.py         CRM I/O · 6 rps · epoch-ms
                                  ─────► services/call_analysis_service.py  ORCHESTRATOR
                                            ├─► transcription_service.py    audio → text   (only if no CRM transcript)
                                            ├─► llm_service.py              text → decision
                                            │     └─► call_analyzer.py      deterministic fallback
                                            ├─► stage_mapping.py            CRM stage → LeadStatus
                                            └─► followup_service.py         decision → lead
                 routes/leads.py, dashboard.py, callers.py, alerts.py, insights.py
                 └─ PyMongo ─► leads · callers · calls · call_transcripts · call_analyses · followup_alerts
```

Each arrow is a module boundary. `call_analysis_service.py` is the only file that knows the
order of the steps; moving processing to a background worker later means calling
`process_call()` from the worker instead of the request handler. Swapping the LLM touches
`llm_service.py` only.

### Collections

| Collection | Purpose |
|---|---|
| `leads` | All 35 CRM lead fields verbatim + our derived `lead_status`, `follow_up` block and `follow_up_history` |
| `callers` | The BDA directory, **synthesized** from the `owner_id/owner_name/owner_email` triples on both leads *and* calls (the CRM has no /users endpoint, and a lead's owner often differs from whoever called) |
| `calls` | All CRM call fields + our pipeline `status`, `transcript_id`, `analysis_id` |
| `call_transcripts` | Transcript text, language, and the CRM's `source` stored verbatim |
| `call_analyses` | Every analysis attempt: outcome, follow-up, intent, summary, key points, confidence, `raw_response`, `status`, `degraded` |
| `followup_alerts` | One row per reminder digest attempted, for the once-a-day dedupe and delivery history |

**Two status fields, deliberately.** The CRM's `status` is *telephony* — `connected`,
`not_connected`, `missed_call` — and lands on `telephony_status`. Ours is the *pipeline*:

`PENDING → PROCESSING → TRANSCRIBING → ANALYZING → COMPLETED` or `FAILED` (with `error` and
`failed_stage`), plus **`NOT_ANALYZABLE`** for a call with neither a transcript nor a recording.
That last one is ~30% of real calls — the not-connected, zero-duration rows. It is a finished
state, not a backlog item, and no LLM call is ever spent on one.

**Stage is free text.** The CRM has ~30 stage values; `LeadStatus` has six.
`services/stage_mapping.py` maps between them and degrades an unknown stage to `NEW` with one
logged warning rather than rejecting a real record. The raw stage is always stored too, because
the numbered ladders (`DNP 1..5`, `Follow-up 1..2`) encode attempt count — a `DNP 5` lead is a
different proposition from a `DNP 1`.

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

Transcription has no equivalent fallback — without Vertex, a recording cannot become text and the
call fails at the `TRANSCRIBING` step with a message naming the missing setting. In practice this
bites rarely: ~55% of analyzable calls arrive from the CRM with a transcript already, and that
step is skipped entirely.

**Two guards on the fallback, both added because real data broke it.**

*Language.* The keyword tables are English. Real transcripts are frequently romanized
Tamil/Malayalam mixed with English — `"ippo vendaam, naan paarkala"` ("not now, I haven't looked
at it") matches no phrase and no date pattern. The analyzer would fall through to its *default*
outcome and `apply_analysis_to_lead` would write it to the lead as though it were a finding. So
`is_analyzable_language()` refuses instead, and the request returns 503 with the reason. A
visible failure is recoverable; a confidently wrong outcome silently written to a lead is not.

*Transcript markup.* Transcripts are `[MM:SS] Agent:` / `Customer:` lines. The `[MM:SS]` is an
offset into the recording, but it is indistinguishable from `HH:MM` to a time regex and always
appears before anything the speaker said — so a call agreed for "8 PM" got scheduled at 01:20.
`strip_transcript_markup()` removes offsets and speaker labels before any extraction. Both cases
are locked in by `check_analyzer.py`.

---

## 3. Prerequisites

- Python 3.11+, Node.js 18+, MongoDB running locally
- A Google Cloud project with **Vertex AI API** enabled and credentials with the
  **Vertex AI User** role (see §5) — only needed to transcribe calls the CRM has no transcript for
- A Lead Call API key (`lca_…`) — only needed to play call recordings

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
| `MONGODB_URL` | — (required) | Mongo connection string |
| `MONGODB_TIMEOUT_MS` | `15000` | Server-selection timeout. A local mongod answers instantly; a hosted `mongodb+srv://` cluster needs a TLS handshake to several hosts and can take 6-12s. Too low and startup fails with `ServerSelectionTimeoutError` even though the cluster is fine |
| `DATABASE_NAME` | — (required) | Database name |
| `GOOGLE_CLOUD_PROJECT` | blank | GCP project with Vertex AI enabled |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` | Vertex region or `global` |
| `VERTEX_AI_MODEL` | `gemini-2.5-flash` | Gemini model id |
| `GOOGLE_APPLICATION_CREDENTIALS` | blank (ADC) | Path to a service-account key |
| `TRANSCRIPTION_PROVIDER` | `vertex` | `vertex` implemented; `local`, `gcp-stt` raise a clear not-configured error |
| `ANALYSIS_FALLBACK_ENABLED` | `true` | Keyword fallback when Gemini fails |
| `LEAD_CALL_API_URL` | `https://lead-call-api.codingpuppet.com` | The CRM base URL |
| `LEAD_CALL_API_KEY` | _(blank)_ | CRM API key (`lca_…`). **A credential to real customer conversations** — server-side only, never returned by `/api/config`. Checked lazily: without it everything works except recording playback |
| `AUDIO_PLAYBACK_ENABLED` | `true` | Show the audio player in the modal |
| `AUDIO_CACHE_DIR` | `uploads` | Where CRM recordings are cached after first fetch (relative to `backend/`) |
| `CORS_ORIGINS` | localhost:5173 | Allowed browser origins |

The frontend has one variable: `VITE_API_BASE_URL=http://localhost:8000`. No credential ever
reaches the browser; the UI reads non-secret flags from `GET /api/config`.

---

## 6. Seed sample data

```bash
cd backend && python seed.py
```

Drops and recreates every collection, then inserts **60 leads, 12 callers, 40 calls,
21 transcripts and 21 analyses**. No API calls and no LLM calls, so reseeding is instant, free
and works offline. Analyses are pre-baked (`model: "seed"`).

**The records are mock; the shape and the distributions are real.** Every field the CRM returns
is stored, and the stage / product / source / language mixes follow frequencies measured against
the live API — `Follow-up 1` is the most common stage on call-active leads, Facebook is ~77% of
sources, Tamil/English/Hindi dominate. `seed_data.py` holds those vocabularies so the ratios stay
auditable.

### One deliberate departure from real data: the BDA mailbox

Every seeded BDA is addressed to **one real mailbox**, set by `SEED_CALLER_EMAIL` in
`seed_data.py`.

The reminder digest emails a BD at their own address, and `FOLLOWUP_ALERTS_ENABLED` with a
working SMTP account will send on a schedule. The CRM's real owners are live `@hclguvi.com`
mailboxes — seeding those points a working scheduler at actual colleagues. Names stay realistic;
the address does not. `seed.py` asserts it, so a stray address fails the seed rather than
shipping.

The trade-off: an address no longer identifies a BD, so `?bd=<email>` matches every seeded lead.
The UI filters on `caller_id` throughout and is unaffected, and the BD dropdown hides the email
line when it distinguishes nothing. Against the real CRM, where owner emails are unique, both
behave as designed.

### Three properties of real data, reproduced deliberately

- **No name, phone or email**, because the CRM has none.
- **`conversion_date`, `sales_qualified` and `sales_owner_*` are null on every lead**, because
  they are null on every real lead. Nothing is invented to fill a column that is empty upstream.
- **Transcript implies recording.** There is no call with a transcript but no audio, and the
  not-connected / zero-duration calls have neither.

Follow-up times are relative to now, so every bucket is always populated:

| Segment | Leads |
|---|---|
| Overdue | 5 |
| Due today | 3 |
| Upcoming | 4 |
| Unscheduled (follow-up agreed, no date stated) | 3 |
| Converted | 3 |
| Dropped | 3 |
| Contacted, call not analysable | 12 |
| Recording but no transcript yet — **the Analyse demo target** | 7 |
| Never called | 20 |

The seed asserts its own invariants and prints them; a violated one fails the run rather than
producing quietly wrong data.

### Audio for seeded calls

A call we invent has a `callId` the CRM has never heard of, so `/recording/{callId}` would 404.
Seeded calls with `has_recording` therefore borrow a **real** CRM `callId` into a separate
`crm_call_id` field, used only to resolve audio — everything else about the call is mock. The
pool lives in `backend/fixtures/crm_recording_ids.json`; refresh it with:

```bash
python tools/harvest_recording_ids.py    # needs LEAD_CALL_API_KEY
```

Seeding works without the fixture. Only playback degrades, and it degrades to a message.

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

The UI is a three-view shell — **My Worklist · Leads · Insights**. The **BD selector** and the
**reminder bell** sit in the top bar and apply everywhere.

The BD selector is a **searchable combobox**, not a dropdown: the real CRM has 100+ distinct
owners and duplicate first names are common, so it matches on name *and* email and shows the
email as a secondary line. Typing filters; arrows and Enter work; the selection persists in
`localStorage`. The same control backs the BD filter in the Leads view.

> The BD selector is a convenience for scoping the worklist and reminders. **It is not access
> control** — there is no auth in this MVP, and every BD's data stays readable through the API
> regardless of what is selected.

### My Worklist — the BD's queue
Who to call next, in order: **Overdue** (oldest first, with how late), **Due today** (earliest
first), then **Needs a date**. Every row carries the follow-up reason from the last analysis, the
lead's CRM stage, its connected/attempted call counts, and inline
**Done / Reschedule / Cancel** — the row leaves the queue as you work, without the list jumping.
An empty section hides instead of showing a zero.

There is no `tel:` link: the CRM exposes no phone number. The stage chip earns its place instead
— a `DNP 5` lead has been chased five times and is highlighted accordingly.

### Leads — the full table
Summary cards (Total · Pending · Overdue · Due Today · Upcoming · No Date · Converted · Dropped)
double as quick filters.

Because every CRM field is stored, filtering runs off that data alone: **search** (lead id or
external id), **BD**, **product**, **stage**, **language**, **source**, **state**,
**disposition**, **min connected calls**, **min win %**, plus outcome, follow-up status and date
ranges. Everything combines with AND, and whatever is active shows as a **removable chip** with a
result count. Two tabs (*Requiring Follow-up* / *Completed / Closed*) and **Export CSV** of the
rows currently shown.

The table is keyed on `lead_id` with `external_id` beneath it, then product, stage, BD, a
connected/attempts count and the follow-up state. Sentinel products (`common`, `do-not-know`,
`career_consultation`) are the CRM's "nothing recorded" markers and are shown as `—` rather than
offered as courses.

Click any lead for the details modal: every CRM field, call activity, each call with its own
analyse action and audio, the analysis, follow-up state, transcript and action history.

### Analysing a call

There is no "Analyze Call" screen. The recording and usually the transcript already exist on the
call, so analysis is an **action on a call**, in the lead detail modal.

Open any lead and each of its calls shows what is known about it — direction, duration, telephony
status, who made it, and the CRM's own pitch score, violations and coaching tips where it has
them — plus two flags: `transcript` and `recording`.

- With a transcript or a recording, the card offers **Analyse** (or **Re-analyse**).
- With neither, it says *"Nothing to analyse — no transcript and no recording."* and offers no
  button. That is ~30% of real calls and it is an honest state, not a gap.

Analysing runs the same pipeline as always, minus the work the CRM already did: transcription is
skipped when a transcript exists, and the CRM's own analysis is passed to the model as context so
the call narrows to what actually matters — the outcome and a concrete follow-up datetime.

Re-analysing is safe. A stored transcript is reused, so a retry only repeats what failed, and the
`is_latest_call` guard means re-analysing an older call cannot overwrite a newer outcome.

### Sentiment

The analysis judges **how the lead sounded**, not just what was said — because two leads can
both say "call me next week" and only tone separates the one warming up from the one brushing
the BD off.

| Field | |
|---|---|
| `label` | `POSITIVE` · `NEUTRAL` · `NEGATIVE` · `MIXED` · `UNKNOWN` |
| `score` | −1.0 (hostile) to +1.0 (enthusiastic); absent when the label is `UNKNOWN` |
| `trajectory` | `IMPROVED` · `STABLE` · `DECLINED` — how the lead sounded early versus at the end |
| `evidence` | one short verbatim quote, in whatever language it was said |

**`trajectory` is the part that earns its place.** A call that ended worse than it started is
the earliest sign a lead is going cold, and a single averaged label cannot express it — which
is why a `DECLINED` lead sorts to the top of its section regardless of its label.

The prompt is told to judge the *customer*, to read romanized Indic tone rather than treating
unfamiliar words as neutral (`vendaam`, `enakku interest illa` are refusals; `sari`, `theek hai`
are assent), and that sentiment is **evidence for** the outcome, never a substitute for it — an
enthusiastic tone alongside an explicit refusal is still `DROPPED`.

> **The keyword fallback never reports sentiment.** It always returns `UNKNOWN` with no score.
> Its phrase tables can recognise an explicit refusal, but a tone *score* derived from counting
> keywords would be a guess dressed as a measurement — and it would be written to the lead and
> used to order the queue. The UI says tone was not assessed, and why.

### Insights — queue health
Overdue **ageing** (`< 1 day` / `1-3` / `4-6` / `7+`, exclusive bands summing to the overdue
total), **outcome mix** with denominators, **workload by BD** (sortable, CSV-exportable, with
`Unassigned` and orphaned rows kept visible), and reminder delivery history.

> **No trends are shown.** Nothing records the daily size of the queue — `follow_up_history`
> stores actions taken, not backlog over time — so week-over-week change cannot be computed
> honestly. A nightly snapshot collection would make it possible.

---

## 8a. Follow-up reminders

Each BD gets one email listing what they owe. Off by default.

**Coverage** — **overdue** plus **due today**. "Due today" means anything inside the 2-hour `DUE`
window *plus* anything else falling on today's **Asia/Kolkata** date. An imminent follow-up is
never dropped just because the clock is about to cross IST midnight. Leads that need a follow-up
with no date set are counted in a footer line rather than listed — they have no deadline to miss,
but they must not vanish either.

**Schedule** — one cron job at `FOLLOWUP_ALERT_HOUR:FOLLOWUP_ALERT_MINUTE` Asia/Kolkata.
`misfire_grace_time` is 60 minutes, so a run missed by a restart or a busy host still fires.
(APScheduler's default is *one second*, which silently drops the day's digest.)

> ⚠️ **Run a single worker.** The scheduler is in-process, so `uvicorn --workers N` starts N
> schedulers and each would send its own digest. The once-a-day dedupe below makes that mostly
> harmless, but it is a race, not a design. A proper fix needs a shared lock in MongoDB.

**Delivery log and dedupe** — every attempt writes a `followup_alerts` document (`SENT`,
`FAILED`, `SKIPPED_NO_EMAIL`, `SKIPPED_NO_RECIPIENT`, `SKIPPED_DUPLICATE`). A **scheduled** run
skips a BD already sent to for that IST date. **Manual** sends always go through — a "Send
digest" button that silently does nothing is worse than a duplicate email.

| Method | Endpoint | Gated |
|---|---|---|
| GET | `/api/alerts/pending` | No — read |
| GET | `/api/alerts/preview` | No — renders the digest HTML, sends nothing, needs no SMTP |
| POST | `/api/alerts/send` | **Yes** — `403` when disabled, `503` when SMTP is unconfigured |
| GET | `/api/alerts/history` | No — read |

**SMTP setup** — set `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` (Gmail needs an *app password*,
not the account password) and `FOLLOWUP_ALERTS_ENABLED=true`, then restart: the flag is read at
startup only, so an edit without a restart leaves the scheduler enabled-but-not-running.

```bash
# Safe: renders the exact HTML, sends nothing, needs no SMTP at all
curl "http://localhost:8000/api/alerts/preview?bd=<caller_id>"
curl "http://localhost:8000/api/alerts/preview?lead=<lead_id>"

# Sends for real
curl -X POST http://localhost:8000/api/alerts/send -H 'Content-Type: application/json' \
     -d '{"lead_id":"<lead_id>"}'     # one lead, to its owner
curl -X POST http://localhost:8000/api/alerts/send -H 'Content-Type: application/json' \
     -d '{"bd_id":"<caller_id>"}'     # one BD's overdue + due-today digest
curl -X POST http://localhost:8000/api/alerts/send -H 'Content-Type: application/json' -d '{}'
```

### Two kinds of reminder

|  | Daily digest | Single-lead reminder |
|---|---|---|
| Trigger | The cron, or `{}` / `{"bd_id"}` | `{"lead_id"}`, or **Remind** in the UI |
| Scope | Everything that BD owes | The one lead you asked about |
| Includes | Overdue + due-today only | **Any** pending follow-up, including upcoming and *no date set* |
| Deduped | Scheduled runs, once per BD per IST day | Never — you asked for it |
| Logged as | `trigger: "scheduled"` / `"manual"` | `trigger: "lead"` |

> Every seeded BDA is addressed to the one mailbox in `SEED_CALLER_EMAIL` (§6), so testing
> delivery cannot reach a real colleague. Every attempt — sent, failed or skipped — is written to
> `followup_alerts` and shown in the reminder panel.

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
| PATCH | `/api/leads/{id}/follow-up` | `RESCHEDULE` only (reason required). `COMPLETE` and `CANCEL` are retired and return 422 |
| GET | `/api/callers` | BD list for the dropdown |
| POST | `/api/calls/{id}/analyze` | Analyse one existing call → update the lead. Idempotent; `409` when there is nothing to analyse |
| GET | `/api/calls` · `/{id}` · `/transcript` · `/analysis` · `/analyses` | Call reads (`/analyses` includes failed attempts) |
| GET | `/api/calls/{id}/audio` | Streams the recording, fetched from the CRM on first request and cached. Never a redirect — a browser cannot send `X-API-Key` |
| GET | `/api/dashboard/summary` · `/filters` | Card counts; dropdown options built from stored values |
| GET | `/api/alerts/pending` · `/preview` · `/history` | Reminder reads (never gated) |
| POST | `/api/alerts/send` | Send now. `{"lead_id"}` one lead · `{"bd_id"}` one BD's digest · `{}` everyone. `403` disabled, `503` no SMTP, `409` nothing to remind about |
| GET | `/api/alerts/preview?bd=` · `?lead=` | The exact HTML that would be sent, without sending. Needs no SMTP |
| GET | `/api/insights/overview` | Ageing bands, outcome mix, unscheduled/unassigned, degraded analyses |
| GET | `/api/insights/by-bd` | Per-BD workload, incl. an `Unassigned` row |
| GET | `/api/config` · `/api/health` | Non-secret UI flags; liveness + `vertex_configured` |

Filter params on the list endpoints and the summary mirror the CRM's own filter vocabulary:

- **identity** — `search` (lead id / external id)
- **ownership** — `bd` (owner email or owner id)
- **attributes** — `product`, `stage`, `previous_stage`, `language`, `segmentation`,
  `city`, `state`, `country`
- **source** — `lead_source`, `source_campaign`, `source_medium`
- **call activity** — `last_disposition_status`, `last_sub_disposition_status`,
  `attempts_min/max`, `connected_min/max`, `win_probability_min/max`
- **sentiment** — `sentiment` (`POSITIVE|NEUTRAL|NEGATIVE|MIXED|UNKNOWN`)
- **CRM dates** — `created_from/to`, `updated_from/to`, `first_contact_from/to`,
  `last_call_from/to`
- **ours** — `outcome`, `status`, `date`, `date_from`, `date_to`,
  `bucket=ALL|OVERDUE|DUE|DUE_TODAY|UPCOMING|UNSCHEDULED|COMPLETED|CANCELLED|CONVERTED|DROPPED`

List filters accept comma-separated values, meaning "any of these".

```bash
# One BD's overdue leads. `bd` takes an owner id or an owner email; use the id,
# because every seeded BDA shares one mailbox (see §6).
curl "http://localhost:8000/api/leads/follow-ups?bd=WN5qOX_XXXXXXXX&bucket=OVERDUE"

# Leads on the follow-up ladder who have actually been reached
curl "http://localhost:8000/api/leads?stage=Follow-up%201,Follow-up%202&connected_min=1"

# Analyse a call and apply the outcome
curl -X POST http://localhost:8000/api/calls/CALL-XXXXXXXX/analyze
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
cd backend && python check_analyzer.py     # 14 cases, no network
```

Covers the spec's five sample transcripts (follow-up with date, converted, dropped, follow-up
without date, payment follow-up) plus edge cases, against the fallback analyzer — and two things
real CRM data broke:

- **`[MM:SS]` transcript markup.** An offset into the recording is not a clock time, but it looks
  like one and comes first on the line. Without stripping it, "call me back at 8 PM" was
  scheduled at 01:20.
- **The language guard.** Romanized Tamil/Malayalam must be *refused* by the keyword analyzer,
  not silently given the default outcome.

---

## Project structure

```
backend/
  app/
    main.py                       app, CORS, error handlers, /api/health, /api/config
    config.py                     .env settings; vertex_config_error(), lead_call_config_error()
    database.py                   MongoClient, collection names, indexes
    models/   lead.py · caller.py · call.py · analysis.py · schemas.py
    routes/   leads.py · calls.py · callers.py · dashboard.py · filters.py
              alerts.py · insights.py
    scheduler.py                  in-process cron for the daily digest (single worker only)
    services/
      lead_call_client.py         CRM I/O: pooled client, 6 rps bucket, Retry-After, epoch-ms
      audio_service.py            CRM recording fetch + disk cache + path guard
      stage_mapping.py            CRM free-text stage -> LeadStatus, ladder step
      transcription_service.py    provider registry; VertexGeminiTranscriber
      llm_service.py              Gemini prompt + validation + repair retry + fallback
      call_analyzer.py            deterministic keyword fallback
      call_analysis_service.py    pipeline orchestrator + status transitions
      followup_service.py         buckets, lead projection, BD actions, IST helpers
      followup_alert_service.py   reminder grouping, digest HTML, delivery log + dedupe
      email_service.py            shared SMTP client
      vertex_client.py            shared google-genai client + error wording
  seed.py · seed_data.py          seeder + the measured CRM vocabularies it draws from
  fixtures/crm_recording_ids.json real CRM callIds, so seeded audio resolves
  tools/harvest_recording_ids.py  refreshes that fixture
  check_analyzer.py · requirements.txt · Dockerfile · .env.example · uploads/  (audio cache)
frontend/
  src/components/  AppShell · BDSelector · ReminderBell            (shell; BDSelector is a
                                                                   searchable combobox, no dep)
                   WorklistView · WorklistRow                     (My Worklist)
                   LeadsView · LeadTable · FilterBar · SummaryCards · LeadDetailsModal ·
                   FollowUpActionModal · AudioPlayer              (Leads; analyse lives in the
                                                                   detail modal, per call)
                   InsightsView                                   (Insights)
                   format.js                                      (shared formatting)
  src/services/  api.js · csv.js · storage.js
  App.jsx · main.jsx · index.css · Dockerfile · .env.example
docker-compose.yml · .env.example · .gitignore · CLAUDE.md · MODULES.md
```
