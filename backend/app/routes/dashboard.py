"""Dashboard summary and filter-option endpoints."""

from fastapi import APIRouter, Depends
from pymongo.database import Database

from app.database import CALLERS, CALLS, LEADS, get_db
from app.models.call import CallStatus, Outcome
from app.models.lead import FollowUpBucket, FollowUpStatus, LeadStatus
from app.models.schemas import DashboardSummary, FilterOptions
from app.routes.filters import LeadFilters, apply_bucket_filter, lead_filters
from app.services.followup_service import compute_bucket, is_ist_today, utcnow

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
            elif bucket is FollowUpBucket.UPCOMING:
                counts["upcoming"] += 1
            elif bucket is FollowUpBucket.UNSCHEDULED:
                counts["unscheduled"] += 1
            if _counts_as_due_today(follow_up, bucket, now):
                counts["due_today"] += 1

    # Calls with something to analyse that have not been analysed yet.
    # Deliberately excludes NOT_ANALYZABLE -- a not-connected, zero-duration
    # call is a finished state, not a backlog item. Not narrowed by the lead
    # filters: it is a pipeline indicator, not a lead count.
    unanalyzed = db[CALLS].count_documents(
        {
            "status": {
                "$nin": [CallStatus.COMPLETED.value, CallStatus.NOT_ANALYZABLE.value]
            },
            "$or": [{"has_transcript": True}, {"has_recording": True}],
        }
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
        "unanalyzed_calls": unanalyzed,
    }


def _counts_as_due_today(follow_up: dict, bucket: FollowUpBucket, now) -> bool:
    """Must match followup_alert_service, or the card and the email disagree.

    A DUE follow-up is inside the 2-hour window -- imminent by definition, so
    it counts even when it falls just past IST midnight. Everything else counts
    only on today's IST date. The previous version compared dates in UTC, which
    put anything after 18:30 IST on the wrong day.
    """
    if bucket is FollowUpBucket.DUE:
        return True
    if bucket in (FollowUpBucket.UPCOMING, FollowUpBucket.OVERDUE):
        return is_ist_today(follow_up.get("datetime"), now)
    return False


def _distinct_sorted(db: Database, field: str) -> list[str]:
    """Distinct non-empty values for a lead field.

    Every CRM field is nullable and most are null on the majority of leads, so
    filtering out blanks here is what keeps a dropdown from opening with an
    empty first row.
    """
    return sorted(str(v) for v in db[LEADS].distinct(field) if v not in (None, ""))


def _bd_options(db: Database) -> list[dict]:
    """BDs for the searchable owner dropdown, with lead counts.

    Keyed and filtered on email rather than name: there are 100+ distinct owners
    and duplicate first names are common in the real data, so the name alone
    cannot identify one. Owners that appear on a lead but have no caller record
    are still listed -- dropping them would silently hide their leads.
    """
    counts: dict[str, int] = {}
    for row in db[LEADS].aggregate([
        {"$match": {"owner_id": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$owner_id", "count": {"$sum": 1}}},
    ]):
        counts[row["_id"]] = row["count"]

    options: list[dict] = []
    seen: set[str] = set()
    for caller in db[CALLERS].find({}, sort=[("name", 1)]):
        caller_id = caller.get("caller_id")
        if not caller_id:
            continue
        seen.add(caller_id)
        options.append({
            "caller_id": caller_id,
            "name": caller.get("name") or caller_id,
            "email": caller.get("email"),
            "active": caller.get("active", True),
            "lead_count": counts.get(caller_id, 0),
        })

    # Owners present on leads but missing from the directory.
    for owner_id, count in counts.items():
        if owner_id in seen:
            continue
        lead = db[LEADS].find_one({"owner_id": owner_id}, {"owner_name": 1, "owner_email": 1})
        options.append({
            "caller_id": owner_id,
            "name": (lead or {}).get("owner_name") or owner_id,
            "email": (lead or {}).get("owner_email"),
            "active": True,
            "lead_count": count,
        })

    options.sort(key=lambda o: (o["name"] or "").lower())
    return options


@router.get("/filters", response_model=FilterOptions)
def get_filter_options(db: Database = Depends(get_db)) -> dict:
    """Populates the filter-bar dropdowns from the data that actually exists."""
    return {
        "bds": _bd_options(db),
        "products": _distinct_sorted(db, "product"),
        "stages": _distinct_sorted(db, "stage"),
        "lead_sources": _distinct_sorted(db, "lead_source"),
        "languages": _distinct_sorted(db, "language"),
        "states": _distinct_sorted(db, "state"),
        "segmentations": _distinct_sorted(db, "segmentation"),
        # Kept separate: in the real data last_sub_disposition_status mirrors
        # the stage, so merging the two would just duplicate the stage list.
        "dispositions": _distinct_sorted(db, "last_disposition_status"),
        "sentiments": _distinct_sorted(db, "latest_sentiment.label"),
        "sub_dispositions": _distinct_sorted(db, "last_sub_disposition_status"),
        "outcomes": [o.value for o in Outcome],
        "follow_up_statuses": [s.value for s in FollowUpStatus],
    }
