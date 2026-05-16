"""AI Compliance Review — send shipment/declaration context to an LLM
using the active system prompt and store the structured response for
audit + comparison with rule-based screenings.
"""
from __future__ import annotations

# --- Ensure project root is on sys.path (Streamlit/Railway compat) -----
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# ----------------------------------------------------------------------

import json
import time

import pandas as pd
import streamlit as st

from db.connection import execute, run_query
from services import llm_review

st.set_page_config(page_title="AI compliance review", page_icon="🤖", layout="wide")
st.title("🤖 AI compliance review")
st.caption(
    "Send a shipment/declaration to an LLM (Anthropic Claude) for structured "
    "compliance analysis. Complements the rule-based screening on the other "
    "pages — every review is logged for audit and comparison."
)


# ----------------------------------------------------------------------
# Health check
# ----------------------------------------------------------------------
import os
if not os.environ.get("ANTHROPIC_API_KEY"):
    st.error(
        "🔑 `ANTHROPIC_API_KEY` is not set. Add it to .env locally or to "
        "Railway → Variables."
    )

active = llm_review.get_active_prompt()
if not active:
    st.warning(
        "No active prompt configured. Go to **Data sources** → "
        "**Compliance prompts** to seed the default one."
    )
    if st.button("⚡ Seed default EU compliance prompt now"):
        try:
            new_id = llm_review.seed_default_prompt()
            st.success(f"✓ Default prompt seeded as #{new_id}. Refresh.")
            time.sleep(1)
            st.rerun()
        except Exception as exc:
            st.error(f"Seed failed: {exc}")
    st.stop()

# Compact prompt info banner
with st.container(border=True):
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    with c1:
        st.markdown(f"**Active prompt:** {active['name']} (v{active['version']})")
    with c2:
        st.metric("Model", active["model"])
    with c3:
        st.metric("Temp", f"{float(active['temperature']):.1f}")
    with c4:
        st.metric("Max tokens", active["max_tokens"])


st.divider()

# ----------------------------------------------------------------------
# Context input
# ----------------------------------------------------------------------
st.subheader("Context to analyse")

source_mode = st.radio(
    "Source of context",
    options=["Manual input", "From a previous screening"],
    horizontal=True,
)

context_text = ""

if source_mode == "Manual input":
    context_text = st.text_area(
        "Paste shipment details, declaration data, party list, route, goods description, etc.",
        height=350,
        placeholder=(
            "Example:\n\n"
            "Exporter: ABC Trading BV, Antwerp, BE\n"
            "Consignee: XYZ Holdings LLC, Dubai, UAE\n"
            "End user: Unknown\n"
            "Goods: 50x industrial CNC milling machine controllers, HS 8537.10\n"
            "Country of origin: DE\n"
            "Destination: AE (via TR)\n"
            "Vessel: MV NORTHWIND (IMO 9876543)\n"
            "Payment: 60% advance via UAE bank, balance on delivery\n"
        ),
    )
elif source_mode == "From a previous screening":
    screenings = run_query(
        """
        SELECT id, created_at, screening_type, summary_text, pdf_filename, inputs
        FROM   screenings
        WHERE  screening_type IN ('invoice', 'name_lookup', 'product_lookup')
        ORDER BY created_at DESC
        LIMIT 50
        """
    )
    if not screenings:
        st.info("No previous screenings to pull from. Use Upload invoice or Search pages first.")
    else:
        options = {
            f"#{s['id']} · {s['screening_type']} · "
            f"{s['created_at'].strftime('%Y-%m-%d %H:%M')} · "
            f"{(s['summary_text'] or '')[:60]}": s["id"]
            for s in screenings
        }
        chosen = st.selectbox("Pick a screening", list(options.keys()))
        sid = options[chosen]
        chosen_row = next(s for s in screenings if s["id"] == sid)

        # Build the context block from the screening data
        inputs = chosen_row["inputs"] or {}
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs)
            except Exception:
                inputs = {}

        lines = [
            f"# Context pulled from screening #{sid} ({chosen_row['screening_type']})",
            f"# Recorded at: {chosen_row['created_at']}",
            "",
        ]
        if inputs.get("parties"):
            lines.append("Parties:")
            for p in inputs["parties"]:
                lines.append(f"  - {p}")
        if inputs.get("destination_country"):
            lines.append(f"Destination country: {inputs['destination_country']}")
        if inputs.get("product_descriptions"):
            lines.append("Goods / product descriptions:")
            for p in inputs["product_descriptions"]:
                lines.append(f"  - {p}")
        if chosen_row.get("pdf_filename"):
            lines.append(f"\nSource document: {chosen_row['pdf_filename']}")
        lines.append("\n[Add any additional context below before running the review.]")

        context_text = st.text_area(
            "Context (editable before sending)",
            value="\n".join(lines),
            height=350,
        )

