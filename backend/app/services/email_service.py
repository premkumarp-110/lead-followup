"""Shared SMTP client for outbound alert email.

Owns connection/authentication so callers never touch smtplib directly, same
role vertex_client.py plays for Gemini. Nothing here runs at import time.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """SMTP is not configured or the send failed. Message is always user-safe."""


def send_email(to: str, subject: str, html_body: str) -> None:
    error = settings.email_config_error()
    if error:
        raise EmailDeliveryError(error)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = to
    message.set_content("This email requires an HTML-capable client to view.")
    message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailDeliveryError(
            "SMTP authentication failed. Check SMTP_USER and SMTP_PASSWORD (Gmail requires an "
            "app password, not the account password)."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailDeliveryError(f"Could not send email via {settings.smtp_host}: {exc}") from exc
