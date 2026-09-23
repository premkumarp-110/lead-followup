"""Follow-up reminder digests: tell each BD what is overdue and due today.

Buckets are derived, never stored (see followup_service.compute_bucket), so the
pending set is recomputed fresh on every run. What *is* stored is the delivery
record: one `followup_alerts` document per BD per attempt, so "was this BD
reminded?" has an answer, and so a scheduled run cannot email the same BD twice
in one day.

Manual sends deliberately bypass that dedupe -- a "Send now" button that
silently does nothing is worse than a duplicate email.
"""

import html
import logging
from datetime import datetime

from pymongo.database import Database
from pymongo.errors import PyMongoError

from app.config import settings
from app.database import CALLERS, FOLLOWUP_ALERTS, LEADS
from app.models.lead import FollowUpBucket, FollowUpStatus
from app.services import email_service
from app.services.audio_service import new_id
from app.services.followup_service import (
    IST,
    compute_bucket,
    decorate_lead,
    is_ist_today,
    ist_date,
    utcnow,
)

logger = logging.getLogger("app")

# Same query as routes/leads.py's ACTIVE_FOLLOW_UP_QUERY: a lead is only a
# candidate while its follow-up is required AND still pending. Kept here
# rather than imported so services never depend on routes.
ACTIVE_FOLLOW_UP_QUERY = {
    "follow_up.required": True,
    "follow_up.status": FollowUpStatus.PENDING.value,
}

# Leads whose owner_id is missing or blank. They cannot be emailed -- nobody
# owns them -- but they must not vanish silently either, so they are grouped
# under this key, logged, and returned in the run summary.
UNASSIGNED_KEY = "__unassigned__"

# An inbox is not a database. Past this many rows the digest links back to the
# dashboard instead of growing without limit.
ALERT_ROW_CAP = 25


class AlertStatus:
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED_NO_EMAIL = "SKIPPED_NO_EMAIL"
    SKIPPED_NO_RECIPIENT = "SKIPPED_NO_RECIPIENT"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


TRIGGER_SCHEDULED = "scheduled"
TRIGGER_MANUAL = "manual"
# One lead, sent on request. Recorded distinctly so the delivery log shows why
# a BD got three emails in an afternoon.
TRIGGER_LEAD = "lead"


class LeadAlertError(ValueError):
    """This lead cannot be reminded about. The message is user-safe."""


# --------------------------------------------------------------------------
# Gathering
# --------------------------------------------------------------------------


def _empty_group(bd_name: str = "") -> dict:
    return {"bd_name": bd_name, "overdue": [], "due_today": [], "unscheduled_count": 0}


def count_orphaned_followups(db: Database) -> int:
    """Leads needing a follow-up that no query will ever surface.

    `required: true` with a status that is not PENDING/COMPLETED/CANCELLED --
    usually null -- matches neither ACTIVE_FOLLOW_UP_QUERY nor the closed list,
    so the lead silently falls out of the product. Counted, not fixed: the
    repair belongs wherever the bad write came from.
    """
    return db[LEADS].count_documents(
        {
            "follow_up.required": True,
            "follow_up.status": {
                "$nin": [
                    FollowUpStatus.PENDING.value,
                    FollowUpStatus.COMPLETED.value,
                    FollowUpStatus.CANCELLED.value,
                ]
            },
        }
    )


