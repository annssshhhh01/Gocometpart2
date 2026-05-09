from difflib import SequenceMatcher


def _check_incoterms(found: str, expected: str) -> str:
    """CIF (Cost, Insurance & Freight) == CIF"""
    if not found:
        return "mismatch"
    found_code = found.strip().split()[0].upper()
    expected_code = expected.strip().split()[0].upper()
    return "match" if found_code == expected_code else "mismatch"


def _check_variants(found: str, variants: list[str]) -> bool:
    """Check if found value matches any acceptable variant (fuzzy)."""
    if not found:
        return False
    found_lower = found.lower().strip()
    for variant in variants:
        v_lower = variant.lower().strip()
        if found_lower == v_lower or v_lower in found_lower or found_lower in v_lower:
            return True
        if SequenceMatcher(None, found_lower, v_lower).ratio() > 0.6:
            return True
    return False


def _fuzzy_match(a: str, b: str) -> bool:
    """Match if similar OR if one contains the other."""
    if not a or not b:
        return False
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()
    if b_lower in a_lower or a_lower in b_lower:
        return True
    return SequenceMatcher(None, a_lower, b_lower).ratio() > 0.6


def validate(data: dict, rules: dict) -> dict:
    results = {}

    for field, rule in rules.items():
        field_data = data.get(field, {})
        found_value = field_data.get("value")
        confidence = field_data.get("confidence", 0.0)
        match_type = rule.get("match_type", "exact")
        variants = rule.get("acceptable_variants", [])

        # Low confidence → uncertain regardless
        if confidence < 0.7:
            status = "uncertain"
        elif field == "incoterms":
            expected = rule.get("expected", "")
            status = _check_incoterms(str(found_value), expected)
        elif variants:
            # Check against acceptable variants first
            if _check_variants(str(found_value), variants):
                status = "match"
            else:
                status = "mismatch"
        elif match_type == "exact":
            expected = rule.get("expected", "")
            if str(found_value).strip().lower() == str(expected).strip().lower():
                status = "match"
            else:
                status = "mismatch"
        elif match_type == "fuzzy":
            expected = rule.get("expected", "")
            if _fuzzy_match(str(found_value), str(expected)):
                status = "match"
            else:
                status = "mismatch"
        elif match_type == "prefix":
            prefix = rule.get("expected_prefix", "")
            if str(found_value).strip().upper().startswith(prefix.upper()):
                status = "match"
            else:
                status = "mismatch"
        else:
            status = "uncertain"

        expected_display = rule.get("expected", rule.get("expected_prefix", "—"))

        results[field] = {
            "status": status,
            "expected": expected_display,
            "found": found_value,
            "confidence": confidence,
            "match_type": match_type,
        }

    return results
