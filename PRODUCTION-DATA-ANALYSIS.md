# Production Data Analysis — Lead Call API vs. the current sample model

**Source API:** `https://lead-call-api.codingpuppet.com` (read-only, `X-API-Key`)
**Measured:** 2026-09-22, live, read-only. See *Appendix A* for sample sizes and caveats.
**Status:** analysis only. No code in this repo has been changed.

`Lead-Call-API-Reference.pdf` (repo root) documents the API. This document compares what it
actually serves against the sample model in `backend/seed.py` (15 leads, 5 BDs, 17 calls) and
sets out what must change, what can be improved, and what can be optimized.

---

## 1. Executive summary

The system was built against a sample set that is not merely *smaller* than production — it is
**shaped differently in ways that break the current model outright**. Five findings dominate
everything else:

| # | Finding | Consequence |
|---|---|---|
| 1 | **Production leads have no name, phone or email.** The API exposes none of these fields. | The `Lead` model requires all three. The search box, the lead table's primary column, and the whole notion of "call the lead" have no data source. |
| 2 | **Scale is ~4 orders of magnitude larger.** Offset 400,000 still returns `hasMore: true`. | Every list endpoint loads the full matching set into Python (`list(db[LEADS].find(...))`). At 400k leads this does not degrade — it falls over. |
| 3 | **The CRM already analyzed 37% of calls.** `analysisSummary` is a full JSON analysis with summary, pitch score, violations and improvement tips. | Re-running our own LLM on those calls is paid, slower duplicate work. |
| 4 | **55% of calls already have a transcript.** Every call that has a transcript also has a recording. | Transcription — the most expensive step in the pipeline — is avoidable on the majority of analyzable calls. |
| 5 | **Transcripts are romanized Tamil/Malayalam, not English.** | `call_analyzer.py`, the deterministic keyword fallback, cannot match a single phrase. It will silently mislabel every degraded call. |

The good news: the **architecture holds**. `process_call()` as the single orchestrator,
`CallAnalysisResult` as the trust boundary, derived-not-stored buckets, and the two-phase filter
are all still the right decisions. What changes is the *data source* and the *scale
assumptions*, not the shape of the pipeline.

---

## 2. What production data actually looks like

### 2.1 Volume

| Measure | Sample data | Production (measured) |
|---|---:|---:|
| Leads | 15 | **≥ 400,500** (offset 400k still `hasMore: true`; true total unknown) |
| Leads with ≥1 call attempt | 17 calls / 14 leads | **≥ 20,000** (paging capped at 40 pages) |
| Leads with ≥1 *connected* call | 14 | **≥ 20,000** |
| Distinct BD owners | 5 | **80 in a single 500-lead sample** |
| Distinct lead stages | 6 (enum) | **30+ free-text values** |
| Distinct products | 6 | **40+ free-text values** |

**`withTotal: true` returns `total: null`.** The CRM does not supply exact counts. Any UI that
promises "1,234 leads" cannot be honestly built — paging must be driven by `hasMore`.

### 2.2 Only ~5% of leads are worth touching

From a 500-lead sample:

- `totalAttempts > 0` — **38 leads (7.6%)**
- `callsConnected > 0` — **26 leads (5.2%)**
- `totalTalktimeSec > 0` — **26 leads (5.2%)**

**95% of production leads have never been called.** They carry no call, no transcript, no
outcome, and nothing for this product to analyze. This single fact is the largest available
optimization — see §5.1.

### 2.3 Field population is sparse and *correlated with call activity*

Null rates across 500 leads:

| Field | Null | Field | Null |
|---|---:|---|---:|
| `conversionDate` | **100.0%** | `city` | 79.6% |
| `nurturing` | 99.2% | `segmentation` | 76.8% |
| `salesQualified` | 99.0% | `state` | 74.8% |
| `winProbability` | 97.8% | `language` | 65.6% |
| `salesOwnerId/Name` | 96.8% | `lastSource/lastMedium` | 64.8% |
| `lastDispositionStatus` | 93.6% | `country` | 62.2% |
| `previousStage` | 93.4% | `sourceContent` | 38.8% |
| `lastCallAttemptedAt` | 92.4% | `product` | 11.2% |
| `firstContactDate` | 92.4% | `externalId` | 3.6% |

