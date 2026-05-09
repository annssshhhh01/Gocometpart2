from typing import TypedDict
from langgraph.graph import StateGraph, END

from agents.extractor import extract_fields
from agents.validator import validate
from agents.decision import decide
from rag import get_rules_context
from config import RULES


# ── State ──────────────────────────────────────────────────────────────────────
class State(TypedDict):
    text: str
    images: list  # base64-encoded page images
    extracted: dict
    validated: dict
    decision: dict


# ── Nodes ──────────────────────────────────────────────────────────────────────
def extractor_node(state: State) -> State:
    extracted = extract_fields(state.get("text", ""), state.get("images"))
    return {"extracted": extracted}


def validator_node(state: State) -> State:
    validated = validate(state.get("extracted", {}), RULES)
    return {"validated": validated}


def decision_node(state: State) -> State:
    validated = state.get("validated", {})
    rules_context = get_rules_context(str(validated))
    decision = decide(validated, rules_context)
    return {"decision": decision}


# ── Graph ──────────────────────────────────────────────────────────────────────
builder = StateGraph(State)

builder.add_node("extractor", extractor_node)
builder.add_node("validator", validator_node)
builder.add_node("decision", decision_node)

builder.set_entry_point("extractor")
builder.add_edge("extractor", "validator")
builder.add_edge("validator", "decision")
builder.add_edge("decision", END)

graph = builder.compile()
