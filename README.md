# GoComet Nova — CG Shipment Validation Console

> Full-stack AI engineer take-home · Part 1 + Part 2

---

## PRD — One Page

### The Problem

SU generates a shipment document set (BOL, Invoice, Packing List) and emails it to CG. CG opens every file, reads every field, mentally checks it against that customer's rules, types out what's wrong, and sends a reply. Two to four amendment cycles per shipment is normal. Each cycle costs 4–24 hours of delay with no audit trail.

### The Three People

| Who | Role | What they care about |
|---|---|---|
| **SU** (Shipping Unit) | Supplier / shipper | Docs go out, job is done. They want one clear reply — approved or here's exactly what to fix. |
| **CG** (Cargo / Control Group) | Validator | Rules compliance across every field, every document. They want the first draft reply to be 95% ready so they just review and click send. |
| **Customer** | End recipient | One clean, correct document set. A wrong HS code or mismatched consignee means customs delays and contract penalties. |

### JTBDs

> **CG:** When a new shipment arrives in my inbox, I want the system to have already cross-checked every field against the customer's rules and drafted my reply, so that I spend 2 minutes reviewing instead of 20 minutes reading.

> **SU:** When I resubmit corrected docs, I want a clear list of exactly which fields failed and what the expected values are, so that I fix the right things and don't loop back a third time.

### North-Star Metric

**Amendment cycles per shipment.** Today it's 2–4 loops before CG signs off. Get it to 1.

### Failure Mode

**Silent auto-approval of a wrong document set.** If the agent approves a shipment with an incorrect HS code or mismatched consignee, it causes customs delays and contract penalties downstream.

**How the system stops it:**
- Uncertain fields (low confidence) are flagged for review *before* mismatches are even checked
- `auto_approve` is only issued when *every* field matches *and* all confidences are ≥ 0.7
- **The agent never sends on its own.** CG always reviews the draft and clicks Approve & Send. This is non-negotiable.

---

## What It Does

### Part 1 — Single Document Pipeline

Three agents in sequence via LangGraph:

1. **Extractor** — sends the document to Llama 4 Scout (vision-capable), returns structured JSON with 8 fields and a confidence score for each
2. **Validator** — compares every field against the customer rule set. Fuzzy matching, exact matching, prefix matching. Ports like "JNPT Nhava Sheva" correctly match "JNPT Mumbai". Incoterms like "CIF (Cost, Insurance & Freight)" correctly match "CIF".
3. **Decision Agent** — picks one of three outcomes. Uncertain fields are flagged before mismatches are checked. Mismatches produce a field-by-field amendment draft. Clean docs get auto-approved with reasoning.

### Part 2 — Real Workflow Wiring

The trigger is the missing piece from Part 1. Part 2 connects the pipeline to a simulated SU inbox:

1. **Trigger** — Watchdog monitors `incoming_shipments/`. The moment a new shipment folder appears (simulating an email with attachments), the pipeline fires automatically in a background thread. No button. No polling.
2. **Multi-doc extraction** — handles BOL + Invoice + Packing List in a single shipment. Each document is extracted independently and labelled by type.
3. **Cross-document validation** — 6 fields (consignee, HS code, invoice number, gross weight, origin port, destination port) are compared across all documents. Mismatches surface as actionable discrepancies.
4. **Decide & Draft** — the Router Agent produces either a clean approval email or an amendment email listing every discrepancy with field name, found value, and expected value.
5. **Hand off** — every result is persisted to SQLite. CG can query "show me everything pending review" via the NL query layer in the Historical Dashboard.

---

## Project Structure

