"""
workflow/gmail_listener.py

Polls a Gmail inbox via IMAP every POLL_INTERVAL seconds.
When an unread email with PDF/image attachments is found:
  1. Downloads all attachments
  2. Saves them to incoming_shipments/<SHP-id>/
  3. Marks the email as read  (prevents reprocessing)
  4. Watchdog detects the new folder and runs the full pipeline automatically

Credentials read from environment:
  GMAIL_ADDRESS      -- Gmail address to monitor (the CG inbox)
  GMAIL_APP_PASSWORD -- Gmail App Password (16-char, not your real password)

IMAP notes:
  - Gmail IMAP must be enabled: Gmail Settings -> Forwarding & POP/IMAP -> Enable IMAP
  - Uses SSL on port 993 (standard Gmail IMAP endpoint)
  - Only UNSEEN (unread) emails are processed; processed emails are marked Seen
"""

from __future__ import annotations

import email
import imaplib
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, UTC
from email.header import decode_header
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Force stdout to UTF-8 so print() never crashes on Windows cp1252 terminals
try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass  # Python < 3.7 fallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GMAIL_IMAP_HOST    = "imap.gmail.com"
GMAIL_IMAP_PORT    = 993
POLL_INTERVAL      = 10          # seconds between inbox checks
INCOMING_DIR       = Path("incoming_shipments")
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
    """Walk a MIME message and save all allowed attachments to shp_dir."""
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
        logger.info("Attachment saved: %s", dest)
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


# Folders to check in order — covers both normal delivery and spam filtering
IMAP_FOLDERS = ["INBOX", "[Gmail]/Spam"]


def _poll_once(mail: imaplib.IMAP4_SSL) -> int:
    """Check INBOX and Spam for unread emails with attachments.

    Returns number of new shipment folders created.
    """
    processed = 0

    for folder in IMAP_FOLDERS:
        status, _ = mail.select(folder)
        if status != "OK":
            logger.debug("Could not select folder: %s", folder)
            continue

        status, data = mail.search(None, "UNSEEN")
        if status != "OK" or not data[0]:
            continue

        email_ids = data[0].split()
        logger.info("Folder '%s': %d unread email(s) found.", folder, len(email_ids))

        for eid in email_ids:
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                continue

            msg     = email.message_from_bytes(msg_data[0][1])
            subject = _decode_header_value(msg.get("Subject", "No Subject"))
            sender  = msg.get("From", "unknown")

            logger.info("Checking email from %s | Subject: %s", sender, subject)

            if not _has_valid_attachments(msg):
                logger.info("No supported attachments -- marking read, skipping.")
                mail.store(eid, "+FLAGS", "\\Seen")
                continue

            # Create shipment folder
            ts          = datetime.now(UTC).strftime("%H%M%S")
            shipment_id = f"SHP-{ts}-{str(uuid.uuid4())[:4].upper()}"
            shp_dir     = INCOMING_DIR / shipment_id
            shp_dir.mkdir(parents=True, exist_ok=True)

            saved = _save_attachments(msg, shp_dir)

            if saved:
                # Save sender metadata so watcher can address the SMTP reply
                meta = {
                    "sender_email": sender,
                    "subject": subject,
                    "shipment_id": shipment_id,
                    "folder": folder,
                }
                (shp_dir / "meta.json").write_text(
                    json.dumps(meta, indent=2), encoding="utf-8"
                )
                print(
                    f"[Gmail] New email from: {sender}\n"
                    f"        Folder  : {folder}\n"
                    f"        Subject : {subject}\n"
                    f"        Shipment: {shipment_id} ({len(saved)} attachment(s))\n"
                    f"        -> Watchdog will trigger the pipeline automatically.",
                    flush=True,
                )
                logger.info(
                    "Shipment %s created -- %d doc(s) -- folder: %s -- from: %s",
                    shipment_id, len(saved), folder, sender,
                )
                processed += 1
            else:
                shp_dir.rmdir()
                logger.warning("Email had attachment headers but no decodable content.")

            # Mark as read so it won't be processed again
            mail.store(eid, "+FLAGS", "\\Seen")

    return processed



def _listener_loop() -> None:
    """Connect to Gmail IMAP and poll on a fixed interval.

    Reconnects automatically on dropped connections.
    Exits only if credentials are missing or auth fails permanently.
    """
    address  = os.getenv("GMAIL_ADDRESS", "").strip()
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

    if not address or not password:
        msg = (
            "[Gmail] WARNING: GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set in .env\n"
            "        Gmail listener is disabled - using Streamlit upload only."
        )
        logger.error(msg)
        print(msg, flush=True)
        return

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"[Gmail] Starting listener for {address} "
        f"(polling every {POLL_INTERVAL}s)...",
        flush=True,
    )

    while True:
        try:
            mail = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
            mail.login(address, password)
            logger.info("Connected to Gmail IMAP as %s", address)
            print(f"[Gmail] Connected -- watching inbox of {address}", flush=True)

            while True:
                count = _poll_once(mail)
                if count:
                    logger.info("%d new shipment(s) queued from Gmail.", count)
                time.sleep(POLL_INTERVAL)

        except imaplib.IMAP4.abort as exc:
            logger.warning("IMAP connection dropped (%s) - reconnecting in 10s...", exc)
            print(f"[Gmail] Connection dropped - reconnecting in 10s...", flush=True)
            time.sleep(10)

        except imaplib.IMAP4.error as exc:
            logger.error("IMAP auth/protocol error: %s", exc)
            print(
                f"[Gmail] IMAP ERROR: {exc}\n"
                "        Check GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env\n"
                "        and ensure IMAP is enabled in Gmail settings.",
                flush=True,
            )
            return  # Don't retry on auth errors

        except Exception as exc:  # noqa: BLE001
            logger.error("Gmail listener error: %s - retrying in 30s", exc, exc_info=True)
            print(f"[Gmail] Error: {exc} - retrying in 30s...", flush=True)
            time.sleep(30)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_listener_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def start_gmail_listener() -> None:
    """Start the Gmail IMAP polling loop in a background daemon thread.

    Safe to call multiple times -- subsequent calls are no-ops if the
    thread is already alive (critical for Streamlit rerenders).
    """
    global _listener_thread  # noqa: PLW0603

    with _thread_lock:
        if _listener_thread is not None and _listener_thread.is_alive():
            return

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
