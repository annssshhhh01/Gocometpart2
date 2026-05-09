import json
from core.llm import call_llama, call_vision

FIELDS = [
    "consignee_name", "hs_code", "port_of_loading", "port_of_discharge",
    "incoterms", "description_of_goods", "gross_weight", "invoice_number"
]

PROMPT_TEMPLATE = """Extract the following fields from the trade document below.
Return STRICT JSON only. No explanation. No markdown.

Fields to extract: {fields}

Rules:
- Each field must have: "value" and "confidence" (0.0 to 1.0)
- If a field is not found: value=null, confidence=0.0
- Do NOT guess. Only extract what is clearly stated.

{doc_section}

Return format:
{{
  "field_name": {{"value": "...", "confidence": 0.95}},
  ...
}}"""


def clean_json(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def ensure_all_fields(data: dict) -> dict:
    for field in FIELDS:
        if field not in data:
            data[field] = {"value": None, "confidence": 0.0}
    return data


def extract_fields(text: str, images: list[str] | None = None) -> dict:
    """
    Extract fields from a trade document.
    - text: extracted text from the document
    - images: list of base64-encoded page images (optional)
    """
    fields_str = ", ".join(FIELDS)

    # If we have images, use vision LLM on the first page image
    if images and len(images) > 0:
        prompt = PROMPT_TEMPLATE.format(
            fields=fields_str,
            doc_section="Look at the attached document image and extract the fields.",
        )
        # Try with vision
        raw = clean_json(call_vision(prompt, images[0]))
        try:
            return ensure_all_fields(json.loads(raw))
        except json.JSONDecodeError:
            pass

        # Retry once
        raw = clean_json(call_vision(prompt, images[0]))
        try:
            return ensure_all_fields(json.loads(raw))
        except json.JSONDecodeError:
            # Fall through to text-based extraction
            pass

    # Text-based extraction (fallback or when no images)
    if text and text.strip():
        prompt = PROMPT_TEMPLATE.format(
            fields=fields_str,
            doc_section=f"Document:\n{text}",
        )
        raw = clean_json(call_llama(prompt))
        try:
            return ensure_all_fields(json.loads(raw))
        except json.JSONDecodeError:
            pass

        # Retry once
        raw = clean_json(call_llama(prompt))
        try:
            return ensure_all_fields(json.loads(raw))
        except json.JSONDecodeError as e:
            raise ValueError(f"Extractor failed after 2 attempts: {e}\nRaw: {raw}")

    raise ValueError("No text or images provided to extractor")