def pending_leads_by_bd(db: Database, now: datetime | None = None) -> dict[str, dict]:
    """Pending follow-ups grouped by the lead's owner (owner_id).

    Returns {bd_id: {bd_name, overdue: [...], due_today: [...], unscheduled_count}}.

    "Due today" is NOT the DUE bucket. compute_bucket only returns DUE inside
    DUE_WINDOW (2 hours), so a 16:00 follow-up seen by the 09:00 digest is
    UPCOMING. Due-today therefore means DUE *or* UPCOMING falling on today's
    IST date -- otherwise the morning digest would arrive nearly empty.
    """
    now = now or utcnow()
    grouped: dict[str, dict] = {}

    for doc in db[LEADS].find(ACTIVE_FOLLOW_UP_QUERY):
        lead = decorate_lead(doc, now)
        follow_up = lead.get("follow_up") or {}
        bucket = follow_up.get("bucket")

        owner_name = lead.get("owner_name") or ""
        bd_id = (lead.get("owner_id") or "").strip() or UNASSIGNED_KEY
        group = grouped.setdefault(bd_id, _empty_group(owner_name))
        if not group["bd_name"] and owner_name:
            group["bd_name"] = owner_name

        if bucket == FollowUpBucket.OVERDUE.value:
            group["overdue"].append(lead)
        elif bucket == FollowUpBucket.DUE.value:
            # DUE means "inside DUE_WINDOW" -- imminent, so it always belongs in
            # the digest. Gating it on the IST date would drop a call due in 45
            # minutes just because the clock is about to cross midnight.
            group["due_today"].append(lead)
        elif bucket == FollowUpBucket.UPCOMING.value:
            if is_ist_today(follow_up.get("datetime"), now):
                group["due_today"].append(lead)
        elif bucket == FollowUpBucket.UNSCHEDULED.value:
            group["unscheduled_count"] += 1

    orphaned = count_orphaned_followups(db)
    if orphaned:
        logger.warning(
            "Follow-up reminder: %d lead(s) have follow_up.required set but no PENDING status. "
            "They are invisible to both the worklist and these reminders -- check the data.",
            orphaned,
        )

    # Most urgent first within each section: oldest overdue, earliest due.
    far_future = datetime.max.replace(tzinfo=IST)
    for group in grouped.values():
        group["overdue"].sort(key=lambda l: (l["follow_up"].get("datetime") or far_future))
        group["due_today"].sort(key=lambda l: (l["follow_up"].get("datetime") or far_future))

    if UNASSIGNED_KEY in grouped:
        unassigned = grouped[UNASSIGNED_KEY]
        logger.warning(
            "Follow-up reminder: %d overdue and %d due-today lead(s) have no assigned BD and "
            "cannot be emailed.",
            len(unassigned["overdue"]),
            len(unassigned["due_today"]),
        )

    return grouped


def group_totals(group: dict) -> dict:
    return {
        "overdue": len(group["overdue"]),
        "due_today": len(group["due_today"]),
        "unscheduled": group["unscheduled_count"],
    }


def has_anything_to_send(group: dict) -> bool:
    """Unscheduled alone is not worth an email -- it has no deadline to miss."""
    return bool(group["overdue"] or group["due_today"])


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _esc(value) -> str:
    """Every value reaching the template goes through here.

    Lead names and follow-up reasons are free text originating from an LLM
    transcript, so they can contain anything.
    """
    return html.escape(str(value)) if value not in (None, "") else ""


def _lead_label(lead: dict) -> str:
    """The CRM exposes no name, phone or email -- the id is the identity."""
    return _esc(lead.get("lead_id") or "Unknown lead")


def _when_text(lead: dict) -> str:
    when = (lead.get("follow_up") or {}).get("datetime")
    if not when:
        return "no date set"
    return _esc(when.astimezone(IST).strftime("%d %b, %I:%M %p"))


def _rows(leads: list[dict]) -> str:
    shown = leads[:ALERT_ROW_CAP]
    rows = "".join(
        "<tr>"
        f"<td>{_lead_label(lead)}</td>"
        f"<td>{_esc(lead.get('product') or '-')}</td>"
        f"<td>{_esc(lead.get('stage') or '-')}</td>"
        f"<td>{_when_text(lead)}</td>"
        f"<td>{_esc((lead.get('follow_up') or {}).get('reason') or '-')}</td>"
        "</tr>"
        for lead in shown
    )
    if len(leads) > ALERT_ROW_CAP:
        rows += (
            f"<tr><td colspan='5'><em>…and {len(leads) - ALERT_ROW_CAP} more. "
            "Open the dashboard to see the full list.</em></td></tr>"
        )
    return rows


def _footer(now: datetime) -> str:
    """Shared by the daily digest and the single-lead reminder.

    The timezone line is not decoration: every datetime in the body is
    rendered in IST while the mail client shows the reader's own zone.
    """
    return (
        "<p style='color:#777;font-size:12px'>Times are shown in IST. "
        f"Generated {_esc(now.astimezone(IST).strftime('%d %b %Y, %I:%M %p'))} IST.</p>"
    )


def _section(title: str, leads: list[dict]) -> str:
    if not leads:
        return ""
    return (
        f"<h3 style='margin:18px 0 6px'>{_esc(title)} ({len(leads)})</h3>"
        "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
        "<tr><th>Lead</th><th>Product</th><th>Stage</th><th>When</th><th>Reason</th></tr>"
        f"{_rows(leads)}"
        "</table>"
    )


