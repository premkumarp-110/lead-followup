# Module Tracker — Lead Follow-up Management Dashboard

Status values: `NOT STARTED` · `IN PROGRESS` · `COMPLETED`

| # | Module | Status | Notes |
|---|--------|--------|-------|
| 1 | Scaffold & config | COMPLETED | Dir tree, requirements.txt, .env.example, .gitignore, config.py, database.py |
| 2 | Models & schemas | COMPLETED | models/lead.py, models/call.py, models/schemas.py |
| 3 | Call analyzer | COMPLETED | services/call_analyzer.py — deterministic, LLM swap point |
| 4 | Follow-up service | COMPLETED | services/followup_service.py — apply_analysis, buckets, actions |
| 5 | Routes & app | COMPLETED | routes/leads.py, calls.py, dashboard.py, filters.py, main.py |
| 6 | Seed data | COMPLETED | seed.py — 15 leads, 13 processed, 2 left unprocessed |
| 7 | Backend verification | COMPLETED | summary, lists, filters, process flow, actions, error cases |
| 8 | Frontend scaffold | COMPLETED | package.json, vite config, main.jsx, App.jsx, api.js, index.css |
| 9 | Dashboard UI | COMPLETED | SummaryCards, FilterBar, LeadTable, Dashboard |
| 10 | Modals | COMPLETED | LeadDetailsModal, FollowUpActionModal |
| 11 | README & final pass | COMPLETED | README.md + all 20 acceptance criteria verified |

**All modules complete.**

## Log

- Tracker created.
- Module 1 COMPLETED: venv built, deps installed, config loads from .env, Mongo ping OK, indexes created.
- Module 2 COMPLETED: models + schemas round-trip; reason/reschedule validation rejects bad input.
- Module 3 COMPLETED: deterministic analyzer behind CallAnalyzer protocol; 9/9 cases pass (check_analyzer.py).
- Module 4 COMPLETED: apply_analysis (idempotent upsert), bucket derivation 10/10, follow-up actions with history.
- Module 5 COMPLETED: all spec endpoints registered (leads, calls, dashboard, health) + error handlers.
- Module 6 COMPLETED: 15 leads / 17 calls / 17 transcripts; 13 processed by the real analyzer, 2 left
  unprocessed. Buckets: 3 overdue, 2 due, 3 upcoming, 3 converted, 2 dropped.
- Module 7 COMPLETED: summary, both lists, combined filters (bd+bucket), process flow + idempotency
  (1 outcome doc), all 3 follow-up actions with reasons, 404/409/422 error cases.
- Module 8 COMPLETED: Vite + React scaffold, api.js wrapper, index.css design tokens.
- Module 9 COMPLETED: cards, filter bar, both tabs/tables verified in headless Chrome against the live API.
- Module 10 COMPLETED: details modal (info/call/follow-up/transcript/history) and action modal
  (Mark Done / Reschedule / Cancel, reason enforced) verified in-browser.
- Module 11 COMPLETED: README (13 sections) written; all 20 acceptance criteria re-verified on a fresh
  seed; production build clean; DB reseeded to demo state.

## Verified acceptance criteria (all 20)

| # | Criterion | Result |
|---|-----------|--------|
| 1 | MongoDB runs locally | ping ok=1 |
| 2 | Sample leads seeded | 15 leads |
| 3 | Calls + transcripts stored | 17 calls, 17 transcripts |
| 4 | Backend starts | /api/health → ok |
| 5 | Frontend starts | :5173 → HTTP 200 |
| 6 | Dashboard displays counts | 15 / 8 / 3 / 3 / 3 / 2 |
| 7 | Follow-up leads displayed separately | 8 leads, overdue first |
| 8 | Converted/dropped displayed separately | 7 leads in closed tab |
| 9 | Filters work | bd=Rahul + OVERDUE → L003, L002 |
| 10 | Clicking a lead opens a modal | verified in headless Chrome |
| 11 | Latest call info displayed | call id, date, duration |
| 12 | Latest transcript displayed | shown in modal |
| 13 | Follow-up date/time displayed | table + modal |
| 14 | POST /api/calls/{id}/process works | CALL016 → FOLLOW_UP_REQUIRED |
| 15 | Processing updates call_outcomes | 1 doc, analyzer=keyword-v1 |
| 16 | Processing updates lead status | L014 → FOLLOW_UP |
| 17 | Follow-up leads appear on dashboard | list 8 → 9 |
| 18 | Converted/dropped leave active list | L015 CONVERTED → closed tab |
| 19 | No LLM/API calls | 0 matches for network/LLM libs |
| 20 | Mongo config from .env | 0 hardcoded URLs in code |
