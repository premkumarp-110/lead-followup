# MODULES.md — build tracker

Tracker for **Lead Follow-up Reminder + professional dashboard**.

Status: `⬜ Not started` · `🟡 In progress` · `✅ Completed`

**Working agreement:** one module at a time. Each requires explicit acknowledgement before it
starts, and this file is updated at every transition.

---

## Progress

| # | Module | Layer | Edge cases | Status |
|---|---|---|---:|---|
| **M1** | Reminder domain + delivery log | Backend | 16 | ✅ Completed |
| **M2** | Reminder API | Backend | 10 | ✅ Completed |
| **M3** | Scheduler hardening | Backend | 7 | ✅ Completed |
| **M4** | Insights + operations API | Backend | 10 | ✅ Completed |
| **M5** | Design-system foundation | Frontend | 6 | ✅ Completed |
| **M6** | App shell, navigation, reminder bell | Frontend | 9 | ✅ Completed |
| **M7** | My Worklist view | Frontend | 9 | ✅ Completed |
| **M8** | Leads view | Frontend | 6 | ✅ Completed |
| **M9** | Insights view | Frontend | 4 | ✅ Completed |
| **M10** | System view | Frontend | 4 | ✅ Completed |
| **M11** | Docs + tracker close-out | Docs | — | ✅ Completed |
| **M12** | Re-base on the Lead Call API schema | Full stack | 11 | ✅ Completed |
| **M13** | Retire "Done"; sentiment analysis + prioritisation | Full stack | 9 | ✅ Completed |

**13 / 13 complete.** ✅ All modules shipped.

- **M1–M4 (backend):** 31 domain checks, 17 insights edge cases, 20 reconciliation checks across
  6 filter combinations, one live cron fire.
- **M5–M10 (frontend):** verified in a real browser at 1440px and 390px — 17 interaction and
  reconciliation assertions, 11 edge-case assertions (empty DB, alerts off, analyzer off, SMTP
  unconfigured), zero console errors, no horizontal scroll on mobile.
- `check_analyzer.py` 11/11 throughout; `npm run build` clean.
- Every verification ran against a scratch database. The live Atlas cluster in `backend/.env`
  was never touched, and no real email was ever sent.

Order: M1→M4 backend, strictly sequential. M5 before all other frontend work. M6 before M7–M10.
M11 closes out.

---

## Two standing constraints

**No auth.** "Which BD am I?" has no real answer in this MVP. A header BD selector (persisted to
`localStorage`, default *All BDs*) stands in for identity. It is a convenience, **not an access
control**. Decision point at M6.

**No historical snapshots, so no trends.** Nothing stores the daily state of the queue;
`follow_up_history` records actions taken, not backlog size over time. Week-over-week deltas and
burn-down charts are **not derivable**. Insights shows what the data genuinely supports and
states the absence rather than faking trend arrows. Real trends would need a separate
nightly-snapshot module.

---

## Module detail

### M1 — Reminder domain + delivery log ✅
Widen alerts from overdue-only to overdue + due-today, and start recording every send.
- **Files:** `backend/app/services/followup_alert_service.py`, `backend/app/database.py`
- **Adds:** `pending_leads_by_bd()`, `send_pending_alerts()`, `followup_alerts` collection
- **Reuses:** `followup_service.decorate_lead()` / `compute_bucket()` / `_as_utc()` / `utcnow()`
- **Fixes two live bugs:** `KeyError` on leads without `name`/`phone`
  (`followup_alert_service.py:51-52` subscripts directly); unescaped HTML in the digest
- **Key subtlety:** "due today" ≠ the `DUE` bucket. `DUE` is a 2-hour window, so due-today is
  `DUE`/`UPCOMING` falling on today's **Asia/Kolkata** date. Getting this wrong empties the
  morning digest.
