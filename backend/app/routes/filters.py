"""Shared lead filtering, used identically by the leads, dashboard and insights routes.

One definition means filters always AND together the same way everywhere.
Invalid enum values are rejected by FastAPI/Pydantic as 422s automatically.

The filter set mirrors the Lead Call API's own documented Lead filters, because
every CRM field is stored locally and filtering is driven from that data alone.
List-style filters ("any of these") accept a comma-separated value.

**Two-phase, and both halves are required.** `mongo_query(base)` covers what
Mongo can evaluate; `apply_bucket_filter(docs, filters, now)` covers the derived
follow-up bucket, which is computed per read and never persisted. A new list
endpoint that calls only the first half silently ignores `?bucket=`.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from fastapi import Query

from app.models.call import Outcome
from app.models.lead import FollowUpBucket, FollowUpStatus
from app.services.followup_service import compute_bucket, utcnow

# Alias: LeadFilters has a field named "date", which would otherwise shadow
# the date class inside the class body and the function signature below.
DateOnly = date


def _split_list(value: str | None) -> list[str]:
    """Comma-separated filter value -> list. 'any of these' semantics."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _any_of(field_name: str, values: list[str]) -> dict:
    """Case-insensitive exact match against any of `values`."""
    if len(values) == 1:
        return {field_name: {"$regex": f"^{re.escape(values[0])}$", "$options": "i"}}
    return {
        "$or": [
            {field_name: {"$regex": f"^{re.escape(value)}$", "$options": "i"}}
            for value in values
        ]
    }


def _range_clause(minimum: float | int | None, maximum: float | int | None) -> dict | None:
    bounds: dict = {}
    if minimum is not None:
        bounds["$gte"] = minimum
    if maximum is not None:
        bounds["$lte"] = maximum
    return bounds or None


def _date_range_clause(start: DateOnly | None, end: DateOnly | None) -> dict | None:
    bounds: dict = {}
    if start is not None:
        bounds["$gte"] = datetime.combine(start, time.min, tzinfo=timezone.utc)
    if end is not None:
        bounds["$lte"] = datetime.combine(end, time.max, tzinfo=timezone.utc)
    return bounds or None


