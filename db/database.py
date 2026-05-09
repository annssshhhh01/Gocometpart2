import os
import sqlite3
import json
from datetime import datetime
from core.llm import call_llama

os.makedirs("db", exist_ok=True)
DB_PATH = "db/results.db"

SCHEMA = """
Table: results
Columns:
  id          INTEGER PRIMARY KEY
  extracted   TEXT (JSON with fields: consignee_name, hs_code, port_of_loading, port_of_discharge, incoterms, description_of_goods, gross_weight, invoice_number — each has value and confidence)
  validated   TEXT (JSON with per-field status: match/mismatch/uncertain, expected, found, confidence)
  decision    TEXT (JSON with decision: auto_approve/flag_for_review/amendment_required, reason, amendment_draft)
  created_at  TEXT (ISO timestamp)
"""


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            extracted   TEXT,
            validated   TEXT,
            decision    TEXT,
            created_at  TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_result(extracted: dict, validated: dict, decision: dict):
    conn = _get_conn()
    conn.execute(
        "INSERT INTO results (extracted, validated, decision, created_at) VALUES (?, ?, ?, ?)",
        (
            json.dumps(extracted),
            json.dumps(validated),
            json.dumps(decision),
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_all_results() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM results ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_flagged() -> int:
    conn = _get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM results WHERE json_extract(decision, '$.decision') = 'flag_for_review'"
    ).fetchone()
    conn.close()
    return row[0]


def count_by_decision() -> dict:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT json_extract(decision, '$.decision') as d, COUNT(*) as c FROM results GROUP BY d"
    ).fetchall()
    conn.close()
    return {row["d"]: row["c"] for row in rows}


def query_nl(question: str) -> str:
    """Natural language query over stored results using LLM-generated SQL."""
    prompt = f"""You are a SQL assistant. Given this SQLite schema:
{SCHEMA}

User question: "{question}"

Rules:
- Write a single SELECT query to answer the question.
- Use json_extract() for JSON fields. Example: json_extract(decision, '$.decision')
- Return ONLY the SQL query, nothing else. No explanation. No markdown."""

    try:
        sql = call_llama(prompt).strip()
        # Clean markdown fences
        if sql.startswith("```"):
            sql = sql.split("```")[1]
            if sql.startswith("sql"):
                sql = sql[3:]
            sql = sql.strip()

        conn = _get_conn()
        rows = conn.execute(sql).fetchall()
        conn.close()

        if not rows:
            return "No results found."

        result_lines = []
        for row in rows:
            result_lines.append(" | ".join(str(v) for v in dict(row).values()))

        return f"Query: {sql}\n\nResult:\n" + "\n".join(result_lines)

    except Exception as e:
        return f"Could not answer: {e}"