# How a lead's derived bucket reads in a one-lead reminder. The daily digest
# only ever contains overdue/due-today work, but an explicit per-lead reminder
# can be sent for anything still pending -- including a follow-up with no date.
_BUCKET_BLURB = {
    FollowUpBucket.OVERDUE.value: "This follow-up is <strong>overdue</strong>.",
    FollowUpBucket.DUE.value: "This follow-up is <strong>due now</strong>.",
    FollowUpBucket.UPCOMING.value: "This follow-up is <strong>scheduled</strong>.",
    FollowUpBucket.UNSCHEDULED.value: (
        "This lead needs a follow-up but <strong>no date was agreed on the call</strong>, "
        "so it sits on no timeline. Give it a time in the dashboard."
    ),
}


def render_lead_reminder(lead: dict, bd_name: str = "", now: datetime | None = None) -> str:
    """A reminder about ONE lead.

    Deliberately not the daily digest: that message answers "what do I owe
    today?" and leads with "You have N follow-ups". This one was asked for
    about a specific lead, so it says which lead and why, and it works for a
    follow-up that is merely upcoming or has no date at all -- neither of which
    the digest would ever include.
    """
    now = now or utcnow()
    follow_up = lead.get("follow_up") or {}
    bucket = follow_up.get("bucket") or compute_bucket(follow_up, now).value

    return (
        f"<p>Hi {_esc(bd_name or 'there')},</p>"
        f"<p>A reminder about lead <strong>{_lead_label(lead)}</strong>.</p>"
        f"<p>{_BUCKET_BLURB.get(bucket, 'This lead has a follow-up pending.')}</p>"
        "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse'>"
        "<tr><th>Lead</th><th>Product</th><th>Stage</th><th>When</th><th>Reason</th></tr>"
        f"{_rows([lead])}"
        "</table>"
        f"{_footer(now)}"
    )


def render_digest(group: dict, now: datetime | None = None) -> str:
    """The digest body. Safe to call with an empty group (preview uses it)."""
    now = now or utcnow()
    name = _esc(group.get("bd_name") or "there")
    overdue, due_today = group["overdue"], group["due_today"]
    total = len(overdue) + len(due_today)

    if not total:
        body = "<p>Nothing overdue and nothing due today.</p>"
    else:
        body = (
            f"<p>You have <strong>{total}</strong> follow-up{'s' if total != 1 else ''} "
            "needing attention:</p>"
            f"{_section('Overdue', overdue)}"
            f"{_section('Due today', due_today)}"
        )

    footer = ""
    if group["unscheduled_count"]:
        count = group["unscheduled_count"]
        footer = (
            f"<p style='color:#555'>You also have <strong>{count}</strong> lead"
            f"{'s' if count != 1 else ''} needing a follow-up with no date set. "
            "They are not listed above because they have no deadline — open the dashboard's "
            "Unscheduled filter to give them one.</p>"
        )

    return (
        f"<p>Hi {name},</p>"
        f"{body}"
        f"{footer}"
        f"{_footer(now)}"
    )


def _lead_subject(lead: dict) -> str:
    bucket = (lead.get("follow_up") or {}).get("bucket")
    prefix = {
        FollowUpBucket.OVERDUE.value: "Overdue follow-up",
        FollowUpBucket.DUE.value: "Follow-up due now",
        FollowUpBucket.UNSCHEDULED.value: "Follow-up needs a date",
    }.get(bucket, "Follow-up reminder")
    return f"{prefix}: {lead.get('lead_id')}"


def _subject(group: dict) -> str:
    overdue, due_today = len(group["overdue"]), len(group["due_today"])
    parts = []
    if overdue:
        parts.append(f"{overdue} overdue")
    if due_today:
        parts.append(f"{due_today} due today")
    return f"Follow-ups: {' and '.join(parts)}" if parts else "Follow-up reminder"


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def _record(db: Database, **fields) -> dict:
    doc = {"alert_id": new_id("ALERT"), "sent_at": utcnow(), "error": None, **fields}
    try:
        db[FOLLOWUP_ALERTS].insert_one(dict(doc))
    except PyMongoError as exc:
        # The email may already have gone out; losing the audit row must not
        # turn that into an exception the caller has to handle.
        logger.error("Could not write followup_alerts record for '%s': %s", fields.get("bd_id"), exc)
    doc.pop("_id", None)
    return doc


def _already_sent_today(db: Database, bd_id: str, sent_for_date: str) -> bool:
    return (
        db[FOLLOWUP_ALERTS].find_one(
            {
                "bd_id": bd_id,
                "sent_for_date": sent_for_date,
                "status": AlertStatus.SENT,
                "trigger": TRIGGER_SCHEDULED,
            }
        )
        is not None
    )


