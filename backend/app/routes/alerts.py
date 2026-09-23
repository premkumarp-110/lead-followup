"""Follow-up reminder endpoints.

Routes stay thin: the grouping, rendering and delivery rules all live in
services/followup_alert_service.py.

Only POST /send is gated on FOLLOWUP_ALERTS_ENABLED. The reads stay open so an
operator can always inspect what *would* be sent, and so delivery history
produced while the feature was on remains visible after it is turned off --
the same rule routes/calls.py follows for the analyzer.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pymongo.database import Database

from app.config import settings
from app.database import CALLERS, FOLLOWUP_ALERTS, LEADS, get_db
from app.models.schemas import (
    AlertHistoryItem,
    AlertPendingResponse,
    AlertSendResponse,
)
from app.services import followup_alert_service as alerts
from app.services.followup_service import decorate_lead, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["alerts"])

HISTORY_LIMIT_DEFAULT = 50
HISTORY_LIMIT_MAX = 200

# NOTE: `bd` here is a caller_id (e.g. "BD001"), NOT the name-or-id that
# routes/filters.py's LeadFilters.bd accepts. Reminders are addressed to a
# specific mailbox, so the id is the only unambiguous key.
_BD_PARAM = Query(None, description="Caller id. Omit for every BD.")
_LEAD_PARAM = Query(None, description="Lead id. Previews the one-lead reminder instead.")


def _require_alerts_enabled() -> None:
    """Guard for the one endpoint that actually sends email."""
    if not settings.followup_alerts_enabled:
        raise HTTPException(
            status_code=403,
            detail="Follow-up reminders are disabled (FOLLOWUP_ALERTS_ENABLED is not set to "
            "true in backend/.env).",
        )


def _require_email_configured() -> None:
    error = settings.email_config_error()
    if error:
        # 503, matching how an unconfigured Vertex is reported: the request is
        # valid, the server just cannot fulfil it yet.
        raise HTTPException(status_code=503, detail=error)


def _last_alert(db: Database, bd_id: str) -> dict | None:
    return db[FOLLOWUP_ALERTS].find_one({"bd_id": bd_id}, sort=[("sent_at", -1)])


def _as_items(leads: list[dict]) -> list[dict]:
    return [
        {
            "lead_id": lead.get("lead_id"),
            "product": lead.get("product"),
            "stage": lead.get("stage"),
            "owner_name": lead.get("owner_name"),
            "follow_up": lead.get("follow_up"),
            "latest_outcome": lead.get("latest_outcome"),
        }
        for lead in leads
    ]


@router.get("/pending", response_model=AlertPendingResponse)
def get_pending(
    bd: str | None = _BD_PARAM,
    db: Database = Depends(get_db),
) -> dict:
    """What each BD currently owes. Never gated -- this is a read.

    An unknown `bd` returns an empty result, not a 404: it is a filter over
    leads, not a lookup of a resource.
    """
    now = utcnow()
    grouped = alerts.pending_leads_by_bd(db, now)

    unassigned = grouped.pop(alerts.UNASSIGNED_KEY, None)
    unassigned_count = 0
    if unassigned:
        unassigned_count = len(unassigned["overdue"]) + len(unassigned["due_today"])

    if bd:
        grouped = {bd: grouped[bd]} if bd in grouped else {}

    groups = []
    for bd_id, group in grouped.items():
        if not (group["overdue"] or group["due_today"] or group["unscheduled_count"]):
            continue
        caller = db[CALLERS].find_one({"caller_id": bd_id})
        last = _last_alert(db, bd_id)
        groups.append(
            {
                "bd_id": bd_id,
                "bd_name": (caller or {}).get("name") or group.get("bd_name") or bd_id,
                "bd_email": (caller or {}).get("email"),
                "overdue": _as_items(group["overdue"]),
                "due_today": _as_items(group["due_today"]),
                "unscheduled_count": group["unscheduled_count"],
                "last_alert_at": (last or {}).get("sent_at"),
                "last_alert_status": (last or {}).get("status"),
            }
        )

    # Most urgent BD first, so the panel leads with who is furthest behind.
    groups.sort(key=lambda g: (-len(g["overdue"]), -len(g["due_today"]), g["bd_name"]))

    return {
        "groups": groups,
        "total_overdue": sum(len(g["overdue"]) for g in groups),
        "total_due_today": sum(len(g["due_today"]) for g in groups),
        "total_unscheduled": sum(g["unscheduled_count"] for g in groups),
        "unassigned_count": unassigned_count,
        "orphaned_count": alerts.count_orphaned_followups(db),
        "generated_at": now,
    }


@router.get("/preview", response_class=HTMLResponse)
def preview_digest(
    bd: str | None = _BD_PARAM,
    lead: str | None = _LEAD_PARAM,
    db: Database = Depends(get_db),
) -> HTMLResponse:
    """The exact HTML that would be emailed. Sends nothing, needs no SMTP.

    This is the safe way to check the template -- including on a machine with
    no mail configuration at all. `lead` previews the single-lead reminder,
    which is a different message from the daily digest.
    """
    now = utcnow()

    if lead:
        doc = db[LEADS].find_one({"lead_id": lead})
        if doc is None:
            raise HTTPException(status_code=404, detail=f"Lead '{lead}' was not found.")
        decorated = decorate_lead(doc, now)
        return HTMLResponse(
            alerts.render_lead_reminder(decorated, decorated.get("owner_name") or "", now)
        )

    grouped = alerts.pending_leads_by_bd(db, now)

    if bd:
        group = grouped.get(bd) or alerts._empty_group()
        caller = db[CALLERS].find_one({"caller_id": bd})
        if caller and caller.get("name"):
            group = {**group, "bd_name": caller["name"]}
        return HTMLResponse(alerts.render_digest(group, now))

    sections = []
    for bd_id, group in sorted(grouped.items()):
        if not alerts.has_anything_to_send(group):
            continue
        sections.append(
            f"<hr><p style='color:#777;font-size:12px'>To: {bd_id}</p>"
            + alerts.render_digest(group, now)
        )
    if not sections:
        return HTMLResponse(alerts.render_digest(alerts._empty_group(), now))
    return HTMLResponse("".join(sections))


@router.post("/send", response_model=AlertSendResponse)
def send_digests(
    payload: dict = Body(default_factory=dict),
    db: Database = Depends(get_db),
) -> dict:
    """Send a reminder now, outside the daily schedule.

    Three scopes, narrowest wins:

      {"lead_id": "..."}  one lead, to whoever owns it -- works even when the
                          follow-up is merely upcoming or has no date at all,
                          neither of which the daily digest would include
      {"bd_id":   "..."}  one BD's overdue + due-today digest
      {}                  every BD with something pending

    Manual sends deliberately skip the once-a-day dedupe that scheduled runs
    use: a button that silently does nothing is worse than a second email.

    Sending to every BD makes one blocking SMTP call per BD, so this request
    can take a while on a large team.
    """
    _require_alerts_enabled()
    _require_email_configured()

    lead_id = (payload or {}).get("lead_id") or None
    bd_id = (payload or {}).get("bd_id") or None

    if lead_id:
        try:
            summary = alerts.send_lead_alert(db, lead_id)
        except alerts.LeadAlertError as exc:
            # Nothing to remind about is the caller's mistake, not a failure to
            # report in the delivery log.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        logger.info("Lead reminder run for %s: %s", lead_id, summary["message"])
        return summary

    summary = alerts.send_pending_alerts(db, trigger=alerts.TRIGGER_MANUAL, bd_id=bd_id)
    logger.info(
        "Manual reminder run: %d sent, %d skipped, %d failed.",
        summary["sent"],
        summary["skipped"],
        summary["failed"],
    )
    return summary


@router.get("/history", response_model=list[AlertHistoryItem])
def get_history(
    bd: str | None = _BD_PARAM,
    limit: int = Query(HISTORY_LIMIT_DEFAULT, ge=1, le=HISTORY_LIMIT_MAX),
    db: Database = Depends(get_db),
) -> list[dict]:
    """Delivery log, newest first. Never gated -- this is a read."""
    query = {"bd_id": bd} if bd else {}
    docs = db[FOLLOWUP_ALERTS].find(query).sort("sent_at", -1).limit(limit)
    return [{k: v for k, v in doc.items() if k != "_id"} for doc in docs]