- **Dedupe:** scheduled sends skip a duplicate `(bd_id, sent_for_date)`; manual always sends
- **Changed during build:** a `DUE` follow-up is now always included regardless of IST date.
  Gating it on "today" dropped a call due in 45 minutes when the clock was about to cross
  midnight — caught by running the grouping at 23:35 IST.
- Also added `count_orphaned_followups()` (edge 14) and `IST`/`ist_date`/`is_ist_today` in
  `followup_service.py`; `scheduler.py` re-pointed at `send_pending_alerts` (M3 still hardens it)

### M2 — Reminder API ✅
- **Files:** new `backend/app/routes/alerts.py`; `main.py`; `models/schemas.py`
- `GET /api/alerts/pending` · `GET /api/alerts/preview` · `POST /api/alerts/send` ·
  `GET /api/alerts/history`
- Only `/send` is gated on `FOLLOWUP_ALERTS_ENABLED` (403). Reads never gated.
  SMTP unconfigured → 503 with `email_config_error()`. Nothing pending → 200, not an error.
- **Deviation:** `?limit=` out of range returns **422** rather than clamping, matching how the
  rest of the API validates query params. Silent clamping hides a caller's mistake.

### M3 — Scheduler hardening ✅
- **File:** `backend/app/scheduler.py`
- 🔴 **The real fix was `misfire_grace_time`.** APScheduler's default is **1 second**, so a run
  missed by a restart or a busy host was dropped **silently** — the exact failure this feature
  exists to prevent. Now 3600s. Demonstrated: a run due 90s ago fires at 3600 and does not at 1.
- **Correction to the plan:** `coalesce=True` and `max_instances=1` are *already* APScheduler
  defaults. They are now set explicitly for durability across upgrades, but they changed nothing.
- Job body wrapped so an unexpected error cannot kill the scheduler thread; job id renamed to
  `daily_followup_reminders`; added `scheduler_status()` (next run time) consumed by `/operations`
- ⚠️ **Single worker only** — `uvicorn --workers N` starts N schedulers. README in M11.

### M4 — Insights + operations API ✅
- **File:** new `backend/app/routes/insights.py`; IST fix in `routes/dashboard.py`
- `/api/insights/overview` (ageing, outcome mix, unscheduled/unassigned) · `/by-bd` · `/operations`
- Both halves of the filter contract applied; one `now` per request. Verified by 20 reconciliation
  assertions across 6 filter combinations — by-bd sums == overview == dashboard == worklist rows.
- **Found during build:** the disjoint buckets did not sum to `pending_total`, because a `DUE`
  lead appeared only in the overlapping `due_today`. Added an explicit `due` count; the response
  now marks which fields are disjoint and which deliberately overlap.
- **Behaviour change:** `dashboard.py::_is_today` compared dates in **UTC**, so after 18:30 IST
  `due_today` disagreed with the reminder email. Replaced with `is_ist_today()` and aligned to the
  same DUE rule, so the card and the email now match.

### M5 — Design-system foundation ✅
- **File:** `frontend/src/index.css`
- Tokens (spacing / radius / shadow / type scale / semantic roles) then primitives: buttons,
  inputs, badges, tables, cards, tabs, modals, skeletons, empty states
- Tabular numerals, right-aligned numerics, borders over shadows, dense rows, designed empty
  states — the rules that separate a data product from a generic page
- **Keeps the existing class contract** so nothing breaks mid-refactor
- Icons are inline SVG, not emoji — they inherit colour and size and render the same everywhere

### M6 — App shell, navigation, reminder bell ✅
- **Files:** new `AppShell.jsx`, `ReminderBell.jsx`, `BDSelector.jsx`; `App.jsx`, `api.js`
- Sidebar: **Worklist · Leads · Insights · System**. Local state, **no react-router dependency**
- Bell count = overdue + due today; panel with "last reminded" and **Send digest now**
- Decision point for the no-auth BD selector — **still open**: the selector ships as planned,
  default All BDs, persisted via a `localStorage` wrapper that cannot throw in private mode