def _resolve_recipient(db: Database, bd_id: str) -> tuple[dict | None, str | None]:
    """Returns (caller, skip_reason). A skip_reason means do not email."""
    caller = db[CALLERS].find_one({"caller_id": bd_id})
    if caller is None:
        return None, f"No caller record found for assigned BD '{bd_id}'."
    if caller.get("active") is False:
        return caller, f"BD '{caller.get('name') or bd_id}' is marked inactive."
    if not (caller.get("email") or "").strip():
        return caller, f"BD '{caller.get('name') or bd_id}' has no email address."
    return caller, None


def send_lead_alert(
    db: Database,
    lead_id: str,
    *,
    now: datetime | None = None,
) -> dict:
    """Send a reminder about ONE lead, to whoever owns it.

    Returns the same summary shape as `send_pending_alerts` so the route, the
    response model and the delivery log stay identical.

    Never deduped. The daily digest is deduped because a cron that fires twice
    should not mail twice; this only happens because somebody asked for it, and
    a button that silently does nothing is worse than a second email.

    Raises LeadAlertError when there is genuinely nothing to remind about --
    the lead is unknown, its follow-up is not required, or it is already
    completed/cancelled. That is a 4xx, not a silent no-op.
    """
    now = now or utcnow()
    summary: dict = {
        "trigger": TRIGGER_LEAD,
        "sent_for_date": ist_date(now).isoformat(),
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "results": [],
        "message": "",
    }

    if not settings.followup_alerts_enabled:
        raise LeadAlertError(
            "Follow-up reminders are disabled (FOLLOWUP_ALERTS_ENABLED is not true)."
        )
    email_error = settings.email_config_error()
    if email_error:
        raise LeadAlertError(email_error)

    doc = db[LEADS].find_one({"lead_id": lead_id})
    if doc is None:
        raise LeadAlertError(f"Lead '{lead_id}' was not found.")

    lead = decorate_lead(doc, now)
    follow_up = lead.get("follow_up") or {}
    if not follow_up.get("required"):
        raise LeadAlertError(
            f"Lead '{lead_id}' has no follow-up required, so there is nothing to remind about."
        )
    if follow_up.get("status") != FollowUpStatus.PENDING.value:
        raise LeadAlertError(
            f"Lead '{lead_id}' has a follow-up that is already "
            f"{(follow_up.get('status') or 'not pending').lower()}."
        )

    bd_id = (lead.get("owner_id") or "").strip() or UNASSIGNED_KEY
    base = {
        "bd_id": bd_id,
        "bd_name": lead.get("owner_name") or "",
        "bd_email": None,
        "sent_for_date": summary["sent_for_date"],
        "overdue_count": 1 if follow_up.get("bucket") == FollowUpBucket.OVERDUE.value else 0,
        "due_today_count": 0,
        "unscheduled_count": 1 if follow_up.get("bucket") == FollowUpBucket.UNSCHEDULED.value else 0,
        "lead_ids": [lead_id],
        "trigger": TRIGGER_LEAD,
    }

    if bd_id == UNASSIGNED_KEY:
        raise LeadAlertError(
            f"Lead '{lead_id}' has no assigned BD, so there is nobody to email."
        )

    caller, skip_reason = _resolve_recipient(db, bd_id)
    if skip_reason:
        # Recorded rather than raised: the request was valid and the operator
        # should be able to see in the log that it was deliberately skipped.
        status = (
            AlertStatus.SKIPPED_NO_EMAIL
            if caller is not None and caller.get("active") is not False
            else AlertStatus.SKIPPED_NO_RECIPIENT
        )
        logger.warning("Lead reminder for %s skipped: %s", lead_id, skip_reason)
        summary["skipped"] = 1
        summary["message"] = skip_reason
        summary["results"] = [_record(db, **base, status=status, error=skip_reason)]
        return summary

    recipient = caller["email"].strip()
    base["bd_email"] = recipient
    bd_name = caller.get("name") or lead.get("owner_name") or ""

    try:
        email_service.send_email(
            to=recipient,
            subject=_lead_subject(lead),
            html_body=render_lead_reminder(lead, bd_name, now),
        )
    except email_service.EmailDeliveryError as exc:
        logger.error("Lead reminder for %s failed to %s: %s", lead_id, recipient, exc)
        summary["failed"] = 1
        summary["message"] = str(exc)
        summary["results"] = [_record(db, **base, status=AlertStatus.FAILED, error=str(exc))]
        return summary

    logger.info("Lead reminder for %s sent to %s.", lead_id, recipient)
    summary["sent"] = 1
    summary["message"] = f"Reminder for {lead_id} sent to {recipient}."
    summary["results"] = [_record(db, **base, status=AlertStatus.SENT)]
    return summary


