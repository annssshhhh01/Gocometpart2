"""
workflow/email_drafter.py

Generates a short, human, operationally-toned CG reply email.

Style goal: reads like a real cargo-ops team message — concise, scannable,
actionable. Not a compliance audit report.

No LLM calls. No SMTP. CG reviews the draft and clicks Send.
"""

from __future__ import annotations

_MAX_BULLETS = 4          # surface at most this many issue bullets
_CG_SIGN_OFF = "GoComet Nova · CG Validation Team"


def _subject(shipment_id: str, decision: str) -> str:
    tag = {
        "auto_approve":       "Documents Approved",
        "flag_for_review":    "Clarification Needed",
        "amendment_required": "Amendment Required",
    }.get(decision, "Shipment Update")
    return f"Re: Shipment {shipment_id} — {tag}"


def _top_issues(decision: dict, cross_disc: list) -> list[str]:
    """Collect the most actionable issues, capped at _MAX_BULLETS."""
    bullets: list[str] = []

    # Amendment items first (most explicit)
    for item in decision.get("amendment_draft", []):
        field    = item.get("field", "").replace("_", " ")
        found    = item.get("found") or "—"
        expected = item.get("expected") or "—"
        bullets.append(f"{field.title()}: found '{found}', expected '{expected}'")
        if len(bullets) >= _MAX_BULLETS:
            return bullets

    # Cross-doc discrepancies
    for d in cross_disc:
        field  = d.get("field", "").replace("_", " ").title()
        status = d.get("status", "")
        if status == "mismatch":
            vals = d.get("documents", {})
            vals_str = " vs ".join(f"{k}: {v}" for k, v in vals.items() if v)
            bullets.append(f"{field} mismatch ({vals_str})")
        elif status == "missing_in_some":
            missing_docs = [k for k, v in d.get("documents", {}).items() if v is None]
            bullets.append(f"{field} missing in: {', '.join(missing_docs)}")
        if len(bullets) >= _MAX_BULLETS:
            return bullets

    # Uncertain / low-confidence fields
    for f in decision.get("flagged_fields", []):
        field = f.get("field", "").replace("_", " ").title()
        conf  = f.get("confidence", 0.0)
        bullets.append(f"{field} — low confidence ({conf:.0%}), please provide clearer copy")
        if len(bullets) >= _MAX_BULLETS:
            return bullets

    return bullets


def draft_email(
    shipment_id: str,
    decision: dict,
    validation_report: dict,
    supplier_name: str = "Team",
    cg_team: str = _CG_SIGN_OFF,
) -> dict:
    """Return a short, human-toned CG reply email dict.

    Returns:
        ``{"subject": str, "body": str, "decision": str}``
    """
    dec_val    = decision.get("decision", "unknown")
    cross_disc = validation_report.get("cross_document_discrepancies", [])
    summary    = validation_report.get("validation_summary", {})
    n_docs     = summary.get("documents_validated", "—")

    subject = _subject(shipment_id, dec_val)
    lines   = [f"Hi {supplier_name},", ""]

    # ── Body by decision type ─────────────────────────────────────────────────
    if dec_val == "auto_approve":
        lines += [
            f"We've reviewed the documents for shipment {shipment_id} "
            f"across {n_docs} file(s) — everything checks out.",
            "",
            "The shipment is approved. No further action needed from your end.",
        ]

    elif dec_val == "flag_for_review":
        issues = _top_issues(decision, cross_disc)
        lines += [
            f"We've reviewed shipment {shipment_id} but need a few clarifications "
            "before we can approve:",
            "",
        ]
        for b in issues:
            lines.append(f"  • {b}")
        if not issues:
            lines.append("  • Some fields had low confidence — please send clearer copies.")
        lines += [
            "",
            "Please share updated documents or a quick note addressing the above.",
        ]

    else:  # amendment_required
        issues = _top_issues(decision, cross_disc)
        lines += [
            f"We found discrepancies in shipment {shipment_id} that need correction "
            "before we can proceed:",
            "",
        ]
        for b in issues:
            lines.append(f"  • {b}")
        if not issues:
            lines.append("  • One or more fields did not match the required values.")
        lines += [
            "",
            "Please correct the above and resubmit the full document set.",
        ]

    # ── Sign-off ──────────────────────────────────────────────────────────────
    lines += [
        "",
        f"Regards,",
        cg_team,
    ]

    return {
        "subject":  subject,
        "body":     "\n".join(lines),
        "decision": dec_val,
    }
