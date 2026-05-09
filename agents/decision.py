import json
from core.llm import call_llama


def decide(validation: dict, rules_context: str) -> dict:
    # Step 1: Python logic — strict priority
    statuses = [v["status"] for v in validation.values()]

    if "uncertain" in statuses:
        decision = "flag_for_review"
    elif "mismatch" in statuses:
        decision = "amendment_required"
    else:
        decision = "auto_approve"

    # Step 2: ALWAYS collect mismatches (found vs expected)
    amendment_draft = []
    for field, result in validation.items():
        if result["status"] == "mismatch":
            amendment_draft.append({
                "field": field,
                "expected": result["expected"],
                "found": result["found"],
                "action": f"Correct '{field}' from '{result['found']}' to '{result['expected']}'"
            })

    # Step 3: ALWAYS surface uncertain fields — never silently approve
    flagged_fields = []
    for field, result in validation.items():
        if result["status"] == "uncertain":
            flagged_fields.append({
                "field": field,
                "found": result["found"],
                "confidence": result["confidence"],
                "reason": f"Low confidence ({result['confidence']:.0%}) — needs human verification"
            })

    # Step 4: LLM generates reasoning (with smart fallback — never shows "LLM failure")
    def _rule_based_reason(decision: str, validation: dict) -> str:
        """Generate a deterministic reason so the UI always has something meaningful."""
        if decision == "auto_approve":
            return (
                "All extracted fields match the customer rule set. "
                "No discrepancies or low-confidence values detected — shipment approved."
            )
        if decision == "flag_for_review":
            uncertain = [f.replace("_", " ") for f, v in validation.items() if v.get("status") == "uncertain"]
            fields_str = ", ".join(uncertain[:3]) or "one or more fields"
            return (
                f"Low extraction confidence on: {fields_str}. "
                "Cannot auto-approve until these are verified by CG."
            )
        # amendment_required
        mismatches = [f.replace("_", " ") for f, v in validation.items() if v.get("status") == "mismatch"]
        fields_str = ", ".join(mismatches[:3]) or "one or more fields"
        return (
            f"Discrepancies found in: {fields_str}. "
            "Values do not match the customer's required specifications."
        )

    # Trim validation to avoid token limit on large multi-doc shipments
    MAX_FIELDS = 12
    trimmed_validation = dict(list(validation.items())[:MAX_FIELDS])

    prompt = f"""You are a trade compliance assistant.

Validation result:
{json.dumps(trimmed_validation, indent=2)}

Decision already made: {decision}

Explain in 1-2 lines why this decision was made, based only on the validation result above. Be specific about which fields caused it. No filler text."""

    try:
        reason = call_llama(prompt).strip()
        if not reason:
            reason = _rule_based_reason(decision, validation)
    except Exception:
        reason = _rule_based_reason(decision, validation)

    output = {
        "decision": decision,
        "reason": reason,
    }

    # Always include both — mismatches AND uncertain fields surfaced
    if amendment_draft:
        output["amendment_draft"] = amendment_draft
    if flagged_fields:
        output["flagged_fields"] = flagged_fields

    return output