Always populated: `leadId`, `stage`, `callsConnected`, `callsMissed`, `totalAttempts`,
`totalTalktimeSec`, `createdAt`, `updatedAt`, `ownerId`, `ownerName`, `ownerEmail`.

**The correlation matters more than the raw rate.** A lead with calls looks completely
different: a `Likely to Enroll` lead pulled from the live API had `winProbability: 55.0`,
`previousStage: "New"`, `lastDispositionStatus: "Connected"`,
`lastSubDispositionStatus: "Likely to Enroll"`, `firstContactDate` set. So `winProbability` is
not 98% useless — it is ~98% *unpopulated on never-called leads* and usable on the cohort we
actually care about. Do not discard these fields based on the global null rate.

**`conversionDate` is 100% null.** Conversion must be derived from `stage == "Converted"`,
never from the date field.

### 2.4 Stages are free text, not an enum

Top values from 500 leads:

```
Not Interested 84 │ New 61 │ DNP 5 45 │ DNP 1 43 │ DNP 3 32 │ Junk 25
Follow-up 1 23 │ DNP 2 22 │ DNP 4 21 │ Invalid 19 │ Not Responding 2 16
Converted 16 │ Qualified 15 │ Not Responding 1 14 │ Language Barrier 12
Not Looking for the Course 8 │ Exploring Courses 7 │ Follow-up 2 7
Did Not Apply 4 │ Not Reachable 4 │ Likely to Enroll │ Reassigned │ …
```

Note the **numbered ladders**: `DNP 1…5`, `Follow-up 1…2`, `Not Responding 1…2`. These encode
attempt count in the stage name — they are a sequence, not independent categories. A mapping
that flattens `DNP 1` and `DNP 5` to the same local status throws away the CRM's own
escalation signal.

`LeadStatus` has exactly six values (`NEW, CONTACTED, INTERESTED, FOLLOW_UP, CONVERTED,
DROPPED`). Feeding a raw CRM stage into it raises a Pydantic `ValidationError`.

### 2.5 Products are inconsistently formatted

```
iit-data-science 109 │ Full Stack Development 60 │ common 59 │ (null) 56
UI-UX-Course 33 │ Business Analytics with DM 30 │ IIT-Machine-Learning-Program 25
DevOps-Program 20 │ selenium-automation-testing 16 │ Digital Marketing 15
Intel AI-ML 12 │ Mech-Cad-Course 10 │ Civil-Cad-Course 9 │ career_consultation 6
data-engineering 6 │ do-not-know 5 │ SDE with AI 4 │ HackerKid 3 │ VFX with AI 2
```

Four naming conventions coexist: kebab-slug, Title Case, snake_case, Mixed-Kebab-Title.
`common`, `do-not-know` and `career_consultation` are **sentinel values, not courses**. The
course filter, which does an anchored case-insensitive regex on one field, cannot present this
as a clean dropdown without a normalization table.

### 2.6 Calls — coverage is the headline

Across **65 real calls** from 40 call-active leads:

| Property | Share |
|---|---:|
| `hasRecording` | **69.2%** |
| `hasTranscript` | **55.4%** |
| transcript **and** recording | **55.4%** |
| **neither** | **30.8%** |
| `analysisSummary` present | **36.9%** |
| `pitchScore` present | **36.9%** |
| `violations > 0` | 21.5% |
| `durationSec == 0` | 30.8% |
| `durationSec >= 30` | 58.5% |

Two structural facts:

- **Transcript ⊆ recording.** 55.4% have both; the both-count equals the transcript count
  exactly. There is no call with a transcript but no audio. So `hasTranscript` is a strictly
  better gate than `hasRecording`.
- **The 30.8% with neither are the `not_connected` / 0-duration rows.** They are unanalyzable
  by construction, and they exactly match the `durationSec == 0` share.

Status distributions:

```
status:      connected 45 │ not_connected 19 │ missed_call 1
finalStatus: completed 45 │ customer_canceled 19 │ agent_unanswered 1
direction:   outbound 56 │ inbound 9
```

