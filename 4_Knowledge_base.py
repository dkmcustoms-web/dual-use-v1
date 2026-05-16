"""Upload an invoice PDF, extract text, then run a full screening."""
from __future__ import annotations

import streamlit as st

from services.pdf_extractor import extract_pdf
from services.screening import ScreeningInput, run_screening

st.set_page_config(page_title="Upload invoice", page_icon="📄", layout="wide")
st.title("📄 Upload invoice")
st.caption(
    "Drop a PDF invoice below. The extracted text is shown so you can "
    "verify/correct the candidate parties, country, and product descriptions "
    "before running the screening."
)

# Session state for staging extracted candidates
if "extracted" not in st.session_state:
    st.session_state.extracted = None

uploaded = st.file_uploader("Invoice PDF", type=["pdf"])

if uploaded is not None:
    file_bytes = uploaded.read()
    with st.spinner("Extracting text from PDF..."):
        extracted = extract_pdf(file_bytes)
    st.session_state.extracted = {
        "filename": uploaded.name,
        "result": extracted,
    }

if st.session_state.extracted:
    data = st.session_state.extracted
    extracted = data["result"]

    st.success(
        f"Extracted {len(extracted.full_text):,} chars from "
        f"{extracted.page_count} page(s)."
    )

    with st.expander("📑 Full extracted text", expanded=False):
        st.text_area(
            "",
            value=extracted.full_text,
            height=350,
            label_visibility="collapsed",
        )

    st.divider()
    st.subheader("Review the inputs before screening")

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("**Parties to screen**")
        default_parties = "\n".join(extracted.candidate_parties)
        parties_text = st.text_area(
            "One per line. Edit/add/remove as needed.",
            value=default_parties,
            height=180,
            help="Candidates were extracted heuristically near labels like "
                 "'Consignor', 'Consignee', 'Afzender', 'Geadresseerde'.",
        )
        parties = [p.strip() for p in parties_text.splitlines() if p.strip()]

        destination = st.text_input(
            "Destination country (ISO-2)",
            value=extracted.candidate_countries[0] if extracted.candidate_countries else "",
            help="Used as a hint to OpenSanctions matching.",
            max_chars=2,
        ).upper()

    with col_right:
        st.markdown("**Products to screen against Annex I**")
        default_products = "\n".join(extracted.candidate_products[:10])
        products_text = st.text_area(
            "One description per line.",
            value=default_products,
            height=240,
        )
        products = [p.strip() for p in products_text.splitlines() if p.strip()]

    st.divider()

    if st.button("🚀 Run screening", type="primary", use_container_width=True):
        if not parties and not products:
            st.warning("Add at least one party or product before screening.")
            st.stop()

        with st.spinner("Running screening..."):
            inp = ScreeningInput(
                screening_type="invoice",
                parties=parties,
                destination_country=destination or None,
                product_descriptions=products,
                pdf_filename=data["filename"],
                pdf_text_excerpt=extracted.full_text[:2000],
            )
            result = run_screening(inp)

        # ----------------- Results -----------------
        status_color = {
            "OK": "✅",
            "REVIEW": "⚠️",
            "ALERT": "🛑",
        }.get(result.summary_status, "ℹ️")
        st.subheader(f"{status_color} {result.summary_status} — {result.summary_text}")
        st.caption(f"Screening ID #{result.screening_id} — logged to audit trail.")

        if not result.hits:
            st.info("No hits across any source. The screening is recorded in the audit log.")
        else:
            # Group by hit_type
            os_hits = [h for h in result.hits if h.hit_type == "opensanctions"]
            annex_hits = [h for h in result.hits if h.hit_type == "annex_i"]
            manual_hits = [h for h in result.hits if h.hit_type == "manual"]

            if os_hits:
                st.markdown("### 🧑 Party / sanctions hits")
                import pandas as pd
                df = pd.DataFrame([{
                    "Severity": h.severity,
                    "Searched": h.matched_term,
                    "Match": h.matched_entity,
                    "Score": f"{h.score:.2f}" if h.score is not None else "—",
                    "Lists": h.source_reference,
                } for h in os_hits])
                st.dataframe(df, hide_index=True, use_container_width=True)

            if annex_hits:
                st.markdown("### 📦 Annex I product hits")
                import pandas as pd
                df = pd.DataFrame([{
                    "Severity": h.severity,
                    "Searched": h.matched_term[:60],
                    "ECN": h.payload.get("code", ""),
                    "Annex I label": h.payload.get("label", "")[:150],
                    "Similarity": f"{h.score:.2f}" if h.score is not None else "—",
                } for h in annex_hits])
                st.dataframe(df, hide_index=True, use_container_width=True)

            if manual_hits:
                st.markdown("### 📝 Manual source hits")
                for h in manual_hits:
                    st.markdown(f"- **{h.severity}** — {h.matched_entity}")