st.divider()

# ----------------------------------------------------------------------
# Run review
# ----------------------------------------------------------------------
col_run, col_info = st.columns([1, 2])
with col_run:
    run_clicked = st.button(
        "🚀 Run AI review",
        type="primary",
        disabled=not context_text.strip() or not os.environ.get("ANTHROPIC_API_KEY"),
        use_container_width=True,
    )
with col_info:
    if context_text:
        st.caption(
            f"Context length: ~{len(context_text):,} chars "
            f"(~{len(context_text) // 4:,} tokens). "
            f"Each review costs an API call to Anthropic."
        )

if run_clicked:
    with st.spinner(f"Querying {active['model']}..."):
        t0 = time.time()
        try:
            result = llm_review.run_llm_review(active, context_text)
        except Exception as exc:
            st.error(f"LLM call failed: {exc}")
            st.exception(exc)
            st.stop()
        elapsed = time.time() - t0

    # ---- Display results -------------------------------------------
    risk_color = {
        "LOW":      "🟢",
        "MEDIUM":   "🟡",
        "HIGH":     "🟠",
        "CRITICAL": "🔴",
    }.get(result.get("risk_level") or "", "⚪")

    st.subheader(f"{risk_color} Result")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Risk level", result.get("risk_level") or "—")
    with c2:
        st.metric("Recommendation", (result.get("recommendation") or "—")[:30])
    with c3:
        st.metric(
            "Tokens",
            f"{result['input_tokens']} → {result['output_tokens']}",
            help="Input → output tokens",
        )
    with c4:
        st.metric("Latency", f"{elapsed:.1f}s")

    # Try to render the sections nicely
    sections = llm_review.extract_sections(result["raw_response"])

    if "_full" in sections:
        # Fallback when section extraction failed
        st.markdown(sections["_full"])
    else:
        ordered = [
            "Summary", "Risk Level", "Findings", "Sanctions Analysis",
            "Missing Information", "Recommendation", "Legal Basis",
        ]
        for title in ordered:
            if title in sections and sections[title].strip():
                with st.container(border=True):
                    st.markdown(f"### {title}")
                    st.markdown(sections[title])

    with st.expander("📄 Raw model response"):
        st.text(result["raw_response"])

    # ---- Persist to screenings (audit trail) ------------------------
    inputs_json = json.dumps({
        "context_text": context_text[:5000],
        "context_length": len(context_text),
        "prompt_name": active["name"],
        "prompt_version": active["version"],
    })
    try:
        rows = run_query(
            """
            INSERT INTO screenings
                (screening_type, inputs, summary_status, summary_text,
                 prompt_id, llm_model, llm_raw_response,
                 llm_input_tokens, llm_output_tokens,
                 llm_risk_level, llm_recommendation)
            VALUES
                ('llm_review', CAST(:inp AS JSONB), :ss, :stx,
                 :pid, :model, :raw,
                 :it, :ot, :rl, :rc)
            RETURNING id
            """,
            {
                "inp": inputs_json,
                "ss": result.get("risk_level") or "UNKNOWN",
                "stx": f"AI review — risk={result.get('risk_level') or '?'}, "
                       f"reco={result.get('recommendation') or '?'}",
                "pid": active["id"],
                "model": result["model"],
                "raw": result["raw_response"],
                "it": result["input_tokens"],
                "ot": result["output_tokens"],
                "rl": result.get("risk_level"),
                "rc": result.get("recommendation"),
            },
        )
        st.success(f"✓ Logged to audit trail as screening #{rows[0]['id']}.")
    except Exception as exc:
        st.warning(f"Result shown above but audit log failed: {exc}")


st.divider()

# ----------------------------------------------------------------------
# History — recent LLM reviews
# ----------------------------------------------------------------------
st.subheader("Recent AI reviews")
rows = run_query(
    """
    SELECT  s.id, s.created_at, s.llm_risk_level, s.llm_recommendation,
            s.llm_model, s.llm_input_tokens, s.llm_output_tokens,
            cp.name AS prompt_name, cp.version AS prompt_version
    FROM    screenings s
    LEFT JOIN compliance_prompts cp ON cp.id = s.prompt_id
    WHERE   s.screening_type = 'llm_review'
    ORDER BY s.created_at DESC
    LIMIT 20
    """
)
if rows:
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, use_container_width=True)
else:
    st.info("No AI reviews yet.")