Duration: **median 77s**, min 4s, max 1297s (21.6 min).

**Inbound calls exist (13.8%).** The prompt in `llm_service.SYSTEM_PROMPT` and the seeded
transcripts assume a BD dialing out. An inbound call has different pragmatics — the lead opened
with intent — and the outcome logic should know which it was.

### 2.7 `analysisSummary` is a structured JSON analysis, not a sentence

The PDF describes it as "the CRM's own short call analysis". It is far more. **24 of 24
non-null values parsed as JSON** with this shape:

```json
{
  "call_summary": "Salesperson called Karthick, a testing professional with a career gap...",
  "performance_metrics": { "pitch_score_percent": 43, "win_probability": 55 },
  "findings": {
    "autofill_data": [
      { "question": "What follow-up action was locked in?",
        "answer": "Customer to share email ID; salesperson to create profile and send details; customer to call back after reviewing." },
      { "question": "Did the prospect show immediate intent to enroll?", "answer": "no" },
      { "question": "What program or course did the prospect express interest in?", "answer": "AI/ML" },
      { "question": "What is the prospect's current status?", "answer": "Job seeker with career gap" }
    ],
    "violations": [
      { "severity": "LOW", "issue": "Missed Greeting / Introduction",
        "detail": "Agent skipped proper greeting and introduction..." }
    ],
    "improvement_tips": [ "Start the call with a proper introduction...", "..." ]
  }
}
```

This maps almost field-for-field onto `CallAnalysisResult`:

| `CallAnalysisResult` | Available from `analysisSummary` |
|---|---|
| `summary` | `call_summary` — directly |
| `key_points` | `autofill_data` Q&A pairs + `improvement_tips` |
| `customer_intent` | derivable from *"Did the prospect show immediate intent to enroll?"* + stage |
| `confidence` | `performance_metrics.win_probability / 100` |
| `outcome` | derivable from stage + intent answers |
| **`follow_up.datetime`** | **NOT available** — *"What follow-up action was locked in?"* is prose, never a timestamp |

**This is the crux of the product's remaining value.** The CRM analyzed the call; it did *not*
schedule the follow-up. Extracting a concrete follow-up datetime from the conversation is the
one thing this system does that the CRM does not. That should become the explicit, narrow job
of the LLM step — not re-summarizing what is already summarized.

⚠️ `analysisSummary` is documented as `string`. It happened to be JSON in every sample. Parse
defensively and fall back to treating it as plain text; do not let a parse failure fail a call.

### 2.8 Transcripts are code-mixed and romanized

`GET /transcript/{callId}` returned `source: "transcript_content"` for all 5 samples. Format:

```
[00:00] Agent: ok, kelkaam.
[00:07] Agent: aa, yes, kaarthikeyan.
[00:10] Agent: naan thaan ungalukku ennudaya personal number text onnu pottirunthen, ippo just—
[00:15] Customer: ippo vendaam, naan paarkala.
[00:32] Customer: saar, actually idhu placement eppadinna, ippo mark interview kuduppaanga.
```

That is **romanized Tamil with English code-switching** ("Tanglish"). Another call's violation
detail quoted Malayalam in native script (`ഓക്കേ, കേൾക്കാം`). Characteristics:

- Speaker labels are `Agent:` / `Customer:` — **not** the `BD:` / `Lead:` of the seeded
  transcripts.
- Every line is prefixed `[MM:SS]`.
- Lengths observed: **602 → 15,220 chars** (≈150 → ≈4,000 tokens).

**Direct consequence:** `call_analyzer.py` matches English phrase tables (`DROPPED_PHRASES`,
`CONVERTED_PHRASES`, `FOLLOW_UP_PHRASES`) and English/ISO date regexes. Against "ippo vendaam,
naan paarkala" it matches nothing and falls through to its default. With
`ANALYSIS_FALLBACK_ENABLED=true` (the current default), every LLM failure therefore produces a
**confidently wrong** outcome rather than a visible failure. The `degraded=True` flag is set,
but the dashboard treats the result as real.

### 2.9 Recordings cannot be played directly by the browser

Measured on a real `/recording/{callId}`:

