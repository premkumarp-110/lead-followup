"""Daily email digest: alert each BD about their currently OVERDUE leads.

Buckets are derived, never stored (see followup_service.compute_bucket), so
this recomputes the current OVERDUE set fresh on every run -- there is no
per-lead "already alerted" state to track since it only runs once a day.
"""

import logging
from datetime import datetime

from pymongo.database import Database

from app.config import settings
from app.database import CALLERS, LEADS
from app.models.lead import FollowUpBucket, FollowUpStatus
from app.services import email_service
from app.services.followup_service import decorate_lead, utcnow

logger = logging.getLogger("app")

# Same query as routes/leads.py's ACTIVE_FOLLOW_UP_QUERY: a lead is only a
# candidate while its follow-up is required AND still pending. Kept here
# rather than imported so services never depend on routes.
ACTIVE_FOLLOW_UP_QUERY = {
    "follow_up.required": True,
    "follow_up.status": FollowUpStatus.PENDING.value,
}


def overdue_leads_by_bd(db: Database, now: datetime | None = None) -> dict[str, list[dict]]:
    """Currently OVERDUE leads, grouped by assigned_bd.id."""
    now = now or utcnow()
    docs = db[LEADS].find(ACTIVE_FOLLOW_UP_QUERY)
    grouped: dict[str, list[dict]] = {}
    for doc in docs:
        lead = decorate_lead(doc, now)
        if lead["follow_up"].get("bucket") != FollowUpBucket.OVERDUE.value:
            continue
        bd_id = lead.get("assigned_bd", {}).get("id")
        if not bd_id:
            continue
        grouped.setdefault(bd_id, []).append(lead)
    return grouped


def _render_digest(bd_name: str, leads: list[dict]) -> str:
    rows = "".join(
        f"<tr>"
        f"<td>{lead['name']}</td>"
        f"<td>{lead['phone']}</td>"
        f"<td>{lead['course']}</td>"
        f"<td>{lead['follow_up'].get('date') or 'unscheduled'} {lead['follow_up'].get('time') or ''}</td>"
        f"</tr>"
        for lead in leads
    )
    return (
        f"<p>Hi {bd_name},</p>"
        f"<p>You have <strong>{len(leads)}</strong> overdue follow-up"
        f"{'s' if len(leads) != 1 else ''}:</p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<tr><th>Lead</th><th>Phone</th><th>Course</th><th>Was due</th></tr>"
        f"{rows}"
        "</table>"
        "<p>Please follow up as soon as possible.</p>"
    )


def send_daily_overdue_alerts(db: Database) -> None:
    if not settings.followup_alerts_enabled:
        logger.info("Follow-up alerts are disabled (FOLLOWUP_ALERTS_ENABLED=false); skipping.")
        return

    email_error = settings.email_config_error()
    if email_error:
        logger.warning("Skipping follow-up alert digest: %s", email_error)
        return

    grouped = overdue_leads_by_bd(db)
    if not grouped:
        logger.info("Follow-up alert digest: no overdue leads, nothing to send.")
        return

    for bd_id, leads in grouped.items():
        caller = db[CALLERS].find_one({"caller_id": bd_id})
        if not caller or not caller.get("email"):
            logger.warning(
                "Follow-up alert: no caller/email found for assigned_bd.id '%s' (%d overdue lead(s) "
                "unalerted).",
                bd_id,
                len(leads),
            )
            continue
        try:
            email_service.send_email(
                to=caller["email"],
                subject=f"{len(leads)} overdue follow-up{'s' if len(leads) != 1 else ''}",
                html_body=_render_digest(caller.get("name", "there"), leads),
            )
            logger.info("Follow-up alert sent to %s (%d overdue lead(s)).", caller["email"], len(leads))
        except email_service.EmailDeliveryError as exc:
            logger.error("Follow-up alert failed for %s: %s", caller["email"], exc)
