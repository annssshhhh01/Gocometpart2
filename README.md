# GoComet Nova — Shipment Validation Pipeline

> An AI pipeline that reads shipment documents, checks them against a customer's rules, and drafts a reply email — automatically.

---

## The problem it solves

Every time a supplier sends a shipment document set (invoice, bill of lading, packing list), someone on the cargo team has to:

- Open every file
- Read every field manually
- Compare it against that customer's specific rules
- Write out what's wrong
- Send the reply and wait for a correction

This loop runs **2–4 times per shipment**. It's slow, error-prone, and fully manual.

This tool automates the reading, checking, and drafting parts. The human still reviews and clicks send — because that's the right call for compliance — but the heavy lifting is done.

---

## What it actually does

### Trigger

A new shipment arrives either via **Gmail** (email with PDF attachments) or **manual upload** in the UI. Either way, a folder gets created under `incoming_shipments/` and the pipeline fires automatically — no button click needed.

### Pipeline (runs in the background)

```
Documents → Extract Fields → Validate vs Rules → Cross-check docs → Decision → Draft Email
```

1. **Extract** — sends each document (PDF or image) to Llama 4 Scout via Groq. Gets back 8 key fields with confidence scores.
2. **Validate** — compares every field against the customer's rule set. Uses fuzzy matching, so "JNPT Nhava Sheva" correctly matches "JNPT Mumbai" and "CIF (Cost, Insurance & Freight)" correctly matches "CIF".
3. **Cross-check** — compares the same fields across multiple documents. If the weight on the invoice doesn't match the BOL, it flags it.
4. **Decide** — picks one of three outcomes:
   - ✅ `auto_approve` — everything checks out
   - 🟡 `flag_for_review` — some fields had low confidence
   - 🔴 `amendment_required` — actual mismatches found
5. **Draft email** — writes a ready-to-send reply in plain professional English. Lists exactly what's wrong and what the correct values should be.

### Human review

The CG (cargo team) sees all of this in the Streamlit dashboard. They can edit the draft, then click **Approve & Send**. The email goes out via Gmail SMTP. Nothing is sent automatically — ever.

### History

Every result is saved to a local SQLite database. The dashboard has a plain-English query box — type things like *"show me all amendment_required shipments"* and it returns results.

---

## Project structure

```
gocometpart2/
│
├── app.py                    # Streamlit UI — the main dashboard
├── config.py                 # Customer rules (what fields should look like)
├── graph.py                  # LangGraph pipeline for single-doc mode
├── rag.py                    # Simple TF-IDF rule retrieval
│
├── agents/
│   ├── extractor.py          # LLM field extraction (text + vision)
│   ├── validator.py          # Field comparison against rules
│   └── decision.py           # approve / flag / amendment logic + LLM reasoning
│
├── workflow/
│   ├── watcher.py            # Watches incoming_shipments/ and fires the pipeline
│   ├── shipment.py           # Loads a shipment folder into a structured object
│   ├── shipment_processor.py # Runs the extractor on every doc in a shipment
│   ├── shipment_validator.py # Per-doc validation + cross-doc consistency check
│   ├── cross_validator.py    # Compares fields across multiple documents
│   ├── email_drafter.py      # Builds the CG reply email (no LLM, pure logic)
│   ├── gmail_listener.py     # Polls Gmail inbox for new emails with attachments
│   └── smtp_sender.py        # Sends the reply when CG clicks Approve & Send
│
├── core/
│   └── llm.py                # Groq API wrapper (text + vision calls)
│
├── db/
│   └── database.py           # SQLite — saves every result, powers the dashboard
│
└── incoming_shipments/       # Drop folders here, watcher picks them up
```

---

## Running it locally

### 1. Clone

```bash
git clone https://github.com/annssshhhh01/miniNova.git
cd miniNova
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

Activate it:

```bash
# Windows
venv\Scripts\activate

# Mac / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up your `.env` file

Create a file called `.env` in the root of the project:

```env
GROQ_API_KEY=your_groq_api_key_here
```

Get a free key (no credit card) at [console.groq.com/keys](https://console.groq.com/keys).

**Optional — Gmail trigger + SMTP send:**

If you want the live Gmail inbox trigger and the Approve & Send button to actually email someone, also add:

```env
GMAIL_ADDRESS=your@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
```

To get a Gmail App Password: Google Account → Security → 2-Step Verification → App Passwords. Generate one for "Mail".

> You can skip the Gmail setup entirely. The upload button in the UI works the same way.

### 5. Run

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501).

---

## How to use it

### Upload documents and run the pipeline

1. Go to the **📦 Shipment Operations** tab
2. Upload any PDFs or images — invoice, BOL, packing list, in any combination
3. The pipeline fires automatically (no button needed)
4. Wait a few seconds — results appear on their own
5. Review the extracted fields, any discrepancies, the decision, and the draft email
6. Edit the email if you want, then click **✉️ Approve & Send**

### Single document mode

Go to **🔬 Single Document** — upload one file and click **Run Pipeline**. Good for testing.

### Dashboard

Go to **📊 Historical Dashboard** to browse past shipments and query them in plain English.

---

## The customer rule set

Pre-configured for **GlobalTech Imports GmbH** (defined in `config.py`):

| Field | Expected Value | How it matches |
|---|---|---|
| Consignee Name | GlobalTech Imports GmbH | Fuzzy |
| HS Code | 8471.30 | Exact |
| Port of Loading | JNPT Mumbai | Fuzzy + port variants |
| Port of Discharge | Hamburg | Fuzzy + port variants |
| Incoterms | CIF | Code extraction |
| Description of Goods | Electronic Components | Fuzzy (contains) |
| Gross Weight | 500 KG | Numeric fuzzy |
| Invoice Number | Must start with INV | Prefix match |

---

## Test scenarios to try

| What you upload | What should happen |
|---|---|
| A clean, correct document set | ✅ auto_approve |
| A blurry scan where fields are hard to read | 🟡 flag_for_review |
| A doc with wrong Incoterms (FOB instead of CIF) | 🔴 amendment_required |

---

## Tech stack

| Layer | Tool |
|---|---|
| UI | Streamlit |
| LLM + Vision | Groq · Llama 4 Scout 17B |
| Pipeline orchestration | LangGraph |
| File system trigger | Watchdog |
| Database | SQLite (local, no server needed) |
| PDF reading | pypdf |
| Email sending | Gmail SMTP |
| Rule retrieval | TF-IDF cosine similarity |

No Docker. No cloud infra. Just Python and a free API key.

---

## Known limitations

- **Scanned PDFs** — pypdf reads text-based PDFs only. For scanned pages, upload them as images (PNG/JPG) for best results.
- **Single customer** — the rule set is hardcoded for GlobalTech. To add a new customer, update `config.py`.
- **Sequential extraction** — documents are extracted one at a time. Fine for demos and small batches; you'd parallelize this for production.
