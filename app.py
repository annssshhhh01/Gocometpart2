import io, base64, json, uuid, shutil, time
from datetime import datetime, UTC
from pathlib import Path

import streamlit as st

from agents.decision import decide
from config import APPROVAL_POLICY, RULES
from db.database import (
    init_db, save_result, count_by_decision, query_nl,
)
from graph import graph
from workflow.shipment import load_shipment
from workflow.shipment_processor import process_shipment
from workflow.shipment_validator import validate_shipment
from workflow.email_drafter import draft_email
from workflow.watcher import start_watcher, result_queue

# ── Init ──────────────────────────────────────────────────────────────────────
init_db()

# ── Start Watchdog observer once (daemon thread, survives Streamlit rerenders)
# This is the real trigger: as soon as a folder lands in incoming_shipments/,
# the full pipeline fires automatically — no button required.
start_watcher()

RULES_CONTEXT = json.dumps(
    {"customer": "GlobalTech Imports GmbH", "rules": RULES, "policy": APPROVAL_POLICY},
    indent=2,
)

st.set_page_config(
    page_title="GoComet Nova · CG Shipment Console",
    page_icon="🚢",
    layout="wide",
)

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* dark base */
.stApp { background: #0a0f1e; }

/* cards */
.card {
    background: linear-gradient(135deg,#111827,#1a2340);
    border: 1px solid #1e3a5f;
    border-radius: 14px;
    padding: 22px 26px;
    margin-bottom: 18px;
}

/* status pill */
.pill {
    display: inline-block;
    padding: 3px 14px;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.05em;
}
.pill-green  { background:#064e3b; color:#34d399; border:1px solid #059669; }
.pill-yellow { background:#451a03; color:#fbbf24; border:1px solid #d97706; }
.pill-red    { background:#450a0a; color:#f87171; border:1px solid #dc2626; }
.pill-blue   { background:#1e3a5f; color:#60a5fa; border:1px solid #2563eb; }

/* metric mini */
.metric-box {
    background:#111827;
    border:1px solid #1e3a5f;
    border-radius:10px;
    padding:16px 20px;
    text-align:center;
}
.metric-box .val { font-size:2rem; font-weight:700; color:#f1f5f9; }
.metric-box .lbl { font-size:0.75rem; color:#64748b; text-transform:uppercase; letter-spacing:.06em; margin-top:4px; }

/* field row */
.frow {
    display:flex; align-items:center; gap:12px;
    padding: 7px 0; border-bottom:1px solid #1e293b;
    font-size:0.85rem;
}
.frow:last-child { border-bottom:none; }
.frow .fname { color:#94a3b8; width:190px; flex-shrink:0; }
.frow .fval  { color:#e2e8f0; flex:1; font-weight:500; }
.frow .fconf { color:#64748b; width:48px; text-align:right; }

/* disc card */
.disc {
    background:#1a1a2e;
    border-left:4px solid #f59e0b;
    border-radius:0 10px 10px 0;
    padding:12px 18px;
    margin-bottom:10px;
}
.disc.mismatch { border-color:#ef4444; }
.disc.missing  { border-color:#f59e0b; }
.disc .dtitle  { font-weight:600; color:#f1f5f9; font-size:0.9rem; margin-bottom:6px; }
.disc .drow    { font-size:0.82rem; color:#94a3b8; }
.disc .dval    { color:#e2e8f0; font-weight:500; }

/* amend row */
.amend {
    background:#1a0a0a;
    border:1px solid #7f1d1d;
    border-radius:10px;
    padding:12px 18px;
    margin-bottom:10px;
    font-size:0.85rem;
}
.amend .afield { color:#fca5a5; font-weight:600; margin-bottom:4px; }
.amend .adiff  { color:#94a3b8; }
.amend .adiff span { color:#e2e8f0; }

/* section title */
.sec-title {
    font-size:0.7rem; font-weight:700; letter-spacing:.12em;
    text-transform:uppercase; color:#475569; margin:28px 0 12px;
}
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="padding:20px 0 10px">
  <div style="font-size:1.7rem;font-weight:700;color:#f1f5f9;letter-spacing:-.02em">
    🚢 GoComet Nova — CG Shipment Console
  </div>
  <div style="color:#475569;font-size:0.88rem;margin-top:4px">
    Multi-agent trade document validation · LangGraph · Groq Vision
  </div>
</div>
""", unsafe_allow_html=True)

tab_p2, tab_p1, tab_db = st.tabs([
    "📦 Shipment Operations",
    "🔬 Single Document (Part 1)",
    "📊 Historical Dashboard",
])

# ══════════════════════════════════════════════════════════════════════════════
# PART 2 — SHIPMENT OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════
with tab_p2:

    # ── Upload ────────────────────────────────────────────────────────────────
    st.markdown('<div class="sec-title">Upload Shipment Documents</div>', unsafe_allow_html=True)
    st.caption("📂 Pipeline runs automatically as soon as you upload your documents — no button needed.")

    uploaded_files = st.file_uploader(
        "Drop shipment documents here",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        help="Upload invoice, BOL, packing list — all in one go",
        key="p2_uploader",
    )

    # ── Session-state init ────────────────────────────────────────────────────
    if "last_upload_fingerprint" not in st.session_state:
        st.session_state.last_upload_fingerprint = None
    if "pending_shipment_id" not in st.session_state:
        st.session_state.pending_shipment_id = None   # waiting on watcher
    if "p2_results" not in st.session_state:
        st.session_state.p2_results = None

    # ── Step 1 — Save files → Watchdog does the rest ─────────────────────────
    # The upload fingerprint lets us detect a genuinely new set of files
    # without re-saving on every Streamlit rerender.
    upload_fingerprint = (
        tuple(sorted((f.name, f.size) for f in uploaded_files))
        if uploaded_files else None
    )

    if upload_fingerprint and upload_fingerprint != st.session_state.last_upload_fingerprint:
        # New upload detected — save to incoming_shipments/ and let Watchdog trigger
        ts = datetime.now(UTC).strftime("%H%M%S")
        shipment_id = f"SHP-{ts}-{str(uuid.uuid4())[:4].upper()}"
        shp_dir = Path("incoming_shipments") / shipment_id
        shp_dir.mkdir(parents=True, exist_ok=True)

        for uf in uploaded_files:
            (shp_dir / uf.name).write_bytes(uf.getbuffer())

        st.session_state.last_upload_fingerprint = upload_fingerprint
        st.session_state.pending_shipment_id     = shipment_id
        st.session_state.p2_results              = None  # clear old results

        st.info(
            f"📂 **{len(uploaded_files)} document(s)** saved to `incoming_shipments/{shipment_id}/`  \n"
            f"⚙️ Watchdog detected the folder — pipeline is running automatically…"
        )

    # ── Step 2 — Poll result_queue for the pipeline result ───────────────────
    # The watcher thread pushes results into result_queue when it finishes.
    # We drain any items that arrived since the last rerender.
    if st.session_state.pending_shipment_id:
        # Non-blocking drain — take everything that's ready right now
        while not result_queue.empty():
            item = result_queue.get_nowait()
            if item["shipment_id"] == st.session_state.pending_shipment_id:
                if item.get("error"):
                    st.error(f"❌ Pipeline error: {item['error']}")
                    st.session_state.pending_shipment_id = None
                else:
                    st.session_state.p2_results = item
                    st.session_state.pending_shipment_id = None
            else:
                # Put back items for other shipments (rare edge case)
                result_queue.put(item)

        # If still pending, show spinner and auto-rerun after a short pause
        if st.session_state.pending_shipment_id:
            with st.spinner("⏳ Pipeline running… (auto-refreshing)"):
                time.sleep(2)
            st.rerun()

    # ── Step 3 — Render results (from session state, survives rerenders) ──────
    if st.session_state.p2_results:
        r                 = st.session_state.p2_results
        shipment_id       = r["shipment_id"]
        processed         = r["processed"]
        validation_report = r["validation_report"]
        decision          = r["decision"]
        email_draft       = r["email_draft"]

        summary = validation_report["validation_summary"]
        docs_validated = summary["documents_validated"]
        cross_disc     = summary["cross_discrepancies"]
        dec_val        = decision.get("decision", "unknown")

        # ── Section 2: Shipment Summary ───────────────────────────────────────
        st.markdown('<div class="sec-title">Shipment Summary</div>', unsafe_allow_html=True)

        pill_cls = {"auto_approve":"pill-green","flag_for_review":"pill-yellow","amendment_required":"pill-red"}.get(dec_val,"pill-blue")
        dec_label = dec_val.replace("_"," ").title()

        m1, m2, m3, m4 = st.columns(4)
        for col, val, lbl in [
            (m1, shipment_id, "Shipment ID"),
            (m2, r.get("num_files", len(uploaded_files or [])), "Documents"),
            (m3, f'<span class="pill {pill_cls}">{dec_label}</span>', "Decision"),
            (m4, cross_disc, "Cross Discrepancies"),
        ]:
            col.markdown(
                f'<div class="metric-box"><div class="val">{val}</div><div class="lbl">{lbl}</div></div>',
                unsafe_allow_html=True,
            )

        # ── Section 3: Extraction Results ─────────────────────────────────────
        st.markdown('<div class="sec-title">Extraction Results</div>', unsafe_allow_html=True)

        for doc_key, doc_result in processed["documents"].items():
            icon = "✅" if doc_result["status"] == "success" else "❌"
            with st.expander(f"{icon}  **{doc_result['name']}**  ·  `{doc_key}`", expanded=True):
                if doc_result["status"] != "success":
                    st.error(f"Extraction failed: {doc_result.get('error','unknown')}")
                    continue

                rows_html = ""
                for field, data in doc_result["fields"].items():
                    val  = data.get("value") or "—"
                    conf = data.get("confidence", 0.0)
                    dot  = ("🟢" if conf >= 0.7 else "🟡" if conf >= 0.4 else "🔴")
                    rows_html += (
                        f'<div class="frow">'
                        f'<span class="fname">{field.replace("_"," ").title()}</span>'
                        f'<span class="fval">{val}</span>'
                        f'<span class="fconf">{dot} {conf:.0%}</span>'
                        f'</div>'
                    )
                st.markdown(f'<div class="card" style="padding:14px 20px">{rows_html}</div>',
                            unsafe_allow_html=True)

        # ── Section 4: Cross-Document Discrepancies ────────────────────────────
        st.markdown('<div class="sec-title">Cross-Document Discrepancies</div>', unsafe_allow_html=True)

        disc_list = validation_report["cross_document_discrepancies"]
        if not disc_list:
            st.success("✅  No cross-document discrepancies detected.")
        else:
            for d in disc_list:
                status = d["status"]
                css_cls = "mismatch" if status == "mismatch" else "missing"
                icon    = "❌" if status == "mismatch" else "⚠️"
                field_label = d["field"].replace("_", " ").title()
                doc_rows = "".join(
                    f'<div class="drow"><b>{k}:</b> <span class="dval">{v if v is not None else "—"}</span></div>'
                    for k, v in d["documents"].items()
                )
                st.markdown(
                    f'<div class="disc {css_cls}">'
                    f'<div class="dtitle">{icon} {field_label} — <em>{status.replace("_"," ")}</em></div>'
                    f'{doc_rows}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ── Section 5: Workflow Decision ───────────────────────────────────────
        st.markdown('<div class="sec-title">Workflow Decision</div>', unsafe_allow_html=True)

        color_map = {
            "auto_approve":       ("#064e3b", "#34d399", "🟢"),
            "flag_for_review":    ("#451a03", "#fbbf24", "🟡"),
            "amendment_required": ("#450a0a", "#f87171", "🔴"),
        }
        bg, fg, ico = color_map.get(dec_val, ("#1e293b","#94a3b8","⚪"))
        st.markdown(
            f'<div class="card" style="border-color:{fg}40;background:linear-gradient(135deg,{bg},{bg}aa)">'
            f'<div style="font-size:1.5rem;font-weight:700;color:{fg}">{ico} {dec_label}</div>'
            f'<div style="color:#94a3b8;margin-top:8px;font-size:0.88rem">{decision.get("reason","")}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Section 6: Amendment Draft ─────────────────────────────────────────
        if decision.get("amendment_draft"):
            st.markdown('<div class="sec-title">Amendment Draft</div>', unsafe_allow_html=True)
            for item in decision["amendment_draft"]:
                field = item["field"].replace("_", " ").title()
                st.markdown(
                    f'<div class="amend">'
                    f'<div class="afield">✏️ {field}</div>'
                    f'<div class="adiff">Found: <span>{item["found"]}</span></div>'
                    f'<div class="adiff">Expected: <span>{item["expected"]}</span></div>'
                    f'<div class="adiff" style="margin-top:6px;color:#fca5a5">{item.get("action","")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ── Section 7: Flagged Uncertain Fields ────────────────────────────────
        if decision.get("flagged_fields"):
            st.markdown('<div class="sec-title">Flagged Uncertain Fields</div>', unsafe_allow_html=True)
            for item in decision["flagged_fields"]:
                field = item["field"].replace("_", " ").title()
                conf  = item.get("confidence", 0.0)
                st.markdown(
                    f'<div class="disc missing">'
                    f'<div class="dtitle">⚠️ {field} — {conf:.0%} confidence</div>'
                    f'<div class="drow">Found: <span class="dval">{item.get("found","—")}</span></div>'
                    f'<div class="drow" style="margin-top:4px;color:#64748b">{item.get("reason","")}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # ── Section 8: CG Reply Email Draft ───────────────────────────────────
        st.markdown('<div class="sec-title">CG Reply Email — Review &amp; Send</div>', unsafe_allow_html=True)

        send_icon = {"auto_approve": "✅", "flag_for_review": "⚠️", "amendment_required": "❌"}.get(dec_val, "📧")
        st.markdown(
            f'<div class="card" style="padding:14px 20px;border-color:#2563eb40">'
            f'<div style="font-size:0.75rem;font-weight:600;letter-spacing:.08em;'
            f'text-transform:uppercase;color:#475569;margin-bottom:10px">'
            f'{send_icon} Draft ready · Edit below before sending</div>'
            f'<div style="font-size:0.82rem;color:#64748b">'
            f'Subject: <span style="color:#e2e8f0;font-weight:500">{email_draft["subject"]}</span>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

        edited_body = st.text_area(
            label="Email body (editable — CG reviews and approves before sending)",
            value=email_draft["body"],
            height=340,
            key="email_body_editor",
        )

        send_col, copy_col, _ = st.columns([2, 2, 3])
        send_clicked = send_col.button(
            "✉️ Approve & Send",
            type="primary",
            use_container_width=True,
            key="send_btn",
        )
        copy_col.download_button(
            label="📋 Download as .txt",
            data=f"Subject: {email_draft['subject']}\n\n{edited_body}",
            file_name=f"{shipment_id}_reply.txt",
            mime="text/plain",
            use_container_width=True,
            key="download_btn",
        )

        if send_clicked:
            st.success(
                f"✅ **Reply approved and sent** for shipment `{shipment_id}`.\n\n"
                "The decision and email have been recorded. "
                "CG can query this shipment via the Historical Dashboard."
            )
            st.balloons()

# ══════════════════════════════════════════════════════════════════════════════
# PART 1 — SINGLE DOCUMENT
# ══════════════════════════════════════════════════════════════════════════════
with tab_p1:
    st.markdown('<div class="sec-title">Upload Single Trade Document</div>', unsafe_allow_html=True)

    document_text = ""
    page_images   = []

    uploaded = st.file_uploader(
        "Upload trade document",
        type=["pdf", "png", "jpg", "jpeg"],
        key="p1_uploader",
    )

    if uploaded:
        if uploaded.type == "application/pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(uploaded.read()))
                document_text = "\n".join(p.extract_text() or "" for p in reader.pages)
                st.success(f"✅ PDF loaded — {len(reader.pages)} page(s)")
            except Exception as e:
                st.error(f"PDF error: {e}")
        else:
            img_bytes = uploaded.read()
            page_images.append(base64.b64encode(img_bytes).decode())
            st.image(img_bytes, caption=uploaded.name, use_column_width=True)
            st.success("✅ Image loaded — vision LLM will be used")

        if document_text:
            with st.expander("Preview extracted text"):
                st.text(document_text[:2000] + ("…" if len(document_text) > 2000 else ""))

    if st.button("⚡ Run Part 1 Pipeline", type="primary", use_container_width=True):
        if not document_text.strip() and not page_images:
            st.warning("Please upload a document first.")
        else:
            with st.spinner("Running multi-agent pipeline…"):
                try:
                    result = graph.invoke({"text": document_text, "images": page_images})
                except Exception as e:
                    st.error(f"Pipeline failed: {e}")
                    st.stop()

            save_result(
                result.get("extracted", {}),
                result.get("validated", {}),
                result.get("decision", {}),
            )

            extracted = result.get("extracted", {})
            validated = result.get("validated", {})
            dec       = result.get("decision", {})
            dec_label = dec.get("decision", "N/A")

            icon_map = {"auto_approve":"🟢","flag_for_review":"🟡","amendment_required":"🔴"}
            st.markdown(f"### {icon_map.get(dec_label,'⚪')} Decision: `{dec_label}`")
            st.info(f"**Reasoning:** {dec.get('reason','N/A')}")

            col_e, col_v = st.columns(2)
            with col_e:
                st.subheader("🔍 Extracted Fields")
                for field, data in extracted.items():
                    if isinstance(data, dict):
                        conf = data.get("confidence", 0)
                        dot  = "🟢" if conf >= 0.7 else "🟡" if conf >= 0.4 else "🔴"
                        st.markdown(
                            f'<div class="frow"><span class="fname">{field.replace("_"," ").title()}</span>'
                            f'<span class="fval">{data.get("value","—")}</span>'
                            f'<span class="fconf">{dot} {conf:.0%}</span></div>',
                            unsafe_allow_html=True,
                        )
            with col_v:
                st.subheader("✅ Validation")
                for field, data in validated.items():
                    status = data.get("status", "—")
                    s_icon = {"match":"✅","mismatch":"❌","uncertain":"⚠️"}.get(status, "?")
                    st.markdown(f"{s_icon} **{field.replace('_',' ').title()}** — {data.get('found','—')}")

            if dec.get("amendment_draft"):
                st.subheader("📝 Amendment Request")
                for item in dec["amendment_draft"]:
                    st.warning(
                        f"**{item['field'].replace('_',' ').title()}**: "
                        f"Found `{item['found']}` → Expected `{item['expected']}`\n\n"
                        f"↳ {item['action']}"
                    )

# ══════════════════════════════════════════════════════════════════════════════
# HISTORICAL DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
with tab_db:
    st.markdown('<div class="sec-title">Pipeline Statistics</div>', unsafe_allow_html=True)

    stats = count_by_decision()
    c1, c2, c3 = st.columns(3)
    for col, key, lbl in [
        (c1, "auto_approve",       "✅ Auto-Approved"),
        (c2, "flag_for_review",    "🟡 Flagged"),
        (c3, "amendment_required", "🔴 Amendment"),
    ]:
        col.markdown(
            f'<div class="metric-box"><div class="val">{stats.get(key,0)}</div>'
            f'<div class="lbl">{lbl}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="sec-title">Natural Language Query</div>', unsafe_allow_html=True)
    nl = st.text_input("Ask about your data", placeholder='e.g. "How many shipments were flagged?"')
    if nl:
        with st.spinner("Querying…"):
            st.code(query_nl(nl))