- Leads/Insights/System: the existing dashboard is mounted as Leads so the app stays usable;
  Insights and System show honest "not built yet" placeholders until M9/M10

### M7 — My Worklist view ✅
- **Files:** new `WorklistView.jsx`, `WorklistRow.jsx`
- Overdue (with ageing) → Due today → Unscheduled. Every row answers *why*. `tel:` links.
  Inline Done / Reschedule / Cancel without losing place.
- Reuses `GET /api/leads/follow-ups`, already sorted by `sort_worklist()`
- **Found in the browser, not in code review:** the reminder bell went stale after actioning a
  lead — it only refetched on BD change. Now refetches on the shared `dataVersion`.
- Also fixed from screenshots: sidebar did not paint full height on long pages; the bell rendered
  a group header with no rows beneath it when a BD's only pending work was unscheduled.

### M8 — Leads view ✅
- **Files:** new `LeadsView.jsx`; refactored `LeadTable.jsx`, `FilterBar.jsx`
- Sticky header, active-filter chips with individual removal, result count, CSV export
- **Analyze Call became its own nav item** (agreed): it was pushing the table ~600px down the page
- Surfaced `upcoming` / `unscheduled` on the cards — computed since the first commit, never shown
- `Dashboard.jsx` retired; `LeadsView` replaces it
- **Bug found by rendering:** `.reason-cell` had `display:-webkit-box` on the `<td>` itself, which
  removes it from the table formatting context — reason text spilled across row boundaries. The
  clamp now lives on an inner `.clamp-2` div.

### M9 — Insights view ✅
- **File:** new `InsightsView.jsx`
- Ageing histogram, per-BD table, outcome mix with denominators, the unscheduled leak, alert
  delivery health. **Inline SVG — no charting dependency.**
- States explicitly that trends are unavailable and why
- KPI row labels `due_today` as deliberately overlapping, so nobody sums five numbers wrongly

### M10 — System view ✅
- **File:** new `SystemView.jsx`
- `unprocessed_calls` (computed since the first commit, rendered nowhere until now), failed calls
  by stage, degraded analyses **with an explanation**, alert delivery, effective config
- Separates scheduler *enabled* from *actually running* — the state that explains a missing digest
- Never renders a secret

### M13 — Retire "Done"; sentiment analysis + prioritisation ✅

Two changes, both moving authority over a lead's fate from the BD's assertion to the call.

**Done is gone.** `COMPLETE` joins `CANCEL` in `RETIRED_ACTIONS` — refused with a 422, absent
from every surface. **`RESCHEDULE` is the only action left**, so `follow_up.status` can now only
hold `PENDING` on new data and a follow-up leaves the worklist exactly one way: a newer call is
analysed and concludes CONVERTED/DROPPED or sets a new date. Both enum values survive so
historical `follow_up_history` rows still deserialise; the `follow_up.status` half of
`CLOSED_QUERY` now matches legacy rows only.

- 🔴 **Exposed a latent bug.** `WorklistView.submitAction` optimistically dropped the row after
  any action. Correct for Done; **wrong for Reschedule**, where the lead is still PENDING — it
  vanished and reappeared on refetch. Done was masking it. Removed.
- Insights swapped `completed_all_time`/`cancelled_all_time` — both frozen at zero on any new
  database — for `rescheduled_all_time`, which still moves and is real signal (a lead pushed
  four times is a lead being avoided).

**Sentiment.** `CallSentiment` adds `label` / `score` / `trajectory` / `evidence` to the
analysis. Nothing computed tone before, and the CRM supplies none — its `analysisSummary` rates
the *agent* (pitch score, violations); this rates the *lead*.

Three deliberate calls:
- **Sentiment and outcome are not forced to agree.** A polite decline is genuinely POSITIVE tone
  with a DROPPED outcome; consistency would destroy the signal being added.
- **`trajectory` outranks `label` in the sort.** A call that ended worse than it started is the
  earliest sign of a lead going cold, invisible in an averaged label.
