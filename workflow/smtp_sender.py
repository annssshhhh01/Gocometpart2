"""
workflow/smtp_sender.py

Sends the CG reply email back to the SU via Gmail SMTP.

Uses the same credentials already in .env:
  GMAIL_ADDRESS      -- the sender (CG inbox / your Gmail)
  GMAIL_APP_PASSWORD -- 16-char App Password

Called only when CG clicks "Approve & Send" in the UI.
The email is NEVER sent automatically — human in the loop is enforced.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587  # TLS


def send_reply(
    to_email: str,
    subject: str,
    body: str,
) -> None:
    """Send a plain-text reply email via Gmail SMTP.

    Args:
        to_email: Recipient address (the SU who sent the original email).
        subject:  Email subject line.
        body:     Plain-text email body (edited by CG before sending).

    Raises:
        RuntimeError: If credentials are missing or SMTP fails.
    """
    from_email = os.getenv("GMAIL_ADDRESS", "").strip()
    password   = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

    if not from_email or not password:
        raise RuntimeError(
            "GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set in .env — cannot send email."
        )

    msg = MIMEMultipart("alternative")
    msg["From"]    = from_email
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    logger.info("Sending reply to %s | Subject: %s", to_email, subject)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(from_email, password)
            server.sendmail(from_email, to_email, msg.as_string())
        logger.info("Reply sent successfully to %s", to_email)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"Gmail SMTP auth failed: {exc}\n"
            "Make sure GMAIL_APP_PASSWORD is correct and IMAP is enabled."
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"SMTP send failed: {exc}") from exc
