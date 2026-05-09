"""
workflow/cross_validator.py

Deterministic cross-document consistency validation for a processed shipment.

Concerns handled here:
  - Extracting comparable field values from each document's extraction result
  - Normalising values before comparison (whitespace, case, numeric units)
  - Detecting mismatches, partial inconsistencies, and universally-missing fields
  - Returning a structured list of discrepancies

Concerns intentionally NOT handled here:
  - Extraction logic  (→ agents/extractor.py)
  - LLM-based reasoning or scoring
  - Embeddings / vector search
  - Database persistence
  - Streamlit / UI code
  - Async or threaded processing
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fields to validate and how they map to extractor output keys
# ---------------------------------------------------------------------------
#
# Each entry defines:
#   user_label   – the name surfaced in discrepancy reports
#   extractor_key – the key used inside doc["fields"]  (from agents/extractor.py)
#   compare_mode  – normalisation strategy applied before equality check:
#                     "text"    – lower + collapse whitespace
#                     "numeric" – strip units, compare as float strings

_VALIDATION_TARGETS: list[dict[str, str]] = [
    {"user_label": "invoice_number",   "extractor_key": "invoice_number",    "compare_mode": "text"},
    {"user_label": "consignee",        "extractor_key": "consignee_name",     "compare_mode": "text"},
    {"user_label": "hs_code",          "extractor_key": "hs_code",            "compare_mode": "text"},
    {"user_label": "gross_weight",     "extractor_key": "gross_weight",       "compare_mode": "numeric"},
    {"user_label": "origin_port",      "extractor_key": "port_of_loading",    "compare_mode": "text"},
    {"user_label": "destination_port", "extractor_key": "port_of_discharge",  "compare_mode": "text"},
]

# ---------------------------------------------------------------------------
# Value normalisation helpers
# ---------------------------------------------------------------------------


def _normalise_text(raw: Any) -> str | None:
    """Lower-case and collapse internal whitespace.  Returns None for null/empty."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return re.sub(r"\s+", " ", s).lower()


def _normalise_numeric(raw: Any) -> str | None:
    """Strip non-numeric characters (units, commas) and return a canonical
    float string so that "500 kg", "500KG", and "500.00" all compare equal.
    Returns None if no numeric content is found."""
    if raw is None:
        return None
    s = str(raw).strip()
    # Extract the first contiguous run of digits (and optional decimal point)
    match = re.search(r"[\d,]+\.?\d*", s.replace(",", ""))
    if not match:
        return None
    try:
        return str(float(match.group().replace(",", "")))
    except ValueError:
        return None


def _normalise(value: Any, mode: str) -> str | None:
    if mode == "numeric":
        return _normalise_numeric(value)
    return _normalise_text(value)


# ---------------------------------------------------------------------------
# Field-value extraction from processed documents
# ---------------------------------------------------------------------------


def _get_field_value(doc_result: dict, extractor_key: str) -> Any:
    """Pull the raw value for *extractor_key* from a processed document dict.

    Returns ``None`` if the document failed extraction, the key is absent, or
    the stored value is explicitly null.
    """
    if doc_result.get("status") != "success":
        return None
    fields = doc_result.get("fields", {})
    field_data = fields.get(extractor_key)
    if not isinstance(field_data, dict):
        return None
    return field_data.get("value")


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------


def validate_shipment_consistency(processed_shipment: dict) -> list[dict]:
    """Compare extracted fields across all documents in a processed shipment.

    Iterates each configured validation target, collects the (normalised) value
    from every document that successfully extracted it, and flags any
    inconsistency as a discrepancy.

    Three discrepancy statuses are possible:

    * ``"mismatch"``             – at least two documents disagree on the value
    * ``"missing_in_some"``      – some docs have the value, others do not
    * ``"missing_in_all"``       – no document produced a value (skipped silently)

    Fields missing in *all* documents are omitted from the output so the caller
    only sees actionable findings.

    Args:
        processed_shipment: The dictionary returned by
            ``shipment_processor.process_shipment``, with keys
            ``shipment_id`` and ``documents`` (a dict keyed by doc_type label).

    Returns:
        A list of discrepancy dicts, each with::

            {
                "field":     "<user_label>",
                "documents": {"invoice": "<raw value>", "bol": "<raw value>", ...},
                "status":    "mismatch" | "missing_in_some"
            }

        Returns an empty list when all validated fields are consistent.
    """
    shipment_id: str = processed_shipment.get("shipment_id", "UNKNOWN")
    docs: dict[str, dict] = processed_shipment.get("documents", {})

    logger.info(
        "Cross-validating shipment %s across %d document(s)",
        shipment_id,
        len(docs),
    )

    discrepancies: list[dict] = []

    for target in _VALIDATION_TARGETS:
        user_label = target["user_label"]
        extractor_key = target["extractor_key"]
        compare_mode = target["compare_mode"]

        # Collect raw and normalised values per document
        raw_values: dict[str, Any] = {}          # doc_key → raw value (for report)
        norm_values: dict[str, str | None] = {}  # doc_key → normalised value

        for doc_key, doc_result in docs.items():
            raw = _get_field_value(doc_result, extractor_key)
            raw_values[doc_key] = raw
            norm_values[doc_key] = _normalise(raw, compare_mode)

        present = {k: v for k, v in norm_values.items() if v is not None}
        absent  = {k for k, v in norm_values.items() if v is None}

        # Skip fields not found anywhere — nothing actionable to report
        if not present:
            logger.debug("Field '%s': absent in all documents — skipping", user_label)
            continue

        unique_norm_values = set(present.values())
        has_mismatch = len(unique_norm_values) > 1
        has_missing  = bool(absent)

        if has_mismatch:
            status = "mismatch"
        elif has_missing:
            status = "missing_in_some"
        else:
            # All present docs agree — no discrepancy
            logger.debug(
                "Field '%s': consistent across %d document(s) — value=%r",
                user_label,
                len(present),
                next(iter(present.values())),
            )
            continue

        discrepancy: dict[str, Any] = {
            "field": user_label,
            "documents": {
                doc_key: raw_values[doc_key]   # surface raw (human-readable) value
                for doc_key in docs
            },
            "status": status,
        }

        logger.warning(
            "Field '%s' — status=%s | values=%s",
            user_label,
            status,
            {k: norm_values[k] for k in docs},
        )
        discrepancies.append(discrepancy)

    logger.info(
        "Cross-validation complete for shipment %s — %d discrepancy/ies found",
        shipment_id,
        len(discrepancies),
    )

    return discrepancies
