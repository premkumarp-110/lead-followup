"""Shared lead filtering, used identically by the leads and dashboard routes.

One definition means filters always AND together the same way everywhere.
Invalid enum values are rejected by FastAPI/Pydantic as 422s automatically.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from fastapi import Query

from app.models.call import Outcome
from app.models.lead import FollowUpBucket, FollowUpStatus
from app.services.followup_service import compute_bucket, utcnow

# Alias: LeadFilters has a field named "date", which would otherwise shadow
# the date class inside the class body and the function signature below.
DateOnly = date


@dataclass
class LeadFilters:
    search: str | None = None
    bd: str | None = None
    course: str | None = None
    outcome: Outcome | None = None
    status: FollowUpStatus | None = None
    date: DateOnly | None = None
    date_from: DateOnly | None = None
    date_to: DateOnly | None = None
    bucket: str | None = None

    def mongo_query(self, base: dict | None = None) -> dict:
        """The part of the filter that MongoDB can evaluate directly."""
        query: dict = dict(base or {})
        clauses: list[dict] = []

        if self.search:
            pattern = re.escape(self.search.strip())
            clauses.append(
                {
                    "$or": [
                        {"name": {"$regex": pattern, "$options": "i"}},
                        {"email": {"$regex": pattern, "$options": "i"}},
                        {"phone": {"$regex": pattern, "$options": "i"}},
                        {"lead_id": {"$regex": pattern, "$options": "i"}},
                    ]
                }
            )
        if self.bd:
            clauses.append(
                {
                    "$or": [
                        {"assigned_bd.name": {"$regex": f"^{re.escape(self.bd)}$", "$options": "i"}},
                        {"assigned_bd.id": self.bd},
                    ]
                }
            )
        if self.course:
            clauses.append({"course": {"$regex": f"^{re.escape(self.course)}$", "$options": "i"}})
        if self.outcome:
            clauses.append({"latest_outcome": self.outcome.value})

        # OVERDUE is a derived bucket, not a stored status -- route it there.
        if self.status and self.status is not FollowUpStatus.OVERDUE:
            clauses.append({"follow_up.status": self.status.value})

        date_clause = self._date_clause()
        if date_clause:
            clauses.append({"follow_up.datetime": date_clause})

        if clauses:
            query["$and"] = query.get("$and", []) + clauses
        return query

    def _date_clause(self) -> dict | None:
        bounds: dict = {}
        if self.date:
            bounds["$gte"] = datetime.combine(self.date, time.min, tzinfo=timezone.utc)
            bounds["$lte"] = datetime.combine(self.date, time.max, tzinfo=timezone.utc)
            return bounds
        if self.date_from:
            bounds["$gte"] = datetime.combine(self.date_from, time.min, tzinfo=timezone.utc)
        if self.date_to:
            bounds["$lte"] = datetime.combine(self.date_to, time.max, tzinfo=timezone.utc)
        return bounds or None

    def wanted_buckets(self) -> set[str] | None:
        """Which derived buckets pass, or None when the filter doesn't care."""
        wanted: set[str] = set()

        raw = (self.bucket or "").strip().upper()
        if raw and raw != "ALL":
            mapping = {
                "OVERDUE": {FollowUpBucket.OVERDUE.value},
                "DUE": {FollowUpBucket.DUE.value},
                "DUE_TODAY": {FollowUpBucket.DUE.value, FollowUpBucket.OVERDUE.value},
                "TODAY": {FollowUpBucket.DUE.value, FollowUpBucket.OVERDUE.value},
                "UPCOMING": {FollowUpBucket.UPCOMING.value},
                "COMPLETED": {FollowUpBucket.COMPLETED.value},
                "CANCELLED": {FollowUpBucket.CANCELLED.value},
            }
            if raw not in mapping:
                # Mirrors Pydantic's 422 style for an unknown enum value.
                raise ValueError(
                    f"Invalid bucket '{self.bucket}'. Expected one of: "
                    "ALL, OVERDUE, DUE, DUE_TODAY, UPCOMING, COMPLETED, CANCELLED."
                )
            wanted |= mapping[raw]

        if self.status is FollowUpStatus.OVERDUE:
            wanted = wanted & {FollowUpBucket.OVERDUE.value} if wanted else {FollowUpBucket.OVERDUE.value}

        return wanted or None


def lead_filters(
    search: str | None = Query(None, description="Matches lead name, email, phone or id"),
    bd: str | None = Query(None, description="Assigned BD name or id"),
    course: str | None = Query(None),
    outcome: Outcome | None = Query(None, description="Latest call outcome"),
    status: FollowUpStatus | None = Query(None, description="Follow-up status"),
    date: DateOnly | None = Query(None, description="Exact follow-up date (YYYY-MM-DD)"),
    date_from: DateOnly | None = Query(None),
    date_to: DateOnly | None = Query(None),
    bucket: str | None = Query(
        None, description="ALL | OVERDUE | DUE | DUE_TODAY | UPCOMING | COMPLETED | CANCELLED"
    ),
) -> LeadFilters:
    return LeadFilters(
        search=search,
        bd=bd,
        course=course,
        outcome=outcome,
        status=status,
        date=date,
        date_from=date_from,
        date_to=date_to,
        bucket=bucket,
    )


def apply_bucket_filter(leads: list[dict], filters: LeadFilters, now: datetime | None = None) -> list[dict]:
    """Filter on the derived bucket, which Mongo cannot evaluate itself."""
    wanted = filters.wanted_buckets()
    if not wanted:
        return leads
    now = now or utcnow()
    return [lead for lead in leads if compute_bucket(lead.get("follow_up"), now).value in wanted]