- `content-type: audio/mpeg` ✅
- `content-length:` **absent**
- `accept-ranges:` **absent** — Range header ignored, full body returned
- **Without `X-API-Key`: `401 INVALID_API_KEY`**

Three consequences:

1. `<audio src="https://lead-call-api.../recording/{id}">` **cannot work** — the browser cannot
   attach the header. The PDF says this explicitly; the measurement confirms it.
2. `routes/calls.py::get_call_audio` returns a **307 redirect** for URL-mode calls. Redirecting
   the browser to the CRM yields a 401. This is a concrete break, not a theoretical one.
3. No `content-length` and no range support means **no seeking and no progress bar**, and the
   player must buffer the whole file. `AudioPlayer.jsx` will behave poorly.

---

## 3. Field-by-field mapping

### 3.1 Lead — the `name`/`phone`/`email` problem

Production `Lead fields` (PDF p.15, confirmed live): `leadId, externalId, stage, previousStage,
product, language, winProbability, segmentation, salesQualified, city, state, country,
leadSource, sourceCampaign, sourceMedium, sourceContent, lastSource, lastMedium, nurturing,
lastDispositionStatus, lastSubDispositionStatus, lastCallAttemptedAt, callsConnected,
callsMissed, totalAttempts, totalTalktimeSec, firstContactDate, conversionDate, createdAt,
updatedAt, ownerId, ownerName, ownerEmail, salesOwnerId, salesOwnerName`.

| Local `Lead` field | Production source | Verdict |
|---|---|---|
| `lead_id` | `leadId` | ✅ direct |
| `name` | **none** | 🔴 **no source** |
| `phone` | **none** | 🔴 **no source** |
| `email` | **none** | 🔴 **no source** |
| `course` | `product` | ⚠️ needs normalization (§2.5) |
| `assigned_bd.id` | `ownerId` | ✅ direct |
| `assigned_bd.name` | `ownerName` | ⚠️ seed stores a **first name**, CRM gives full name |
| `lead_status` | `stage` | 🔴 30+ values → 6-value enum |
| `follow_up.*` | **none** | ✅ correct — this is ours, derived from analysis |
| `last_call_at` | `lastCallAttemptedAt` | ✅ epoch ms → datetime |
| `latest_outcome` | derivable | ⚠️ from stage + analysis |
| `created_at` / `updated_at` | `createdAt` / `updatedAt` | ✅ epoch ms → datetime |
| — | `externalId` | ➕ **add**: the LeadSquared GUID, the cross-system join key |
| — | `winProbability`, `totalAttempts`, `callsConnected`, `callsMissed`, `totalTalktimeSec`, `lastDispositionStatus`, `leadSource`, `city/state/country`, `language` | ➕ **add**: high-value, currently discarded |

**The PII gap is a product decision, not a technical one.** Options, in recommended order:

1. **Re-key the UI on `leadId` + owner + product + stage.** Most honest. The lead table's
   identity column becomes `leadId` (short, e.g. `WN5qOX_8mRhQSh0`) with product and stage as
   context. The BD already knows who the lead is in the CRM. `externalId` links back to
   LeadSquared. Requires `name`/`phone`/`email` to become optional on the model and the search
   filter to drop its `name|email|phone` regex branches.
2. **Enrich from LeadSquared separately** using `externalId`. Correct long-term, but this MVP
   deliberately has no LeadSquared integration, and it re-introduces PII handling.
3. Keep names blank/placeholder. Worst — the table reads as broken and the search box lies.

Whichever is chosen, `Lead.name/phone/email` must stop being required.

### 3.2 Call — a status-field collision

| Local `Call` field | Production source | Verdict |
|---|---|---|
| `call_id` | `callId` | ✅ direct — use it, don't mint `CALL-xxxx` |
| `lead_id` | request context | ✅ |
| `caller_id` | call-level `ownerId` | ⚠️ **differs from the lead's owner** — see below |
| **`status`** | **`status`** | 🔴 **name collision** |
| `duration_seconds` | `durationSec` | ✅ |
| `started_at` / `ended_at` | `startTime` / `endTime` | ✅ epoch ms |
| `created_at` | `callTime` | ✅ |
| `audio_url` | `recordingPath` | ⚠️ relative path, needs BASE + auth |
| `source_type` | — | ⚠️ needs a new `CRM` value |
| — | `direction`, `finalStatus`, `pitchScore`, `violations`, `analysisSummary`, `hasRecording`, `hasTranscript` | ➕ **add** |

