import math
from collections import Counter

# ── Hardcoded trade compliance rules ──────────────────────────────────────────
RULES = [
    "Incoterms must be CIF for all sea freight shipments.",
    "Port of discharge must be Nhava Sheva for India-bound cargo.",
    "HS code must match the declared description of goods.",
    "Gross weight on invoice must not exceed the bill of lading weight by more than 5%.",
    "Invoice number must be unique and referenced in all shipping documents.",
]


# ── Pure Python TF-IDF cosine similarity ──────────────────────────────────────
def _tokenize(text: str) -> list[str]:
    return text.lower().split()


def _tf(tokens: list[str]) -> dict:
    counts = Counter(tokens)
    total = len(tokens)
    return {w: c / total for w, c in counts.items()}


def _cosine(vec_a: dict, vec_b: dict) -> float:
    common = set(vec_a) & set(vec_b)
    dot = sum(vec_a[w] * vec_b[w] for w in common)
    mag_a = math.sqrt(sum(v ** 2 for v in vec_a.values()))
    mag_b = math.sqrt(sum(v ** 2 for v in vec_b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# Pre-compute rule vectors at import time
_rule_vecs = [_tf(_tokenize(r)) for r in RULES]


# ── Retrieval function ─────────────────────────────────────────────────────────
def get_rules_context(query: str, top_k: int = 2) -> str:
    query_vec = _tf(_tokenize(query))
    scores = [_cosine(query_vec, rv) for rv in _rule_vecs]
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return " ".join(RULES[i] for i in top_indices)
