"""
workflow/shipment_validator.py

Orchestrates all shipment-level validation by combining:
  1. Per-document customer-rule validation  (reuses Part 1 agents/validator.py)
  2. Cross-document consistency validation  (reuses workflow/cross_validator.py)

Concerns handled here:
  - Iterating successfully-extracted documents and running rule-based validation
  - Collecting per-document validation results with full traceability
  - Delegating cross-document field consistency checking
  - Aggregating both layers into a single shipment-level validation report

Concerns intentionally NOT handled here:
  - Extraction logic  (→ agents/extractor.py / shipment_processor.py)
  - Validation rule definitions  (→ config.py)
  - Decision / amendment routing  (→ agents/decision.py)
  - Database persistence
  - Streamlit / UI code
  - Async or threaded processing
"""

from __future__ import annotations

import logging
from typing import Any

from agents.validator import validate
from config import RULES
from workflow.cross_validator import validate_shipment_consistency

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_document(doc_key: str, doc_result: dict) -> dict | None:
    """Run customer-rule validation against a single successfully-extracted doc.

    Args:
        doc_key: The key used for this document in the processed shipment
                 (e.g. ``"invoice"``, ``"bol"``).
        doc_result: The per-document result dict produced by
                    ``shipment_processor._extract_document``.

    Returns:
        A validation result dict, or ``None`` if the document was not
        successfully extracted (in which case the caller skips it cleanly).
    """
    if doc_result.get("status") != "success":
        logger.warning(
            "Skipping rule validation for '%s' — extraction status: %s (error: %s)",
            doc_key,
            doc_result.get("status"),
            doc_result.get("error"),
        )
        return None

    fields: dict = doc_result.get("fields", {})
    doc_name: str = doc_result.get("name", doc_key)
    doc_type: str = doc_result.get("doc_type", doc_key)

    logger.info(
        "Running rule validation on '%s' (%s)", doc_name, doc_type
    )

    try:
        validation_result = validate(fields, RULES)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Rule validation failed for '%s': %s", doc_name, exc, exc_info=True
        )
        return {
            "doc_name": doc_name,
            "doc_type": doc_type,
            "status": "validation_error",
            "fields": {},
            "error": str(exc),
        }

    field_statuses = [v.get("status") for v in validation_result.values()]
    has_mismatch  = "mismatch"  in field_statuses
    has_uncertain = "uncertain" in field_statuses

    if has_mismatch:
        doc_status = "amendment_required"
    elif has_uncertain:
        doc_status = "flag_for_review"
    else:
        doc_status = "auto_approve"

    logger.info(
        "Rule validation complete for '%s' — overall status: %s",
        doc_name,
        doc_status,
    )

    return {
        "doc_name": doc_name,
        "doc_type": doc_type,
        "status": doc_status,
        "fields": validation_result,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


def validate_shipment(processed_shipment: dict) -> dict:
    """Orchestrate full shipment-level validation.

    Combines two validation layers:

    * **Per-document rule validation**: runs the Part 1 ``validate()`` function
      against the customer rule set (``config.RULES``) for every document that
      was successfully extracted.  Documents that failed extraction are skipped
      and noted in the summary.

    * **Cross-document consistency validation**: delegates to
      ``cross_validator.validate_shipment_consistency()`` to detect field
      mismatches or missing values across documents.

    Args:
        processed_shipment: The dictionary returned by
            ``shipment_processor.process_shipment``, with keys
            ``shipment_id`` and ``documents`` (a dict keyed by doc-type label).

    Returns:
        A shipment-level validation report::

            {
                "shipment_id": "SHP001",
                "document_validations": {
                    "invoice": {
                        "doc_name": "invoice.pdf",
                        "doc_type": "invoice",
                        "status": "auto_approve" | "flag_for_review" | "amendment_required",
                        "fields": { <field>: {status, expected, found, confidence, ...} },
                        "error": null
                    },
                    "bol": { ... }
                },
                "cross_document_discrepancies": [
                    {
                        "field": "hs_code",
                        "documents": {"invoice": "8471", "bol": "8542"},
                        "status": "mismatch"
                    },
                    ...
                ],
                "validation_summary": {
                    "documents_validated": 2,
                    "documents_skipped": 1,
                    "cross_discrepancies": 1
                }
            }
    """
    shipment_id: str = processed_shipment.get("shipment_id", "UNKNOWN")
    docs: dict[str, dict] = processed_shipment.get("documents", {})

    logger.info(
        "Starting shipment validation for %s — %d document(s) to process",
        shipment_id,
        len(docs),
    )

    # ── Layer 1: per-document rule validation ─────────────────────────────────
    document_validations: dict[str, Any] = {}
    skipped_count = 0

    for doc_key, doc_result in docs.items():
        result = _validate_document(doc_key, doc_result)
        if result is None:
            skipped_count += 1
        else:
            document_validations[doc_key] = result

    validated_count = len(document_validations)

    # ── Layer 2: cross-document consistency check ─────────────────────────────
    logger.info(
        "Running cross-document consistency check for shipment %s", shipment_id
    )
    cross_discrepancies = validate_shipment_consistency(processed_shipment)

    # ── Assemble report ───────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "shipment_id": shipment_id,
        "document_validations": document_validations,
        "cross_document_discrepancies": cross_discrepancies,
        "validation_summary": {
            "documents_validated": validated_count,
            "documents_skipped": skipped_count,
            "cross_discrepancies": len(cross_discrepancies),
        },
    }

    logger.info(
        "Shipment %s validation complete — validated=%d, skipped=%d, cross_discrepancies=%d",
        shipment_id,
        validated_count,
        skipped_count,
        len(cross_discrepancies),
    )

    return report
