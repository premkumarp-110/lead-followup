"""In-process scheduler for the daily follow-up reminder digest.

Started/stopped from main.py's lifespan, and only when
settings.followup_alerts_enabled is true. SMTP misconfiguration is handled
inside send_pending_alerts itself (it logs and skips, never raises here).

ONE PROCESS ONLY. Running uvicorn with --workers N starts N schedulers, and
each would fire its own digest. The once-a-day dedupe in
followup_alert_service keeps that from spamming BDs, but it is a race, not a
design. A real fix needs a shared lock in MongoDB; until then, run a single
worker (or disable FOLLOWUP_ALERTS_ENABLED on all but one).
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import get_db
from app.services.followup_alert_service import TRIGGER_SCHEDULED, send_pending_alerts

logger = logging.getLogger("app")

JOB_ID = "daily_followup_reminders"

# APScheduler's default misfire_grace_time is ONE SECOND: a run missed by more
# than that is dropped silently. A deploy, a restart or a briefly busy host at
# the trigger minute would lose the whole day's digest with no error anywhere.
# An hour of grace means a late start still sends.
MISFIRE_GRACE_SECONDS = 3600

_scheduler: BackgroundScheduler | None = None


def _run_daily_alerts() -> None:
    """The scheduled job body.

    Wrapped: send_pending_alerts already handles PyMongoError and per-BD
    delivery failures, but anything it does not anticipate would otherwise
    propagate into APScheduler's worker and take every future run with it.
    """
    try:
        summary = send_pending_alerts(get_db(), trigger=TRIGGER_SCHEDULED)
        logger.info(
            "Scheduled reminder run for %s: %d sent, %d skipped, %d failed.",
            summary.get("sent_for_date"),
            summary.get("sent", 0),
            summary.get("skipped", 0),
            summary.get("failed", 0),
        )
    except Exception:
        logger.exception("Scheduled follow-up reminder run failed; the schedule continues.")


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler

    if not settings.followup_alerts_enabled:
        logger.info("Follow-up reminder scheduler not started (FOLLOWUP_ALERTS_ENABLED=false).")
        _scheduler = None
        return None

    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        _run_daily_alerts,
        "cron",
        hour=settings.followup_alert_hour,
        minute=settings.followup_alert_minute,
        id=JOB_ID,
        misfire_grace_time=MISFIRE_GRACE_SECONDS,
        # Both are already APScheduler defaults; set explicitly so the intent
        # survives a library upgrade that changes them.
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler

    job = scheduler.get_job(JOB_ID)
    logger.info(
        "Follow-up reminder scheduler started: daily at %02d:%02d Asia/Kolkata. Next run: %s.",
        settings.followup_alert_hour,
        settings.followup_alert_minute,
        job.next_run_time.isoformat() if job and job.next_run_time else "unknown",
    )
    return scheduler


def scheduler_status() -> dict:
    """What the operator needs to trust the schedule.

    "Enabled" and "actually going to run" are different claims; this reports
    the second. The flag is only read at startup, so a .env edit without a
    restart shows up here as enabled-but-not-running.
    """
    job = _scheduler.get_job(JOB_ID) if _scheduler is not None else None
    return {
        "enabled": settings.followup_alerts_enabled,
        "running": bool(_scheduler is not None and _scheduler.running),
        "job_id": JOB_ID,
        "hour": settings.followup_alert_hour,
        "minute": settings.followup_alert_minute,
        "timezone": "Asia/Kolkata",
        "next_run_at": job.next_run_time if job and job.next_run_time else None,
        "misfire_grace_seconds": MISFIRE_GRACE_SECONDS,
    }


def stop_scheduler(scheduler: BackgroundScheduler | None) -> None:
    global _scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    _scheduler = None
