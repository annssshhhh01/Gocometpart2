"""
workflow/shipment.py

Responsible for shipment loading and metadata construction.

Concerns handled here:
  - Discovering supported documents inside a shipment folder
  - Inferring document type from filename
  - Building a structured shipment dictionary

Concerns intentionally NOT handled here:
  - Document content extraction
  - LLM calls
  - Validation logic
  - Watchdog / filesystem monitoring
  - Database persistence
  - Streamlit / UI code
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Directory constants
# ---------------------------------------------------------------------------

INCOMING_DIR: Path = Path("incoming_shipments")
PROCESSED_DIR: Path = Path("processed_shipments")

# ---------------------------------------------------------------------------
# Supported file extensions
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg"}
)

# ---------------------------------------------------------------------------
# Document-type detection
# ---------------------------------------------------------------------------


def detect_doc_type(filename: str) -> str:
    """Infer a canonical document type from a filename.

    Rules are evaluated top-to-bottom (most specific first) against the
    lowercased filename stem.  The first matching rule wins.

    Canonical types returned:
      - "freight_invoice"      – freight / freight_invoice
      - "commercial_invoice"   – commercial_invoice / commercial
      - "invoice"              – any remaining "invoice" filename
      - "bol"                  – bol / bill_of_lading / bill
      - "packing_list"         – packing / packing_list
      - "shipment_manifest"    – manifest / shipment_manifest
      - "customs_declaration"  – customs / declaration
      - "delivery_receipt"     – delivery / receipt
      - "unknown"              – anything else

    Args:
        filename: The bare filename (with or without extension).

    Returns:
        A lowercase document-type string.
    """
    stem = Path(filename).stem.lower()

    # ── Invoice family (specific → general) ───────────────────────────────────
    if "freight" in stem and "invoice" in stem:
        return "freight_invoice"
    if "commercial" in stem and "invoice" in stem:
        return "commercial_invoice"
    if "commercial" in stem:
        return "commercial_invoice"
    if "freight" in stem:
        return "freight_invoice"
    if "invoice" in stem:
        return "invoice"

    # ── Bill of Lading ────────────────────────────────────────────────────────
    if "bol" in stem or "bill" in stem or "lading" in stem:
        return "bol"

    # ── Packing list ──────────────────────────────────────────────────────────
    if "packing" in stem:
        return "packing_list"

    # ── Shipment manifest ─────────────────────────────────────────────────────
    if "manifest" in stem:
        return "shipment_manifest"

    # ── Customs declaration ───────────────────────────────────────────────────
    if "customs" in stem or "declaration" in stem:
        return "customs_declaration"

    # ── Delivery receipt ──────────────────────────────────────────────────────
    if "delivery" in stem or "receipt" in stem:
        return "delivery_receipt"

    return "unknown"


# ---------------------------------------------------------------------------
# Shipment loader
# ---------------------------------------------------------------------------


def load_shipment(shipment_path: Path) -> dict:
    """Build a shipment metadata dictionary from a folder of documents.

    Scans *shipment_path* for supported files, skips hidden files and any
    file whose extension is not in SUPPORTED_EXTENSIONS, then assembles a
    structured shipment record.

    The returned dictionary has the following shape::

        {
            "shipment_id": "SHP001",
            "status": "incoming",
            "created_at": "<ISO-8601 UTC timestamp>",
            "documents": [
                {
                    "name": "invoice.pdf",
                    "path": "<absolute path as string>",
                    "doc_type": "invoice"
                },
                ...
            ]
        }

    Args:
        shipment_path: ``pathlib.Path`` pointing to the shipment folder
            (e.g. ``incoming_shipments/SHP001``).

    Returns:
        A shipment dictionary as described above.

    Raises:
        FileNotFoundError: If *shipment_path* does not exist or is not a
            directory.
    """
    shipment_path = shipment_path.resolve()

    if not shipment_path.exists():
        raise FileNotFoundError(
            f"Shipment directory not found: {shipment_path}"
        )
    if not shipment_path.is_dir():
        raise FileNotFoundError(
            f"Expected a directory, got a file: {shipment_path}"
        )

    documents: list[dict] = []

    for file in sorted(shipment_path.iterdir()):
        # Skip directories
        if not file.is_file():
            continue

        # Skip hidden files (e.g. .DS_Store, .gitkeep)
        if file.name.startswith("."):
            continue

        # Skip unsupported file types
        if file.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        documents.append(
            {
                "name": file.name,
                "path": str(file),
                "doc_type": detect_doc_type(file.name),
            }
        )

    return {
        "shipment_id": shipment_path.name,
        "status": "incoming",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "documents": documents,
    }
