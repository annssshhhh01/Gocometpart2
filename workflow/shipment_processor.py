"""
workflow/shipment_processor.py

Orchestrates document extraction across all documents in a shipment.

Concerns handled here:
  - Iterating over documents in a shipment object
  - Preparing document content (text / base64 images) for each file
  - Calling the Part 1 extractor agent per document
  - Aggregating per-document results into a shipment-level structure
  - Isolating per-document failures so one bad file cannot fail the whole shipment

Concerns intentionally NOT handled here:
  - Extraction logic itself  (→ agents/extractor.py)
  - Cross-document field validation  (→ future validator layer)
  - Database persistence
  - Streamlit / UI code
  - Async or threaded processing
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Part 1 extractor — reused unchanged
# ---------------------------------------------------------------------------
from agents.extractor import extract_fields

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported MIME / extension helpers
# ---------------------------------------------------------------------------

_IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg"})
_PDF_SUFFIX = ".pdf"


def _prepare_document_inputs(doc_path: Path) -> tuple[str, list[str]]:
    """Read a document file and return ``(text, images)`` ready for the extractor.

    - PDF  → extract text with pypdf; images list is empty (DLL-safe, same as app.py)
    - Image → text is empty; images contains one base64-encoded string

    Args:
        doc_path: Absolute path to the document file.

    Returns:
        A ``(text, images)`` tuple matching the signature of ``extract_fields``.

    Raises:
        ValueError: If the file extension is not supported.
        FileNotFoundError: If the file does not exist.
    """
    if not doc_path.exists():
        raise FileNotFoundError(f"Document not found: {doc_path}")

    suffix = doc_path.suffix.lower()

    if suffix == _PDF_SUFFIX:
        try:
            import pypdf  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "pypdf is required for PDF processing. "
                "Install it with: pip install pypdf"
            ) from exc

        pdf_bytes = doc_path.read_bytes()
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text_parts = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(text_parts)
        return text, []

    if suffix in _IMAGE_SUFFIXES:
        img_bytes = doc_path.read_bytes()
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        return "", [b64]

    raise ValueError(f"Unsupported file extension '{suffix}' for: {doc_path.name}")


# ---------------------------------------------------------------------------
# Per-document extraction
# ---------------------------------------------------------------------------


def _extract_document(doc: dict) -> dict:
    """Run extraction for a single document entry from the shipment.

    Args:
        doc: A document dictionary as produced by ``load_shipment``, containing
             at minimum ``name``, ``path``, and ``doc_type``.

    Returns:
        A result dictionary with the following keys:

        - ``name``       : original filename
        - ``doc_type``   : detected document type
        - ``status``     : ``"success"`` or ``"failed"``
        - ``fields``     : extracted field dict (empty dict on failure)
        - ``error``      : error message string (``None`` on success)
    """
    doc_path = Path(doc["path"])
    doc_name = doc["name"]
    doc_type = doc["doc_type"]

    logger.info("Extracting document: %s (type=%s)", doc_name, doc_type)

    result: dict[str, Any] = {
        "name": doc_name,
        "doc_type": doc_type,
        "status": "failed",
        "fields": {},
        "error": None,
    }

    try:
        text, images = _prepare_document_inputs(doc_path)
        extracted = extract_fields(text, images)
        result["fields"] = extracted
        result["status"] = "success"
        logger.info(
            "Extraction succeeded: %s — %d fields extracted",
            doc_name,
            len(extracted),
        )
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        logger.error(
            "Extraction failed for %s: %s", doc_name, exc, exc_info=True
        )

    return result


# ---------------------------------------------------------------------------
# Shipment-level orchestrator
# ---------------------------------------------------------------------------


def process_shipment(shipment: dict) -> dict:
    """Orchestrate document extraction across an entire shipment.

    Iterates every document in ``shipment["documents"]``, calls the Part 1
    extractor agent for each one, and aggregates results into a single
    shipment-level dictionary.

    A failure in one document does **not** abort the rest of the shipment;
    failed documents are marked individually with ``status: "failed"`` and an
    ``error`` message so the caller can decide how to handle them.

    Args:
        shipment: A shipment dictionary as produced by ``load_shipment``,
                  with keys ``shipment_id``, ``status``, ``created_at``,
                  and ``documents`` (list of document dicts).

    Returns:
        A shipment-level result dictionary::

            {
                "shipment_id": "SHP001",
                "status": "processing_complete",
                "documents": {
                    "invoice": {
                        "name": "invoice.pdf",
                        "doc_type": "invoice",
                        "status": "success",
                        "fields": { ... },
                        "error": null
                    },
                    "bol": { ... },
                    "packing_list": { ... }
                }
            }

        If multiple documents share the same ``doc_type`` (e.g. two invoices),
        a numeric suffix is appended to avoid silent overwrites:
        ``"invoice"``, ``"invoice_2"``, etc.
    """
    shipment_id: str = shipment.get("shipment_id", "UNKNOWN")
    docs: list[dict] = shipment.get("documents", [])

    logger.info(
        "Processing shipment %s — %d document(s)", shipment_id, len(docs)
    )

    aggregated: dict[str, dict] = {}
    doc_type_counts: dict[str, int] = {}

    for doc in docs:
        doc_result = _extract_document(doc)
        doc_type: str = doc_result["doc_type"]

        # Build a unique key when doc_type appears more than once
        doc_type_counts[doc_type] = doc_type_counts.get(doc_type, 0) + 1
        count = doc_type_counts[doc_type]
        key = doc_type if count == 1 else f"{doc_type}_{count}"

        aggregated[key] = doc_result

    total = len(docs)
    succeeded = sum(1 for r in aggregated.values() if r["status"] == "success")
    failed = total - succeeded

    logger.info(
        "Shipment %s processing complete — %d/%d succeeded, %d failed",
        shipment_id,
        succeeded,
        total,
        failed,
    )

    return {
        "shipment_id": shipment_id,
        "status": "processing_complete",
        "documents": aggregated,
    }
