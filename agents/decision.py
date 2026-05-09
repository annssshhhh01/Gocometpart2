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

    # Step 4: LLM generates reasoning
    prompt = f"""You are a trade compliance assistant.

Validation result:
{json.dumps(validation, indent=2)}

Applicable rules:
{rules_context}

Decision already made: {decision}

Explain in 1-2 lines based only on validation and rules. Do not add extra information."""

    try:
        reason = call_llama(prompt).strip()
    except Exception:
        reason = "Reasoning unavailable due to LLM failure."

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
