"""
test_shipment_workflow.py

End-to-end integration test for the Part 2 GoComet Nova shipment pipeline.

Simulates the complete workflow for a single shipment folder:
    load_shipment()
        → process_shipment()
            → validate_shipment()
                → decide()

Run from the project root:
    python test_shipment_workflow.py

Expected shipment folder layout:
    incoming_shipments/
    └── SHP001/
        ├── invoice.pdf
        ├── bol.pdf
        └── packing_list.pdf

Place real trade documents in incoming_shipments/SHP001/ before running.
"""

import json
import logging
import sys
import textwrap
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,          # suppress per-module debug noise in test output
    format="%(levelname)-8s  %(name)s  %(message)s",
)

# ── Imports ───────────────────────────────────────────────────────────────────
from agents.decision import decide
from config import APPROVAL_POLICY, RULES
from workflow.shipment import load_shipment
from workflow.shipment_processor import process_shipment
from workflow.shipment_validator import validate_shipment

# ── Constants ─────────────────────────────────────────────────────────────────
SHIPMENT_ID  = "SHP001"
SHIPMENT_DIR = Path("incoming_shipments") / SHIPMENT_ID

RULES_CONTEXT = json.dumps(
    {"customer": "GlobalTech Imports GmbH", "rules": RULES, "policy": APPROVAL_POLICY},
    indent=2,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _header(title: str) -> None:
    width = 72
    print()
    print("=" * width)
    print(f"  {title}")
    print("=" * width)


def _subheader(title: str) -> None:
    print()
    print(f"  ── {title} {'─' * max(0, 65 - len(title))}")


def _pp(data: object) -> None:
    """Pretty-print any JSON-serialisable object, indented by 4 spaces."""
    raw = json.dumps(data, indent=2, default=str)
    for line in raw.splitlines():
        print("    " + line)


def _abort(message: str) -> None:
    print(f"\n  [ABORT]  {message}\n")
    sys.exit(1)


# ── Step helpers ──────────────────────────────────────────────────────────────

def step_load() -> dict:
    _header(f"STEP 1 — Load Shipment  [{SHIPMENT_ID}]")

    if not SHIPMENT_DIR.exists():
        _abort(
            f"Shipment folder not found: {SHIPMENT_DIR}\n"
            "  Create the folder and add trade documents (PDF / PNG / JPG), then re-run."
        )

    try:
        shipment = load_shipment(SHIPMENT_DIR)
    except Exception as exc:
        _abort(f"load_shipment() failed: {exc}")

    if not shipment["documents"]:
        _abort(
            f"{SHIPMENT_DIR} exists but contains no supported documents.\n"
            "  Supported types: .pdf  .png  .jpg  .jpeg"
        )

    _subheader("Shipment Metadata")
    print(f"    shipment_id : {shipment['shipment_id']}")
    print(f"    status      : {shipment['status']}")
    print(f"    created_at  : {shipment['created_at']}")
    print(f"    documents   : {len(shipment['documents'])} file(s) detected")
    for doc in shipment["documents"]:
        print(f"                  • {doc['name']}  [{doc['doc_type']}]")

    return shipment


def step_process(shipment: dict) -> dict:
    _header("STEP 2 — Extract Document Fields")
    print("  Calling extractor agent for each document... (LLM calls in progress)")

    try:
        processed = process_shipment(shipment)
    except Exception as exc:
        _abort(f"process_shipment() failed unexpectedly: {exc}")

    _subheader("Extraction Results per Document")
    for doc_key, doc_result in processed["documents"].items():
        status_icon = "✓" if doc_result["status"] == "success" else "✗"
        print(f"\n    [{status_icon}] {doc_key}  ({doc_result['name']})")
        if doc_result["status"] == "success":
            for field, data in doc_result["fields"].items():
                value = data.get("value") or "—"
                conf  = data.get("confidence", 0.0)
                bar   = "●" if conf >= 0.7 else "○"
                print(f"         {bar}  {field:<25}  {str(value):<30}  {conf:.0%}")
        else:
            print(f"         [FAILED] {doc_result.get('error', 'unknown error')}")

    return processed


def step_validate(processed: dict) -> dict:
    _header("STEP 3 — Validate Shipment")

    try:
        validation_report = validate_shipment(processed)
    except Exception as exc:
        _abort(f"validate_shipment() failed unexpectedly: {exc}")

    summary = validation_report["validation_summary"]
    _subheader("Validation Summary")
    print(f"    documents validated  : {summary['documents_validated']}")
    print(f"    documents skipped    : {summary['documents_skipped']}")
    print(f"    cross discrepancies  : {summary['cross_discrepancies']}")

    _subheader("Per-Document Rule Validation")
    for doc_key, dv in validation_report["document_validations"].items():
        status_icon = {"auto_approve": "✓", "flag_for_review": "⚠", "amendment_required": "✗"}.get(dv["status"], "?")
        print(f"\n    [{status_icon}] {doc_key}  →  {dv['status']}")
        for field, result in dv["fields"].items():
            icon = {"match": "✓", "mismatch": "✗", "uncertain": "⚠"}.get(result["status"], "?")
            print(
                f"         {icon}  {field:<25}  "
                f"found={str(result.get('found') or '—'):<25}  "
                f"expected={str(result.get('expected') or '—')}"
            )

    _subheader("Cross-Document Discrepancies")
    discrepancies = validation_report["cross_document_discrepancies"]
    if not discrepancies:
        print("    No cross-document discrepancies found.")
    else:
        _pp(discrepancies)

    return validation_report


def step_decide(validation_report: dict) -> dict:
    _header("STEP 4 — Workflow Decision  [agents/decision.py]")

    # Flatten to a single merged field dict across all successfully validated docs.
    # When the same field appears in multiple docs, last-write wins — this is
    # intentional: the decision agent needs a unified view; discrepancies are
    # already surfaced by the cross-validator.
    merged_validation: dict = {}
    for dv in validation_report["document_validations"].values():
        merged_validation.update(dv["fields"])

    if not merged_validation:
        _abort("No validated fields available to make a decision (all documents failed extraction).")

    print("  Calling decision agent... (LLM reasoning in progress)")

    try:
        decision = decide(merged_validation, RULES_CONTEXT)
    except Exception as exc:
        _abort(f"decide() failed unexpectedly: {exc}")

    icon_map = {
        "auto_approve":       "🟢",
        "flag_for_review":    "🟡",
        "amendment_required": "🔴",
    }
    icon = icon_map.get(decision.get("decision", ""), "⚪")

    _subheader("Decision")
    print(f"    {icon}  Decision : {decision.get('decision', 'N/A').upper()}")
    print(f"    Reason   : {decision.get('reason', 'N/A')}")

    if decision.get("amendment_draft"):
        _subheader("Amendment Draft")
        _pp(decision["amendment_draft"])

    if decision.get("flagged_fields"):
        _subheader("Flagged Fields (Uncertain — Needs Human Review)")
        _pp(decision["flagged_fields"])

    return decision


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║   GoComet Nova Part 2 — End-to-End Shipment Workflow Test           ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"  Shipment  : {SHIPMENT_ID}")
    print(f"  Directory : {SHIPMENT_DIR.resolve()}")

    shipment          = step_load()
    processed         = step_process(shipment)
    validation_report = step_validate(processed)
    decision          = step_decide(validation_report)

    _header("WORKFLOW COMPLETE")
    final = decision.get("decision", "unknown").upper()
    print(f"\n  Shipment {SHIPMENT_ID}  →  Final Decision : {final}\n")


if __name__ == "__main__":
    main()