- **The keyword fallback never judges tone** — always UNKNOWN, no score. Its tables can spot a
  refusal; a score from keyword counts would be a guess dressed as a measurement, written to the
  lead and used to order the queue.

`sort_worklist()` now keys on `(bucket, sentiment_rank, datetime)`: urgency dominates between
sections, sentiment leads within one. The section hints changed from "Oldest first" to "Hardest
first, then oldest" — the old label would have been a lie.

**Found while verifying:** the end-to-end ordering assertion initially could not fail, because
random per-lead sentiment gave all five OVERDUE leads the same rank. The seed now *cycles*
through each bucket's declared spread instead of sampling it, so every reseed shows the full
range and the test has teeth.

Also fixed two pre-existing `check_analyzer.py` quirks: the predicate asserted only outcome and
datetime (printing `customer_intent` without checking it), and the printed total was
`len(CASES)` — counting guard failures in the numerator but not the denominator. Now 32/32.

**Verified:** `check_analyzer.py` 32/32 · build clean · reconciliation across 7 filter
combinations · ordering asserted per bucket with a real spread · `?sentiment=` any-of sums
correctly · COMPLETE and CANCEL both 422 while historical rows still read · zero console errors.

### M12 — Re-base on the Lead Call API schema ✅

Replaced the invented sample model with the real CRM schema, end to end. Verified live against
`https://lead-call-api.codingpuppet.com` before any code changed.

**What the live API actually showed** (300 call-active leads, 78 calls):
- 🔴 **No `name`, `phone` or `email` exists.** All three were *required* on the local `Lead`
  model, so every production lead would have failed validation. Identity is now `lead_id` +
  `external_id`.
- 🔴 **`salesOwner*`, `sales_qualified` and `conversion_date` are null on every lead.** Stored,
  never invented. Conversion is derived from `stage == "Converted"`.
- ✅ **BDA data is rich** — 116 distinct owner triples in 300 leads, and a lead's owner routinely
  differs from whoever made its calls. `callers` is synthesized from **both** sources; building
  it from leads alone leaves `caller_id` lookups 404ing.
- **Follow-up data exists, but never a date.** `stage`/`lastSubDispositionStatus` carry
  `Follow-up 1` (21%), `Likely to Enroll`, `Payment Link Shared`; `analysisSummary` carries
  *"What follow-up action was locked in?"* as prose (*"Google Meet tomorrow at 11 AM"*). ~1/3 of
  those answers contain an extractable time. **This is the product's remaining value** and it now
  drives the design.
- ~55% of analyzable calls already have a transcript; ~37% already have the CRM's own analysis.
  Both are now reused rather than recomputed.

**Files:** new `services/lead_call_client.py`, `services/stage_mapping.py`, `seed_data.py`,
`tools/harvest_recording_ids.py`, `fixtures/crm_recording_ids.json`; rewritten `seed.py`,
`models/lead.py`, `models/call.py`, `models/caller.py`, `routes/filters.py`, `routes/calls.py`,
`services/audio_service.py`, `BDSelector.jsx`, `LeadDetailsModal.jsx`, `AudioPlayer.jsx`.

**Removed:** `CallAnalyzer.jsx`, `ProcessingSteps.jsx`, `SystemView.jsx`; `POST /api/calls/upload`
· `/validate-url` · `/from-url` · `/from-text` · `/{id}/process`, `GET /{id}/status`,
`GET /api/insights/operations`; the whole upload/SSRF path in `audio_service.py`;
`CALL_ANALYZER_*`, `AUDIO_STORAGE_MODE`, `MAX_AUDIO_MB`, `ALLOWED_AUDIO_TYPES`; ten dead
`api.js` functions and three dead `format.js` helpers. `ffprobe` is no longer a dependency.

**Four bugs found by running it, not by reading it:**