🔴 **The collision:** local `Call.status` is a *pipeline* status (`UPLOADED → PROCESSING →
TRANSCRIBING → ANALYZING → COMPLETED / FAILED`) written by `process_call()` at every
transition. Production `status` is a *telephony* status (`connected`, `not_connected`,
`missed_call`). These are unrelated axes and both are needed. They must live in separate fields
— e.g. keep `status` for the pipeline and add `telephony_status` + `final_status`.

⚠️ **Caller ≠ lead owner.** In a real record from the live API, the lead was owned by
`data@hclguvi.com` (Saravana) while its three calls were made by `vigneshvr@hclguvi.com` and
`solomon@hclguvi.com`. The model already separates `lead.assigned_bd` from `call.caller_id`,
which is correct — but the `callers` collection must be populated from **both** lead owners and
call owners, or `caller_id` lookups 404. There is no `/callers` endpoint on the CRM; the caller
directory has to be synthesized from the `ownerId/ownerName/ownerEmail` triples seen in the
data.

### 3.3 Transcript

| Local `CallTranscript` | Production | Verdict |
|---|---|---|
| `transcript` | `transcript` | ✅ |
| `provider` | `source` | ✅ map (`transcript_content` etc.) |
| `language` | **none** | ⚠️ must be detected, or left null |
| `duration_seconds` | from the call | ✅ |

`source` has four documented values (`transcript`, `transcript_content`, `transcript_content_vt`,
`transcript_url`) and the PDF warns **"the formats differ slightly"**. Only
`transcript_content` appeared in the sample. Store `source` verbatim so format-specific parsing
stays possible later.

---

## 4. What must CHANGE (blocking)

Ordered by severity. Each is a correctness break, not a preference.

**C1 — `Lead.name/phone/email` must become optional.** No production source exists. Currently
required → every production lead fails validation. Drag-along: `filters.mongo_query` search
regex, `LeadTable.jsx` name column, `followup_alert_service._render_digest` (renders
`name`/`phone`).

**C2 — Map `stage` → `lead_status` through an explicit table.** 30+ free-text values into a
6-value enum. Store the raw `stage` alongside the mapped status so nothing is lost, and default
*unknown* stages to a safe value rather than raising. Proposal: `Converted → CONVERTED`;
`Not Interested / Junk / Invalid / Not Looking for the Course / Did Not Apply → DROPPED`;
`Follow-up N / Likely to Enroll / Exploring Courses / Qualified → FOLLOW_UP`;
`DNP N / Not Responding N / Not Reachable / Language Barrier → CONTACTED`; `New → NEW`;
unknown → `NEW` + log.

**C3 — Split the call status fields.** §3.2. Without this, ingesting a CRM call either
corrupts the pipeline state machine or discards telephony state.

**C4 — Proxy the audio instead of redirecting.** `get_call_audio`'s 307 → CRM returns 401.
Must stream server-side with the `X-API-Key` attached. Also: no `content-length`/`accept-ranges`
from upstream, so the response cannot promise range support.

**C5 — Guard the keyword fallback against non-English transcripts.** §2.8. Either disable the
fallback for CRM-sourced calls (fail visibly), or gate it on a cheap language check. A
confidently wrong outcome on a Tanglish call is worse than a failed one, because
`apply_analysis_to_lead` writes it to the lead.

**C6 — Paginate every list endpoint.** `list_leads`, `list_follow_ups`, `list_closed` and
`get_summary` all do `list(db[LEADS].find(...))` with no limit, then filter in Python. At
400k+ leads this exhausts memory. The two-phase filter design (`mongo_query` +
`apply_bucket_filter`) is sound but *assumes a small candidate set* — that assumption is now
false.

**C7 — Epoch-ms conversion everywhere.** All CRM timestamps are epoch **milliseconds**; all
local storage is tz-aware UTC datetimes. One shared converter, applied at the boundary. A
seconds/milliseconds mix-up puts records in 1970.