@dataclass
class LeadFilters:
    # ---- Identity ----------------------------------------------------------
    # There is no name/phone/email in the CRM, so search is an id lookup.
    search: str | None = None

    # ---- Ownership ---------------------------------------------------------
    # Matched on owner_email or owner_id, never owner_name: there are 100+
    # distinct owners and first names collide, while emails are unique.
    bd: str | None = None

    # ---- Attributes --------------------------------------------------------
    product: str | None = None
    stage: str | None = None
    previous_stage: str | None = None
    language: str | None = None
    segmentation: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None

    # ---- Source / marketing attribution ------------------------------------
    lead_source: str | None = None
    source_campaign: str | None = None
    source_medium: str | None = None

    # ---- Call activity -----------------------------------------------------
    last_disposition_status: str | None = None
    last_sub_disposition_status: str | None = None
    # How the lead sounded on the winning analysis. Ours, not the CRM's.
    sentiment: str | None = None
    attempts_min: int | None = None
    attempts_max: int | None = None
    connected_min: int | None = None
    connected_max: int | None = None
    win_probability_min: float | None = None
    win_probability_max: float | None = None

    # ---- CRM dates ---------------------------------------------------------
    created_from: DateOnly | None = None
    created_to: DateOnly | None = None
    updated_from: DateOnly | None = None
    updated_to: DateOnly | None = None
    first_contact_from: DateOnly | None = None
    first_contact_to: DateOnly | None = None
    last_call_from: DateOnly | None = None
    last_call_to: DateOnly | None = None

    # ---- Ours: derived from call analysis ----------------------------------
    outcome: Outcome | None = None
    status: FollowUpStatus | None = None
    date: DateOnly | None = None
    date_from: DateOnly | None = None
    date_to: DateOnly | None = None
    bucket: str | None = None

    # Maps a filter attribute to the lead field it matches, for the plain
    # "any of these" string filters. Keeps mongo_query from being 20 ifs.
    _LIST_FILTERS = (
        ("product", "product"),
        ("stage", "stage"),
        ("previous_stage", "previous_stage"),
        ("language", "language"),
        ("segmentation", "segmentation"),
        ("city", "city"),
        ("state", "state"),
        ("country", "country"),
        ("lead_source", "lead_source"),
        ("source_campaign", "source_campaign"),
        ("source_medium", "source_medium"),
        ("last_disposition_status", "last_disposition_status"),
        ("last_sub_disposition_status", "last_sub_disposition_status"),
        ("sentiment", "latest_sentiment.label"),
    )

    _RANGE_FILTERS = (
        ("total_attempts", "attempts_min", "attempts_max"),
        ("calls_connected", "connected_min", "connected_max"),
        ("win_probability", "win_probability_min", "win_probability_max"),
    )

    _DATE_FILTERS = (
        ("created_at", "created_from", "created_to"),
        ("updated_at", "updated_from", "updated_to"),
        ("first_contact_date", "first_contact_from", "first_contact_to"),
        ("last_call_attempted_at", "last_call_from", "last_call_to"),
    )

    def mongo_query(self, base: dict | None = None) -> dict:
        """The part of the filter that MongoDB can evaluate directly."""
        query: dict = dict(base or {})
        clauses: list[dict] = []

        # Leads have no name/phone/email. Search is an id prefix match across
        # the two identifiers the CRM does expose.
        if self.search:
            pattern = re.escape(self.search.strip())
            clauses.append(
                {
                    "$or": [
                        {"lead_id": {"$regex": pattern, "$options": "i"}},
                        {"external_id": {"$regex": pattern, "$options": "i"}},
                    ]
                }
            )

        if self.bd:
            values = _split_list(self.bd)
            clauses.append(
                {
                    "$or": [
                        _any_of("owner_email", values),
                        {"owner_id": {"$in": values}},
                    ]
                }
            )

        for attr, field_name in self._LIST_FILTERS:
            values = _split_list(getattr(self, attr))
            if values:
                clauses.append(_any_of(field_name, values))

        for field_name, min_attr, max_attr in self._RANGE_FILTERS:
            clause = _range_clause(getattr(self, min_attr), getattr(self, max_attr))
            if clause:
                clauses.append({field_name: clause})

        for field_name, from_attr, to_attr in self._DATE_FILTERS:
            clause = _date_range_clause(getattr(self, from_attr), getattr(self, to_attr))
            if clause:
                clauses.append({field_name: clause})

        if self.outcome:
            clauses.append({"latest_outcome": self.outcome.value})

        # CONVERTED / DROPPED quick filters select on lead_status, which Mongo
        # can evaluate directly; the other bucket values are derived in Python.
        quick = (self.bucket or "").strip().upper()
        if quick in {"CONVERTED", "DROPPED"}:
            clauses.append({"lead_status": quick})

        # OVERDUE is a derived bucket, not a stored status -- route it there.
        if self.status and self.status is not FollowUpStatus.OVERDUE:
            clauses.append({"follow_up.status": self.status.value})

        follow_up_dates = self._follow_up_date_clause()
        if follow_up_dates:
            clauses.append({"follow_up.datetime": follow_up_dates})

        if clauses:
            query["$and"] = query.get("$and", []) + clauses
        return query

    def _follow_up_date_clause(self) -> dict | None:
        if self.date:
            return {
                "$gte": datetime.combine(self.date, time.min, tzinfo=timezone.utc),
                "$lte": datetime.combine(self.date, time.max, tzinfo=timezone.utc),
            }
        return _date_range_clause(self.date_from, self.date_to)

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
                "UNSCHEDULED": {FollowUpBucket.UNSCHEDULED.value},
                "COMPLETED": {FollowUpBucket.COMPLETED.value},
                "CANCELLED": {FollowUpBucket.CANCELLED.value},
                # Outcome-style quick filters: these leads have no follow-up
                # bucket, so they are matched on lead_status instead (see
                # `mongo_query`). Listed here so the value validates.
                "CONVERTED": set(),
                "DROPPED": set(),
            }
            if raw not in mapping:
                # Mirrors Pydantic's 422 style for an unknown enum value.
                raise ValueError(
                    f"Invalid bucket '{self.bucket}'. Expected one of: ALL, OVERDUE, DUE, "
                    "DUE_TODAY, UPCOMING, UNSCHEDULED, COMPLETED, CANCELLED, CONVERTED, DROPPED."
                )
            wanted |= mapping[raw]

        if self.status is FollowUpStatus.OVERDUE:
            wanted = wanted & {FollowUpBucket.OVERDUE.value} if wanted else {FollowUpBucket.OVERDUE.value}

        return wanted or None


