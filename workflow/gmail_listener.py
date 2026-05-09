"""
workflow/gmail_listener.py

Polls a Gmail inbox via IMAP every POLL_INTERVAL seconds.
When an unread email with PDF/image attachments is found:
  1. Downloads all attachments
  2. Saves them to incoming_shipments/<SHP-id>/
  3. Marks the email as read  (prevents reprocessing)
  4. Watchdog detects the new folder and runs the full pipeline automatically

Credentials read from environment:
  GMAIL_ADDRESS      — Gmail address to monitor (the CG inbox)
  GMAIL_APP_PASSWORD — Gmail App Password (16-char, not your real password)

IMAP notes:
  - Gmail IMAP must be enabled: Gmail Settings → Forwarding & POP/IMAP → Enable IMAP
  - Uses SSL on port 993 (standard Gmail IMAP endpoint)
  - Only UNSEEN (unread) emails are processed; processed emails are marked Seen
"""

from __future__ import annotations

import email
import imaplib
import logging
import os
import threading
import time
import uuid
from datetime import datetime, UTC
from email.header import decode_header
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GMAIL_IMAP_HOST   = "imap.gmail.com"
GMAIL_IMAP_PORT   = 993
POLL_INTERVAL     = 30          # seconds between inbox checks
INCOMING_DIR      = Path("incoming_shipments")
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode_header_value(raw) -> str:
    """Safely decode an encoded email header value."""
    decoded, encoding = decode_header(raw or "")[0]
    if isinstance(decoded, bytes):
        return decoded.decode(encoding or "utf-8", errors="replace")
    return decoded or ""


def _save_attachments(msg: email.message.Message, shp_dir: Path) -> list[Path]:
    """Walk a MIME message and save all allowed attachments to shp_dir.

    Returns list of saved file paths.
    """
    saved: list[Path] = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get("Content-Disposition") is None:
            continue

        raw_filename = part.get_filename()
        if not raw_filename:
            continue

        filename = _decode_header_value(raw_filename)
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            logger.debug("Skipping unsupported attachment: %s", filename)
            continue

        dest = shp_dir / filename
        dest.write_bytes(part.get_payload(decode=True))
        saved.append(dest)
        logger.info("Attachment saved → %s", dest)

    return saved


def _has_valid_attachments(msg: email.message.Message) -> bool:
    """Return True if the message has at least one supported attachment."""
    for part in msg.walk():
        if part.get("Content-Disposition") is None:
            continue
        filename = part.get_filename() or ""
        if Path(filename).suffix.lower() in ALLOWED_EXTENSIONS:
            return True
    return False


def _poll_once(mail: imaplib.IMAP4_SSL) -> int:
    """Check inbox once for unread emails with attachments.

    Returns:
        Number of new shipment folders created.
    """
    mail.select("inbox")
    status, data = mail.search(None, "UNSEEN")
    if status != "OK" or not data[0]:
        return 0

    email_ids = data[0].split()
    processed = 0

    for eid in email_ids:
        status, msg_data = mail.fetch(eid, "(RFC822)")
        if status != "OK":
            continue

        msg     = email.message_from_bytes(msg_data[0][1])
        subject = _decode_header_value(msg.get("Subject", "No Subject"))
        sender  = msg.get("From", "unknown")

        logger.info("Checking email from %s — Subject: %s", sender, subject)

        if not _has_valid_attachments(msg):
            logger.info("No supported attachments — marking read, skipping.")
            mail.store(eid, "+FLAGS", "\\Seen")
            continue

        # ── Create shipment folder ────────────────────────────────────────
        ts          = datetime.now(UTC).strftime("%H%M%S")
        shipment_id = f"SHP-{ts}-{str(uuid.uuid4())[:4].upper()}"
        shp_dir     = INCOMING_DIR / shipment_id
        shp_dir.mkdir(parents=True, exist_ok=True)

        saved = _save_attachments(msg, shp_dir)

        if saved:
            print(
                f"[Gmail] 📧 New email from {sender}\n"
                f"        Subject : {subject}\n"
                f"        Shipment: {shipment_id}  ({len(saved)} attachment(s))\n"
                f"        → Watchdog will trigger the pipeline automatically."
            )
            logger.info(
                "Shipment %s created — %d doc(s) — from: %s",
                shipment_id, len(saved), sender,
            )
            processed += 1
        else:
            # No valid files written — remove empty folder
            shp_dir.rmdir()
            logger.warning("Email had attachment headers but no decodable content.")

        # Mark as read so it's not processed again next poll
        mail.store(eid, "+FLAGS", "\\Seen")

    return processed


def _listener_loop() -> None:
    """Connect to Gmail IMAP and poll on a fixed interval.

    Reconnects automatically on dropped connections or transient errors.
    Exits only if credentials are missing.
    """
    address  = os.getenv("GMAIL_ADDRESS", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

    if not address or not password:
        msg = (
            "[Gmail] ⚠️  GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set in .env\n"
            "         Gmail listener is disabled — using Streamlit upload only."
        )
        logger.error(msg)
        print(msg)
        return

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[Gmail] Starting listener for {address} (polling every {POLL_INTERVAL}s)…")

    while True:
        try:
            mail = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
            mail.login(address, password)
            logger.info("Connected to Gmail IMAP as %s", address)
            print(f"[Gmail] ✅ Connected — watching inbox of {address}")

            while True:
                count = _poll_once(mail)
                if count:
                    logger.info("%d new shipment(s) queued from Gmail.", count)
                time.sleep(POLL_INTERVAL)

        except imaplib.IMAP4.abort as exc:
            logger.warning("IMAP connection dropped (%s) — reconnecting in 10s…", exc)
            print(f"[Gmail] Connection dropped — reconnecting in 10s…")
            time.sleep(10)

        except imaplib.IMAP4.error as exc:
            logger.error("IMAP auth/protocol error: %s", exc)
            print(
                f"[Gmail] ❌ IMAP error: {exc}\n"
                "        Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env\n"
                "        and ensure IMAP is enabled in Gmail settings."
            )
            # Don't retry on auth errors — bad credentials will keep failing
            return

        except Exception as exc:  # noqa: BLE001
            logger.error("Gmail listener error: %s — retrying in 30s", exc, exc_info=True)
            print(f"[Gmail] Error: {exc} — retrying in 30s…")
            time.sleep(30)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_listener_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def start_gmail_listener() -> None:
    """Start the Gmail IMAP polling loop in a background daemon thread.

    Safe to call multiple times — subsequent calls are no-ops if the
    thread is already alive (critical for Streamlit rerenders which
    re-execute the entire script on every interaction).
    """
    global _listener_thread  # noqa: PLW0603

    with _thread_lock:
        if _listener_thread is not None and _listener_thread.is_alive():
            return  # already running

        _listener_thread = threading.Thread(
            target=_listener_loop,
            daemon=True,
            name="gmail-listener",
        )
        _listener_thread.start()
        logger.info("Gmail listener daemon thread started.")


def is_gmail_listener_running() -> bool:
    """Return True if the listener thread is alive (for UI status display)."""
    return _listener_thread is not None and _listener_thread.is_alive()
