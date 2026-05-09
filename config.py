# ── Customer Rule Set ──────────────────────────────────────────────────────────
CUSTOMER = {
    "customer_id": "CUST001",
    "customer_name": "GlobalTech Imports GmbH",
}

RULES = {
    "consignee_name": {
        "expected": "GlobalTech Imports GmbH",
        "match_type": "fuzzy",
    },
    "hs_code": {
        "expected": "8471.30",
        "match_type": "exact",
    },
    "port_of_loading": {
        "expected": "JNPT Mumbai",
        "match_type": "fuzzy",
        "acceptable_variants": [
            "JNPT Mumbai",
            "JNPT Nhava Sheva",
            "JNPT, Nhava Sheva",
            "Jawaharlal Nehru Port",
            "Nhava Sheva Mumbai",
            "Nhava Sheva",
        ],
    },
    "port_of_discharge": {
        "expected": "Hamburg",
        "match_type": "fuzzy",
        "acceptable_variants": [
            "Hamburg",
            "Hamburg Germany",
            "Port of Hamburg",
            "HH Port Germany",
            "HH Port, Germany",
            "Hamburger Hafen",
        ],
    },
    "incoterms": {
        "expected": "CIF",
        "match_type": "exact",
    },
    "description_of_goods": {
        "expected": "Electronic Components",
        "match_type": "fuzzy",
    },
    "gross_weight": {
        "expected": "500 KG",
        "match_type": "fuzzy",
    },
    "invoice_number": {
        "expected_prefix": "INV",
        "match_type": "prefix",
    },
}

APPROVAL_POLICY = {
    "auto_approve_threshold": 0.7,
    "auto_approve_condition": "ALL fields must be match with confidence above 0.7",
    "flag_condition": "ANY field is uncertain — confidence below 0.7",
    "amendment_condition": "ANY field is mismatch regardless of confidence",
    "silent_approval": False,
}