def send_pending_alerts(
    db: Database,
    *,
    trigger: str = TRIGGER_SCHEDULED,
    bd_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Send the reminder digest to every BD with something pending.

    `trigger` distinguishes the daily cron from an operator pressing Send.
    Scheduled runs are deduped to once per BD per IST day; manual runs are not.
    Returns a summary; never raises for a per-BD failure.
    """
    now = now or utcnow()
    # Fixed once so a run that crosses IST midnight stays on one date.
    sent_for_date = ist_date(now).isoformat()
    summary: dict = {
        "trigger": trigger,
        "sent_for_date": sent_for_date,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "results": [],
        "message": "",
    }

    if not settings.followup_alerts_enabled:
        summary["message"] = "Follow-up alerts are disabled (FOLLOWUP_ALERTS_ENABLED is not true)."
        logger.info("Follow-up reminder: %s", summary["message"])
        return summary

    email_error = settings.email_config_error()
    if email_error:
        summary["message"] = email_error
        logger.warning("Follow-up reminder skipped: %s", email_error)
        return summary

    try:
        grouped = pending_leads_by_bd(db, now)
    except PyMongoError as exc:
        # Must never propagate: this runs on the scheduler thread (M3).
        summary["message"] = f"Could not read leads: {exc}"
        logger.error("Follow-up reminder failed to read leads: %s", exc)
        return summary

    if bd_id:
        grouped = {bd_id: grouped.get(bd_id, _empty_group())}

    for target_id, group in grouped.items():
        if not has_anything_to_send(group):
            continue

        totals = group_totals(group)
        lead_ids = [l["lead_id"] for l in group["overdue"] + group["due_today"]]
        base = {
            "bd_id": target_id,
            "bd_name": group.get("bd_name") or "",
            "bd_email": None,
            "sent_for_date": sent_for_date,
            "overdue_count": totals["overdue"],
            "due_today_count": totals["due_today"],
            "unscheduled_count": totals["unscheduled"],
            "lead_ids": lead_ids,
            "trigger": trigger,
        }

        if target_id == UNASSIGNED_KEY:
            summary["skipped"] += 1
            summary["results"].append(
                {
                    **base,
                    "status": AlertStatus.SKIPPED_NO_RECIPIENT,
                    "error": "These leads have no assigned BD, so there is nobody to email.",
                }
            )
            continue

        if trigger == TRIGGER_SCHEDULED and _already_sent_today(db, target_id, sent_for_date):
            summary["skipped"] += 1
            summary["results"].append(
                _record(
                    db,
                    **base,
                    status=AlertStatus.SKIPPED_DUPLICATE,
                    error=f"Already sent to this BD for {sent_for_date}.",
                )
            )
            continue

        caller, skip_reason = _resolve_recipient(db, target_id)
        if skip_reason:
            status = (
                AlertStatus.SKIPPED_NO_EMAIL
                if caller is not None and caller.get("active") is not False
                else AlertStatus.SKIPPED_NO_RECIPIENT
            )
            logger.warning(
                "Follow-up reminder: %s (%d lead(s) unalerted).", skip_reason, len(lead_ids)
            )
            summary["skipped"] += 1
            summary["results"].append(_record(db, **base, status=status, error=skip_reason))
            continue

        recipient = caller["email"].strip()
        base["bd_email"] = recipient
        # The caller record is the directory entry; the lead's owner_name is a copy.
        group = {**group, "bd_name": caller.get("name") or group.get("bd_name") or ""}

        try:
            email_service.send_email(
                to=recipient,
                subject=_subject(group),
                html_body=render_digest(group, now),
            )
        except email_service.EmailDeliveryError as exc:
            logger.error("Follow-up reminder failed for %s: %s", recipient, exc)
            summary["failed"] += 1
            summary["results"].append(_record(db, **base, status=AlertStatus.FAILED, error=str(exc)))
            continue

        logger.info(
            "Follow-up reminder sent to %s (%d overdue, %d due today).",
            recipient,
            totals["overdue"],
            totals["due_today"],
        )
        summary["sent"] += 1
        summary["results"].append(_record(db, **base, status=AlertStatus.SENT))

    if not summary["results"]:
        summary["message"] = "No follow-ups are overdue or due today."
        logger.info("Follow-up reminder: nothing to send.")

    return summary
