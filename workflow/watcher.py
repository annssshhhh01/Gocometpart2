"""
workflow/watcher.py

Monitors incoming_shipments/ for new shipment folders.
When a folder appears (simulating an SU email arriving), the full
pipeline is executed automatically in a background thread:

    load_shipment → process_shipment → validate_shipment
        → decide → draft_email → save_result

Results are pushed to a thread-safe queue so Streamlit can poll and
display them without blocking the UI.

Concerns intentionally NOT handled here:
  - Streamlit rendering / session state
  - Any network / SMTP calls
  - Async code (everything is sync inside a daemon thread)
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path

from watchdog.events import DirCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from workflow.shipment import INCOMING_DIR, load_shipment

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared result queue — produced by the watcher thread, consumed by Streamlit
# ---------------------------------------------------------------------------

# Each item is a dict:  {"shipment_id": str, "result": dict, "error": str|None}
result_queue: queue.Queue[dict] = queue.Queue()


# ---------------------------------------------------------------------------
# Internal pipeline runner (called from background thread)
# ---------------------------------------------------------------------------

def _run_pipeline(shipment_path: Path) -> None:
    """Load and process a shipment folder end-to-end, then push to result_queue."""

    # Lazy imports keep the watcher module importable even if optional deps
    # are missing until runtime.
    from agents.decision import decide
    from config import APPROVAL_POLICY, RULES
    from db.database import save_result
    from workflow.email_drafter import draft_email
    from workflow.shipment_processor import process_shipment
    from workflow.shipment_validator import validate_shipment

    import json as _json
    rules_context = _json.dumps(
        {"customer": "GlobalTech Imports GmbH", "rules": RULES, "policy": APPROVAL_POLICY},
        indent=2,
    )

    shipment_id = shipment_path.name
    logger.info("Pipeline starting for shipment: %s", shipment_id)

    try:
        # ── Give the OS a moment to finish writing all files ──────────────
        time.sleep(0.5)

        shipment          = load_shipment(shipment_path)
        processed         = process_shipment(shipment)
        validation_report = validate_shipment(processed)

        # Flatten extracted fields for the decision agent
        merged: dict = {}
        for dv in validation_report["document_validations"].values():
            merged.update(dv["fields"])

        decision = decide(merged, rules_context) if merged else {
            "decision": "flag_for_review",
            "reason": "No fields could be extracted.",
        }

        email_draft = draft_email(
            shipment_id=shipment_id,
            decision=decision,
            validation_report=validation_report,
        )

        save_result(
            extracted=merged,
            validated={
                doc_key: dv["fields"]
                for doc_key, dv in validation_report["document_validations"].items()
            },
            decision=decision,
        )

        result_queue.put({
            "shipment_id":       shipment_id,
            "processed":         processed,
            "validation_report": validation_report,
            "decision":          decision,
            "email_draft":       email_draft,
            "error":             None,
        })

        logger.info("Pipeline completed for shipment: %s  decision=%s",
                    shipment_id, decision.get("decision"))

    except Exception as exc:  # noqa: BLE001
        logger.error("Pipeline failed for shipment %s: %s", shipment_id, exc, exc_info=True)
        result_queue.put({
            "shipment_id": shipment_id,
            "error":       str(exc),
        })


# ---------------------------------------------------------------------------
# Watchdog event handler
# ---------------------------------------------------------------------------

class ShipmentHandler(FileSystemEventHandler):
    """Reacts to new shipment directories and launches the full pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self.processed_shipments: set[str] = set()

    def _is_hidden(self, path: Path) -> bool:
        return any(part.startswith(".") for part in path.parts)

    def on_created(self, event: DirCreatedEvent) -> None:  # type: ignore[override]
        if not event.is_directory:
            return

        shipment_path = Path(event.src_path)

        if self._is_hidden(shipment_path):
            return

        shipment_id = shipment_path.name

        if shipment_id in self.processed_shipments:
            logger.debug("Duplicate event ignored: %s", shipment_id)
            return

        self.processed_shipments.add(shipment_id)
        logger.info("New shipment folder detected: %s — launching pipeline thread", shipment_id)
        print(f"[Watcher] New shipment detected: {shipment_id} — running pipeline…")

        # Run in a daemon thread so the watcher loop is never blocked
        t = threading.Thread(
            target=_run_pipeline,
            args=(shipment_path,),
            daemon=True,
            name=f"pipeline-{shipment_id}",
        )
        t.start()


# ---------------------------------------------------------------------------
# Public entry point — starts observer in background (non-blocking)
# ---------------------------------------------------------------------------

_observer: Observer | None = None
_observer_lock = threading.Lock()


def start_watcher(watch_dir: Path = INCOMING_DIR) -> None:
    """Start the Watchdog observer in a background daemon thread.

    Safe to call multiple times — subsequent calls are no-ops if the
    observer is already running (important for Streamlit rerenders).

    Args:
        watch_dir: Directory to monitor. Created if it does not exist.
    """
    global _observer  # noqa: PLW0603

    with _observer_lock:
        if _observer is not None and _observer.is_alive():
            return  # already running — do nothing

        watch_dir = watch_dir.resolve()
        watch_dir.mkdir(parents=True, exist_ok=True)

        handler  = ShipmentHandler()
        observer = Observer()
        observer.schedule(handler, path=str(watch_dir), recursive=False)
        observer.daemon = True
        observer.start()

        _observer = observer
        logger.info("Watchdog observer started on: %s", watch_dir)
        print(f"[Watcher] Watching '{watch_dir.name}/' for new shipment folders…")


def stop_watcher() -> None:
    """Stop the observer gracefully (useful for tests / clean shutdown)."""
    global _observer  # noqa: PLW0603
    with _observer_lock:
        if _observer and _observer.is_alive():
            _observer.stop()
            _observer.join()
            _observer = None
            logger.info("Watchdog observer stopped.")


# ---------------------------------------------------------------------------
# CLI convenience (unchanged — run this file directly for standalone mode)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import signal

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )

    start_watcher()
    print("Press Ctrl+C to stop.")

    def _handle_sig(sig, frame):  # noqa: ANN001
        print("\n[Watcher] Stopping…")
        stop_watcher()

    signal.signal(signal.SIGINT, _handle_sig)
    # Keep main thread alive
    while _observer and _observer.is_alive():
        time.sleep(1)