**C8 — Idempotent ingestion.** Use the CRM `callId` as `call_id` and upsert. `call_analyses`
has no unique index on `call_id` (deliberately — it stores every attempt), so a re-sync without
a guard re-analyzes and re-bills every call.

---

## 5. What can be OPTIMIZED

### 5.1 Sync only leads that have calls — ~95% fewer records

`filters.attemptsMin` / `connectedMin` are evaluated **server-side by the CRM**, and the lead
document already carries `callsConnected`/`totalAttempts`, so no call-log paging is needed to
decide. Measured: 5.2% of leads have a connected call.

```
{"filters": {"connectedMin": 1}, "limit": 500, "batch": N}
```

≥400,500 leads → ~20,000 relevant. The PDF's own worked example (p.18) iterates **every** lead
and fetches calls per lead — that is an N+1 over 400k leads and would take days at the rate
limit. Filter first.

### 5.2 Skip transcription on 55% of calls — the single biggest cost saving

`hasTranscript: true` → `GET /transcript/{callId}` and feed it straight to the analysis step.
The Vertex Gemini audio transcription call is the most expensive and slowest stage in
`process_call()`; `process_call()` already reuses a stored transcript when `transcript_id` is
set, so the hook exists.

Per 100 call-active calls: **≈55 need no transcription at all**, ≈31 have neither transcript
nor recording and should be skipped entirely, leaving ≈14 that genuinely need audio
transcription.

### 5.3 Reuse the CRM's own analysis on 37% of calls

§2.7. `analysisSummary` supplies `summary`, `key_points`, `confidence` and strong signal for
`customer_intent`. Narrowing the LLM call to **follow-up datetime extraction + outcome
classification** on a pre-summarized input cuts prompt size and output size substantially, and
on calls where no follow-up is plausible (stage `Converted`/`Not Interested`) the LLM can be
skipped outright.

### 5.4 Never analyze a call that cannot be analyzed

Skip before any paid call:

- `status != "connected"` (29.2% of calls)
- `durationSec == 0` (30.8%)
- `hasTranscript == false && hasRecording == false` (30.8%)

A useful floor is `durationSec >= 30` — the PDF's own worked example uses it, and 41.5% of
calls fall below it. A sub-30s connected call is a wrong-number or a hangup.

### 5.5 Respect the rate limit deliberately

**60 requests / 10 seconds per key** (6 rps), `429` + `Retry-After`. Measured: 40 sequential
`/lead/calls` requests took 16.1s at 0.17s pacing — comfortably inside. What's needed:

- A shared token-bucket limiter across all CRM calls (a single worker can trivially exceed 6 rps
  on transcript fetches).
- **Honor `Retry-After`** on 429 rather than blind exponential backoff.
- Bounded concurrency (4–6 in flight), not unbounded `asyncio.gather`.
- One pooled `httpx.Client` — `analysis_llm_client.py` currently constructs a client per call,
  paying a TLS handshake each time. The same mistake should not be repeated for the CRM client.

### 5.6 Cache what is immutable

- **Transcripts never change** — cache permanently, keyed by `callId`. Never re-fetch.
- **Recordings never change** — cache to disk on first proxy; `AUDIO_UPLOAD_DIR` already exists.
  Also works around the missing `content-length`/range support by serving a complete local file
  with proper headers.
- **`analysisSummary`** — immutable per call, store it at ingest.
- Lead lists change constantly — short TTL or none.

### 5.7 Index for the new scale

Current indexes were sized for 15 leads. Missing at 400k, given the actual query patterns:

```
leads: (lead_status)                                                # CLOSED_QUERY
leads: (follow_up.required, follow_up.status, follow_up.datetime)   # ACTIVE_FOLLOW_UP_QUERY + date bounds
leads: (latest_outcome)                                             # outcome filter
leads: (external_id) unique sparse                                  # CRM join key
leads: (assigned_bd.id)                                             # only assigned_bd.name is indexed today
leads: (updated_at desc)                                            # every list sorts on this
```

Also: the `search` filter uses **unanchored** `$regex` on four fields — a full collection scan
at any scale. With no `name`/`email`/`phone` to search (C1), this filter should be rebuilt
around `leadId`/`externalId` exact match anyway.