```
gocometpart2/
├── app.py                        # Streamlit UI — Part 1 + Part 2 console
├── graph.py                      # LangGraph pipeline (Part 1)
├── config.py                     # Customer rule set (GlobalTech Imports GmbH)
├── rag.py                        # TF-IDF retrieval for compliance rules
├── requirements.txt
│
├── agents/
│   ├── extractor.py              # Vision + text extraction with retry logic
│   ├── validator.py              # Fuzzy/exact/prefix matching against rules
│   └── decision.py               # Priority logic + LLM reasoning
│
├── workflow/                     # Part 2 modules
│   ├── watcher.py                # Watchdog observer — auto-triggers pipeline on new folder
│   ├── shipment.py               # Shipment folder loader + metadata
│   ├── shipment_processor.py     # Runs extractor on each doc in the shipment
│   ├── shipment_validator.py     # Runs cross-validator + per-doc validation
│   ├── cross_validator.py        # Cross-document field consistency checks
│   └── email_drafter.py          # Drafts CG reply email (no LLM, no SMTP)
│
├── core/
│   ├── llm.py                    # Groq API wrapper (text + vision calls)
│   └── parser.py                 # JSON cleaning utilities
│
├── db/
│   └── database.py               # SQLite persistence + NL query layer
│
└── incoming_shipments/           # Drop shipment folders here — watcher picks them up
```

---

## Getting It Running

### 1. Clone the repo

```bash
git clone https://github.com/annssshhhh01/miniNova.git
cd miniNova
```

### 2. Create a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

> **Note:** All dependencies are pure Python — no C extensions, no compiled libraries. Runs on restricted corporate environments where native DLLs get blocked.

### 4. Add your Groq API key

```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com/keys](https://console.groq.com/keys).

### 5. Run

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501).

---

## How to Test

### Part 2 — Shipment Operations (Watchdog trigger)

1. Go to **📦 Shipment Operations** tab
2. Upload any combination of PDF/image documents (invoice, BOL, packing list)
3. Files are saved to `incoming_shipments/<SHP-id>/`
4. **Watchdog detects the new folder automatically** — pipeline fires without any button click
5. The UI polls every 2 seconds and renders results when the pipeline completes
6. CG reviews the extraction results, discrepancies, decision, and **edits the AI-drafted email**
7. CG clicks **✉️ Approve & Send** — nothing goes to the client until this step

### Part 1 — Single Document

1. Go to **🔬 Single Document** tab
2. Upload one PDF or image
3. Click **Run Part 1 Pipeline**
4. See extracted fields, validation results, and decision

### Historical Dashboard

- Go to **📊 Historical Dashboard**
- See running counts by decision type
- Use the NL query box: `"how many shipments were flagged this week?"` or `"show me all amendment_required decisions"`

### Three test scenarios

| Document | What should happen |
|---|---|
| Clean doc — all fields match | 🟢 auto_approve |
| Degraded/scanned — some fields unreadable | 🟡 flag_for_review |
| Wrong Incoterms (FOB instead of CIF) | 🔴 amendment_required |

---

## The Customer Rule Set

Rules for **GlobalTech Imports GmbH** (CUST001), defined in `config.py`:

| Field | Expected | Match Type |
|---|---|---|
| Consignee Name | GlobalTech Imports GmbH | Fuzzy |
| HS Code | 8471.30 | Exact |
| Port of Loading | JNPT Mumbai | Fuzzy + variants |
| Port of Discharge | Hamburg | Fuzzy + variants |
| Incoterms | CIF | Code extraction |
| Description of Goods | Electronic Components | Fuzzy (contains) |
| Gross Weight | 500 KG | Fuzzy |
| Invoice Number | INV prefix | Prefix match |

---

## Stack

| Component | Technology |
|---|---|
| Pipeline orchestration | LangGraph |
| LLM + Vision | Groq · Llama 4 Scout 17B |
| Filesystem trigger | Watchdog |
| UI | Streamlit |
| Persistence | SQLite |
| PDF extraction | pypdf |
| RAG retrieval | Pure Python TF-IDF + cosine similarity |

No Docker. No database server. Just Python and a Groq API key.

---

## Known Limitations

- **Scanned PDFs** — pypdf handles text-based PDFs. For scans, upload pages as images directly for best results.
- **Single customer** — rule set is hardcoded for GlobalTech Imports GmbH. Multi-tenant rule management is the obvious next step.
- **RAG quality** — TF-IDF works but misses semantic similarity. A proper embedding model would improve retrieval accuracy.
- **Synchronous pipeline** — for bulk processing you'd run extractions in parallel. Currently each doc is extracted sequentially.