1. 🔴 **`[MM:SS]` transcript markup hijacked every date extraction.** Real transcripts are
   `[01:20] Customer: …` lines; the offset is indistinguishable from a clock time and always
   comes first. "Call me back at 8 PM" was being scheduled at **01:20**. Since every CRM
   transcript has these, the fallback would have mis-timed essentially every call.
   `strip_transcript_markup()` + 2 regression cases.
2. 🔴 **The keyword fallback silently mislabelled non-English calls.** Its phrase tables are
   English; against romanized Tamil it matched nothing and returned its *default* outcome, which
   `apply_analysis_to_lead` then wrote to the lead. `is_analyzable_language()` now refuses, and
   the request fails visibly instead. Confirmed live: a Tanglish call returned 503 and left the
   lead untouched, while an English one fell back and updated correctly.
3. 🔴 **`sparse=True` does not exclude explicit nulls.** Sparse only skips *absent* fields, so
   the second lead with `external_id: null` (~4% of real leads) collided on the unique index.
   Fixed with `partialFilterExpression: {external_id: {$type: "string"}}`.
4. **`.truncate` on a `<td>`** sets `display:block` and pulled the BD column out of its row —
   the same trap as the `.reason-cell` bug in M8. Caught in a screenshot; fixed with an inner div.

**Audio is proxied, never redirected.** A browser cannot attach `X-API-Key` to an `<audio src>`,
so the old 307 would have returned 401. The backend fetches once, caches to disk, and serves from
there — which also restores duration and seeking, since upstream sends no `content-length` and
ignores `Range`. Verified: 6.5s first fetch, 0.09s cached, real `audio/mpeg`.

**Mock data, real shape.** 60 leads / 12 callers / 40 calls, drawn from the measured
distributions. Seeded calls with a recording borrow a real CRM `callId` into `crm_call_id` so
playback resolves; nothing else about them is real.

**Verified:** `check_analyzer.py` 14/14 · `npm run build` clean · reconciliation across 5 filter
combinations (summary == worklist == insights == disjoint-bucket sum) · BD filter by email and by
id both equal the by-bd count · audio cache hit/miss · analyse from the UI end to end · no
horizontal scroll at 390px on any view · zero console errors.

### M11 — Docs + tracker close-out ⬜
- **Files:** this file, `README.md`, `.env.example`, `backend/.env.example`
- README §8 rewritten for the five views; new §8a documents reminders end to end
- The `--workers` constraint is documented in both the README and `backend/.env.example`
- Both `.env.example` files already carried the `FOLLOWUP_*`/`SMTP_*` keys — verified rather than
  assumed; only the stale overdue-only comments needed correcting

---

## Already in place (not part of this work)

- Lead dashboard, worklist / closed tabs, filters, follow-up actions with history
- Call ingestion (upload / URL / text) and the transcribe → analyze → apply pipeline
- Derived follow-up buckets (`compute_bucket`), never persisted
- The original overdue-only email digest and its APScheduler cron — **M1–M3 extend this**
- `PRODUCTION-DATA-ANALYSIS.md` — the original gap analysis between the Lead Call API and the old
  sample model. **M12 implemented its blocking findings** (C1-C5, C7-C8 and I2/I3/I5/I6/I7/I9).
  Still outstanding, and still correct: **C6 pagination** — every list endpoint loads the full
  matching set into Python, which is fine at 60 seeded leads and falls over at the 400k+ the CRM
  actually holds. Do that before pointing this at a live sync.

## Out of scope

Auth, SMS / WhatsApp / push, per-BD reminder preferences, dark mode, nightly snapshots for real
trends, and **write-back to the CRM** (the Lead Call API is read-only).

A **live CRM sync** is deliberately not built. `lead_call_client.py` is the piece it would need —
pooled client, rate limiting, error mapping, epoch-ms conversion — and it already exists, used
today for recording playback. Adding a sync means paging `{"filters": {"connectedMin": 1}}`
(~5% of leads have any call activity), upserting by `callId`, and doing **C6 pagination** first.