### 5.8 Two sync cadences, not one

- **Incremental (frequent):** `updatedFrom = <last sync watermark>` catches changes cheaply.
- **Call-activity sweep:** `lastCallFrom = <watermark>` catches leads whose call log grew.
- **Full backfill:** once, paged, filtered by `connectedMin: 1`.

`updatedAt` is 0% null, so the watermark is reliable.

---

## 6. What can be IMPROVED

**I1 — Expose `aiQuery` as the dashboard search box.** With `name`/`phone`/`email` gone, the
search field has little to do. The CRM's `aiQuery` accepts plain English ("leads from facebook
created in the last 7 days"), resolves it against real stage names, and returns
`appliedAiConditions` so the resolved filter can be shown back to the BD. That is a better
search than a regex over a field that no longer exists. Note the PDF's caveat: pass call
`duration` via `filters.durationMinSec`, never through `aiQuery` — the unit conversion is only
correct on the literal filter. Handle `AI_FILTER_NO_CONDITIONS` /
`AI_FILTER_ALL_CONDITIONS_BLOCKED` / `AI_FILTER_FAILED` as user-facing messages, and surface
`droppedConditions`.

**I2 — Surface `pitchScore` and `violations` in the UI.** Present on 37% of calls, already
computed, currently discarded. BD coaching signal for free. `improvement_tips` likewise.

**I3 — Filter by BD on `ownerEmail`, not `assigned_bd.name`.** 80 distinct owners per 500-lead
sample means the `distinct("assigned_bd.name")` dropdown becomes unusable, and names collide
(two "Saravana"s are plausible; emails are unique). The seed's first-name-vs-full-name
inconsistency disappears at the same time.

**I4 — Use the CRM's richer call-activity fields in the worklist.** `lastDispositionStatus`,
`lastSubDispositionStatus`, `totalAttempts` and the `DNP N` ladder are a real prioritization
signal — a `DNP 5` lead deserves different treatment from `DNP 1`. Currently nothing but our
own follow-up datetime orders the worklist.

**I5 — Map CRM errors onto the existing handler taxonomy.** `main.py` maps exception types to
statuses; CRM failures need the same treatment: `401 INVALID_API_KEY` / `API_KEY_REVOKED` →
operator-facing config error (never a 500); `429 RATE_LIMITED` → retry with `Retry-After`;
`502 CRM_UNAVAILABLE` / `RECORDING_ACCESS_DENIED` → 503 like `VertexUnavailable`;
`404 RECORDING_NOT_AVAILABLE` / `TRANSCRIPT_NOT_AVAILABLE` → skip the call, don't fail the sync.
The CRM's `{"status","message","errorCode"}` body is machine-readable — use `errorCode`.

**I6 — Handle `direction: inbound` (13.8%) in the prompt.** `SYSTEM_PROMPT` assumes an outbound
BD call. Tell the model which it was.

**I7 — Teach the prompt about romanized Indic transcripts.** State that input may be Tanglish or
Manglish with `[MM:SS] Agent:/Customer:` markers. The current prompt is silent on this and the
seeded examples are clean English dialogue.

**I8 — Key handling and rotation.** The CRM key is a bearer credential to real customer
conversations. It belongs in `backend/.env` only, must never reach `UIConfig` (`/api/config`
correctly returns no secrets today — keep it that way), and `/admin/keys/create` +
`/admin/keys/revoke` make rotation cheap. A revoked key stops working on its *next* request, so
rotation needs no downtime.

**I9 — The SSRF guard doesn't apply to CRM audio.** `audio_service.validate_audio_url` +
`_assert_public_host` exist to vet *user-supplied* URLs. CRM `recordingPath` values are
first-party and relative; they should go down a separate trusted path, not through URL
validation. Conversely `download_url_to_temp` sends no auth header, so it cannot fetch a CRM
recording as-is.

**I10 — `total: null` means no exact counts.** `DashboardSummary.total_leads` currently equals
`len(docs)` after loading everything. With pagination (C6) that number becomes "leads on this
page". Either compute counts with `count_documents` against the local mirror (accurate, since
we control it) or present them as approximate. Do not promise a total the CRM cannot give.

