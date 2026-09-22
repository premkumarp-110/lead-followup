"""Dashboard summary and filter-option endpoints."""

from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.database import CALLS, LEADS, get_db
from app.models.call import CallStatus, Outcome
from app.models.lead import FollowUpBucket, FollowUpStatus, LeadStatus
from app.models.schemas import DashboardSummary, FilterOptions
from app.routes.filters import LeadFilters, apply_bucket_filter, lead_filters
from app.services.followup_service import compute_bucket, utcnow

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
def get_summary(
    filters: LeadFilters = Depends(lead_filters),
    db: Database = Depends(get_db),
) -> dict:
    """The six summary cards, honoring the same filters as the tables."""
    now = utcnow()
    docs = apply_bucket_filter(list(db[LEADS].find(filters.mongo_query())), filters, now)

    counts = {"follow_up": 0, "due_today": 0, "overdue": 0, "upcoming": 0, "unscheduled": 0}
    converted = dropped = 0

    for lead in docs:
        follow_up = lead.get("follow_up") or {}
        if lead.get("lead_status") == LeadStatus.CONVERTED.value:
            converted += 1
        elif lead.get("lead_status") == LeadStatus.DROPPED.value:
            dropped += 1

        if follow_up.get("required") and follow_up.get("status") == FollowUpStatus.PENDING.value:
            counts["follow_up"] += 1
            bucket = compute_bucket(follow_up, now)
            if bucket is FollowUpBucket.OVERDUE:
                counts["overdue"] += 1
                counts["due_today"] += 1 if _is_today(follow_up, now) else 0
            elif bucket is FollowUpBucket.DUE:
                counts["due_today"] += 1
            elif bucket is FollowUpBucket.UPCOMING:
                counts["upcoming"] += 1
                counts["due_today"] += 1 if _is_today(follow_up, now) else 0
            elif bucket is FollowUpBucket.UNSCHEDULED:
                counts["unscheduled"] += 1

    # Calls that were ingested but never finished processing. Not narrowed by
    # the lead filters -- it is a pipeline indicator, not a lead count.
    unprocessed = db[CALLS].count_documents(
        {"status": {"$nin": [CallStatus.COMPLETED.value]}}
    )

    return {
        "total_leads": len(docs),
        "follow_ups_required": counts["follow_up"],
        "due_today": counts["due_today"],
        "overdue": counts["overdue"],
        "converted": converted,
        "dropped": dropped,
        "upcoming": counts["upcoming"],
        "unscheduled": counts["unscheduled"],
        "unprocessed_calls": unprocessed,
    }


def _is_today(follow_up: dict, now) -> bool:
    when = follow_up.get("datetime")
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=now.tzinfo)
    return when.astimezone(now.tzinfo).date() == now.date()


@router.get("/filters", response_model=FilterOptions)
def get_filter_options(db: Database = Depends(get_db)) -> dict:
    """Populates the filter-bar dropdowns from the data that actually exists."""
    return {
        "bds": sorted(b for b in db[LEADS].distinct("assigned_bd.name") if b),
        "courses": sorted(c for c in db[LEADS].distinct("course") if c),
        "outcomes": [o.value for o in Outcome],
        "follow_up_statuses": [s.value for s in FollowUpStatus],
    }
