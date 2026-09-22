"""In-process scheduler for the daily follow-up alert email digest.

Started/stopped from main.py's lifespan. Only runs when
settings.followup_alerts_enabled is true; SMTP misconfiguration is handled
inside send_daily_overdue_alerts itself (logs and skips, never raises here).
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import get_db
from app.services.followup_alert_service import send_daily_overdue_alerts

logger = logging.getLogger("app")


def _run_daily_alerts() -> None:
    send_daily_overdue_alerts(get_db())


def start_scheduler() -> BackgroundScheduler | None:
    if not settings.followup_alerts_enabled:
        logger.info("Follow-up alert scheduler not started (FOLLOWUP_ALERTS_ENABLED=false).")
        return None

    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        _run_daily_alerts,
        "cron",
        hour=settings.followup_alert_hour,
        minute=settings.followup_alert_minute,
        id="daily_overdue_alerts",
    )
    scheduler.start()
    logger.info(
        "Follow-up alert scheduler started: daily digest at %02d:%02d Asia/Kolkata.",
        settings.followup_alert_hour,
        settings.followup_alert_minute,
    )
    return scheduler


def stop_scheduler(scheduler: BackgroundScheduler | None) -> None:
    if scheduler is not None:
        scheduler.shutdown(wait=False)