**I11 — Don't destroy existing data on sync.** `seed.py` **drops** all five collections. A CRM
sync must be additive/upsert-only. Seeded docs carry `"seeded": True`, which is a convenient
discriminator for separating sample from real records rather than wiping the database.

---

## 7. Suggested sequencing

| Phase | Work | Unblocks |
|---|---|---|
| **0** | This document + decide the PII question (§3.1) | Everything downstream |
| **1** | CRM client: pooled `httpx.Client`, token-bucket limiter, `Retry-After` handling, `errorCode` mapping (I5), epoch-ms converter (C7) | All I/O |
| **2** | Model changes: optional PII (C1), stage map (C2), split status fields (C3), new CRM fields (§3.1/3.2) | Ingestion |
| **3** | Additive sync: `connectedMin: 1` paging (5.1), upsert by `callId` (C8), synthesize `callers` from owner triples (§3.2), skip unanalyzable calls (5.4) | Real data in the dashboard |
| **4** | Pipeline savings: reuse CRM transcripts (5.2), reuse `analysisSummary` (5.3), narrow the LLM to follow-up extraction | Cost |
| **5** | Scale: pagination (C6), indexes (5.7), audio proxy + cache (C4, 5.6) | Usability at 400k |
| **6** | UX: `aiQuery` search (I1), pitch/violations (I2), owner-email filter (I3) | Polish |

---

## Appendix A — Measurement methodology

All figures were measured live on **2026-09-22** against
`https://lead-call-api.codingpuppet.com`. **Read-only:** only `GET /health`,
`POST /leads/search`, `POST /lead/calls`, `GET /transcript/{id}` were called, plus one partial
`GET /recording/{id}` for headers. No recording was downloaded in full, no transcription was
run, and nothing was written to the CRM.

| Statistic | Sample | Caveat |
|---|---|---|
| Lead field null rates, stage/product/source distributions | 500 leads, `{"limit":500,"batch":0}` | **Not random** — default sort is `created_at desc`, so this is the 500 most recently created leads. Treat as indicative. |
| Call coverage, status/duration distributions | 65 calls from the first 40 leads matching `connectedMin: 1` | Biased toward call-active leads **by design** — that is the cohort this product acts on. Not representative of all calls. |
| Transcript format and length | 8 transcripts (5 for `source`, 8 for length) | Small sample. All were `transcript_content`; the other three documented `source` values were not observed. |
| Recording headers | 1 recording, partial fetch | — |
| Dataset size | `{"limit":500,"batch":800}` still returned `hasMore: true` | Establishes a **floor of 400,500**, not a total. `withTotal: true` returns `total: null`. |
| Call-active lead count | Paged `attemptsMin:1` and `connectedMin:1` | Walk capped at 40 pages → **"≥ 20,000"**, not exhausted. |

**Incidental finding:** `Python-urllib`'s default User-Agent (`Python-urllib/3.x`) is blocked by
the edge with `403 Forbidden` on `/lead/calls`. `httpx` (what this codebase uses), `curl`, and
any custom UA all return `200`. Not an issue for this repo, but it will bite anyone debugging
with a stdlib one-liner.

## Appendix B — Repo touchpoints referenced

`backend/app/models/lead.py` · `backend/app/models/call.py` · `backend/app/models/analysis.py` ·
`backend/app/models/schemas.py` · `backend/app/routes/leads.py` · `backend/app/routes/calls.py` ·
`backend/app/routes/filters.py` · `backend/app/routes/dashboard.py` ·
`backend/app/services/call_analysis_service.py` · `backend/app/services/llm_service.py` ·
`backend/app/services/call_analyzer.py` · `backend/app/services/audio_service.py` ·
`backend/app/services/followup_service.py` · `backend/app/services/analysis_llm_client.py` ·
`backend/app/services/followup_alert_service.py` · `backend/app/database.py` ·
`backend/app/config.py` · `backend/seed.py` · `frontend/src/components/LeadTable.jsx` ·
`frontend/src/components/AudioPlayer.jsx` · `frontend/src/components/FilterBar.jsx`