def lead_filters(
    search: str | None = Query(None, description="Matches lead id or external id"),
    bd: str | None = Query(None, description="Owner email or owner id (comma-separated for any-of)"),
    product: str | None = Query(None, description="Comma-separated for any-of"),
    stage: str | None = Query(None, description="CRM stage, e.g. 'Follow-up 1'"),
    previous_stage: str | None = Query(None),
    language: str | None = Query(None),
    segmentation: str | None = Query(None, description="e.g. M1"),
    city: str | None = Query(None),
    state: str | None = Query(None),
    country: str | None = Query(None),
    lead_source: str | None = Query(None),
    source_campaign: str | None = Query(None),
    source_medium: str | None = Query(None),
    last_disposition_status: str | None = Query(None),
    last_sub_disposition_status: str | None = Query(None),
    sentiment: str | None = Query(
        None, description="POSITIVE | NEUTRAL | NEGATIVE | MIXED | UNKNOWN (comma-separated)"
    ),
    attempts_min: int | None = Query(None, ge=0),
    attempts_max: int | None = Query(None, ge=0),
    connected_min: int | None = Query(None, ge=0),
    connected_max: int | None = Query(None, ge=0),
    win_probability_min: float | None = Query(None, ge=0, le=100),
    win_probability_max: float | None = Query(None, ge=0, le=100),
    created_from: DateOnly | None = Query(None),
    created_to: DateOnly | None = Query(None),
    updated_from: DateOnly | None = Query(None),
    updated_to: DateOnly | None = Query(None),
    first_contact_from: DateOnly | None = Query(None),
    first_contact_to: DateOnly | None = Query(None),
    last_call_from: DateOnly | None = Query(None),
    last_call_to: DateOnly | None = Query(None),
    outcome: Outcome | None = Query(None, description="Latest call outcome"),
    status: FollowUpStatus | None = Query(None, description="Follow-up status"),
    date: DateOnly | None = Query(None, description="Exact follow-up date (YYYY-MM-DD)"),
    date_from: DateOnly | None = Query(None),
    date_to: DateOnly | None = Query(None),
    bucket: str | None = Query(
        None,
        description="ALL | OVERDUE | DUE | DUE_TODAY | UPCOMING | UNSCHEDULED | COMPLETED | "
        "CANCELLED | CONVERTED | DROPPED",
    ),
) -> LeadFilters:
    return LeadFilters(
        search=search,
        bd=bd,
        product=product,
        stage=stage,
        previous_stage=previous_stage,
        language=language,
        segmentation=segmentation,
        city=city,
        state=state,
        country=country,
        lead_source=lead_source,
        source_campaign=source_campaign,
        source_medium=source_medium,
        last_disposition_status=last_disposition_status,
        last_sub_disposition_status=last_sub_disposition_status,
        sentiment=sentiment,
        attempts_min=attempts_min,
        attempts_max=attempts_max,
        connected_min=connected_min,
        connected_max=connected_max,
        win_probability_min=win_probability_min,
        win_probability_max=win_probability_max,
        created_from=created_from,
        created_to=created_to,
        updated_from=updated_from,
        updated_to=updated_to,
        first_contact_from=first_contact_from,
        first_contact_to=first_contact_to,
        last_call_from=last_call_from,
        last_call_to=last_call_to,
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
