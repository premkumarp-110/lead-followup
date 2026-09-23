"""Aggregate views: queue health for analysts, pipeline health for operators.

Everything lead-scoped is computed in Python from ONE filtered read, the same
shape routes/dashboard.py uses. That is deliberate: these numbers sit next to
the tables in the UI, and the only way to guarantee they reconcile is to count
the same candidate set, through the same filter dependency, against the same
`now`. An aggregation pipeline would be faster and would drift.

Scale is the known trade-off -- this scans the filtered lead set. Correct at
current volumes; see PRODUCTION-DATA-ANALYSIS.md section 5.7 for the large-CRM case.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.database import CALL_ANALYSES, CALLERS, CALLS, LEADS, get_db
from app.models.call import CallStatus, Outcome
from app.models.lead import FollowUpAction, FollowUpBucket, FollowUpStatus, LeadStatus
from app.models.schemas import InsightsByBD, InsightsOverview
from app.routes.filters import LeadFilters, apply_bucket_filter, lead_filters
from app.services.followup_alert_service import UNASSIGNED_KEY
from app.services.followup_service import compute_bucket, is_ist_today, utcnow

router = APIRouter(prefix="/api/insights", tags=["insights"])

# Half-open, so no lead can land in two bands and none can fall between them.
# Labels live here rather than in the UI so every client says the same thing.
AGEING_BANDS = [
    ("under_1d", "< 1 day", timedelta(0), timedelta(days=1)),
    ("d1_3", "1-3 days", timedelta(days=1), timedelta(days=4)),
    ("d4_6", "4-6 days", timedelta(days=4), timedelta(days=7)),
    ("over_7d", "7+ days", timedelta(days=7), None),
]


def _filtered_leads(db: Database, filters: LeadFilters, now: datetime) -> list[dict]:
    """Both halves of the filter contract, always.

    mongo_query() alone silently ignores ?bucket=, because buckets are derived
    at read time and Mongo cannot evaluate them.
    """
    return apply_bucket_filter(list(db[LEADS].find(filters.mongo_query())), filters, now)


def _bucket_of(lead: dict, now: datetime) -> FollowUpBucket:
    return compute_bucket(lead.get("follow_up") or {}, now)


def _is_pending(lead: dict) -> bool:
    follow_up = lead.get("follow_up") or {}
    return bool(follow_up.get("required")) and follow_up.get("status") == FollowUpStatus.PENDING.value


def _counts_as_due_today(lead: dict, bucket: FollowUpBucket, now: datetime) -> bool:
    """Same rule as the reminder digest, so the card and the email agree.

    A DUE follow-up is inside the 2-hour window -- imminent by definition, and
    included even when it falls after IST midnight. Anything else counts only
    when it lands on today's IST date.
    """
    if bucket is FollowUpBucket.DUE:
        return True
    if bucket in (FollowUpBucket.UPCOMING, FollowUpBucket.OVERDUE):
        return is_ist_today((lead.get("follow_up") or {}).get("datetime"), now)
    return False


@router.get("/overview", response_model=InsightsOverview)
def get_overview(
    filters: LeadFilters = Depends(lead_filters),
    db: Database = Depends(get_db),
) -> dict:
    """Queue health: how much is pending, how late it is, and how it ended up."""
    now = utcnow()
    leads = _filtered_leads(db, filters, now)

    totals = {"pending": 0, "overdue": 0, "due": 0, "due_today": 0, "upcoming": 0, "unscheduled": 0}
    unassigned = 0
    mix = {"converted": 0, "dropped": 0, "follow_up_required": 0, "not_analyzed": 0}
    tone = {"positive": 0, "neutral": 0, "negative": 0, "mixed": 0,
            "not_assessed": 0, "declining": 0}
    ageing = {key: 0 for key, _, _, _ in AGEING_BANDS}

    for lead in leads:
        if not (lead.get("owner_id") or "").strip():
            unassigned += 1

        outcome = lead.get("latest_outcome")
        if outcome == Outcome.CONVERTED.value:
            mix["converted"] += 1
        elif outcome == Outcome.DROPPED.value:
            mix["dropped"] += 1
        elif outcome == Outcome.FOLLOW_UP_REQUIRED.value:
            mix["follow_up_required"] += 1
        else:
            mix["not_analyzed"] += 1

        sentiment = lead.get("latest_sentiment") or {}
        label = (sentiment.get("label") or "UNKNOWN").lower()
        tone["not_assessed" if label == "unknown" else label] = (
            tone.get("not_assessed" if label == "unknown" else label, 0) + 1
        )
        # Counted separately because it OVERLAPS the labels: a declining call
        # can have averaged out to neutral, and that is the point of tracking it.
        if (sentiment.get("trajectory") or "") == "DECLINED":
            tone["declining"] += 1

        if not _is_pending(lead):
            continue

        totals["pending"] += 1
        bucket = _bucket_of(lead, now)
        if bucket is FollowUpBucket.OVERDUE:
            totals["overdue"] += 1
            when = (lead.get("follow_up") or {}).get("datetime")
            if when is not None:
                late = now - when
                for key, _label, low, high in AGEING_BANDS:
                    if late >= low and (high is None or late < high):
                        ageing[key] += 1
                        break
        elif bucket is FollowUpBucket.DUE:
            totals["due"] += 1
        elif bucket is FollowUpBucket.UPCOMING:
            totals["upcoming"] += 1
        elif bucket is FollowUpBucket.UNSCHEDULED:
            totals["unscheduled"] += 1

        if _counts_as_due_today(lead, bucket, now):
            totals["due_today"] += 1

    # Analyses produced by the keyword fallback rather than the LLM. Not
    # narrowed by the lead filters -- it is a pipeline indicator. Worth showing
    # because a degraded result is still written to the lead.
    degraded = db[CALL_ANALYSES].count_documents({"degraded": True})
    unanalyzed = db[CALLS].count_documents(
        {
            "status": {"$nin": [CallStatus.COMPLETED.value, CallStatus.NOT_ANALYZABLE.value]},
            "$or": [{"has_transcript": True}, {"has_recording": True}],
        }
    )

    return {
        "degraded_analyses": degraded,
        "unanalyzed_calls": unanalyzed,
        "total_leads": len(leads),
        "pending_total": totals["pending"],
        "overdue_total": totals["overdue"],
        "due_total": totals["due"],
        "due_today_total": totals["due_today"],
        "upcoming_total": totals["upcoming"],
        "unscheduled_total": totals["unscheduled"],
        "unassigned_total": unassigned,
        "ageing": [
            {"key": key, "label": label, "count": ageing[key]}
            for key, label, _low, _high in AGEING_BANDS
        ],
        "outcome_mix": {**mix, "total": len(leads)},
        "sentiment_mix": {**tone, "total": len(leads)},
        "generated_at": now,
    }


@router.get("/by-bd", response_model=InsightsByBD)
def get_by_bd(
    filters: LeadFilters = Depends(lead_filters),
    db: Database = Depends(get_db),
) -> dict:
    """Per-BD workload.

    Leads with no assigned BD get their own row rather than being dropped --
    otherwise the columns stop summing to the total and the table quietly lies.
    `completed`/`cancelled` are all-time counts from the lead's own
    follow_up_history, which is the only durable record of actions taken.
    """
    now = utcnow()
    leads = _filtered_leads(db, filters, now)

    rows: dict[str, dict] = {}

    def row_for(bd_id: str, bd_name: str) -> dict:
        if bd_id not in rows:
            rows[bd_id] = {
                "bd_id": bd_id,
                "bd_name": bd_name,
                "bd_email": None,
                "active": True,
                "orphaned": False,
                "assigned": 0,
                "overdue": 0,
                "due": 0,
                "due_today": 0,
                "upcoming": 0,
                "unscheduled": 0,
                "rescheduled_all_time": 0,
                "converted": 0,
                "dropped": 0,
            }
        return rows[bd_id]

    for lead in leads:
        owner_name = lead.get("owner_name") or ""
        bd_id = (lead.get("owner_id") or "").strip() or UNASSIGNED_KEY
        row = row_for(bd_id, owner_name)
        if not row["bd_name"] and owner_name:
            row["bd_name"] = owner_name

        row["assigned"] += 1

        if lead.get("lead_status") == LeadStatus.CONVERTED.value:
            row["converted"] += 1
        elif lead.get("lead_status") == LeadStatus.DROPPED.value:
            row["dropped"] += 1

        # Reschedules are the only action a BD can still take, and repeated
        # ones are a real signal -- a lead pushed four times is a lead being
        # avoided. (completed/cancelled counters were dropped: both actions are
        # retired, so those columns were frozen at zero on any new database.)
        for entry in lead.get("follow_up_history") or []:
            if entry.get("action") == FollowUpAction.RESCHEDULE.value:
                row["rescheduled_all_time"] += 1

        if not _is_pending(lead):
            continue

        bucket = _bucket_of(lead, now)
        if bucket is FollowUpBucket.OVERDUE:
            row["overdue"] += 1
        elif bucket is FollowUpBucket.DUE:
            row["due"] += 1
        elif bucket is FollowUpBucket.UPCOMING:
            row["upcoming"] += 1
        elif bucket is FollowUpBucket.UNSCHEDULED:
            row["unscheduled"] += 1
        if _counts_as_due_today(lead, bucket, now):
            row["due_today"] += 1

    # Attach the caller record. A missing one means the BD was deleted while
    # their leads stayed behind: keep the row, flag it, don't silently rename it.
    for bd_id, row in rows.items():
        if bd_id == UNASSIGNED_KEY:
            row["bd_name"] = "Unassigned"
            row["orphaned"] = True
            continue
        caller = db[CALLERS].find_one({"caller_id": bd_id})
        if caller is None:
            row["orphaned"] = True
            row["bd_name"] = row["bd_name"] or bd_id
        else:
            row["bd_name"] = caller.get("name") or row["bd_name"] or bd_id
            row["bd_email"] = caller.get("email")
            row["active"] = caller.get("active") is not False

    ordered = sorted(
        rows.values(),
        key=lambda r: (-r["overdue"], -r["due_today"], -r["assigned"], r["bd_name"]),
    )
    return {"rows": ordered, "total_leads": len(leads), "generated_at": now}
