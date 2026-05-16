"""AI Compliance Review — send shipment/declaration context to an LLM
using the active system prompt and store the structured response for
audit + comparison with rule-based screenings.

Includes cost estimation per call and PDF export (both fresh + historical).
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
import os
import time

import pandas as pd
import streamlit as st

from db.connection import run_query
from services import llm_review
from services.pdf_export import build_review_pdf


st.set_page_config(page_title="AI compliance review", page_icon="🤖", layout="wide")
st.title("🤖 AI compliance review")
st.caption(
    "Send a shipment/declaration to an LLM (Anthropic Claude) for structured "
    "compliance analysis. Every review is logged with token usage and "
    "estimated cost for audit and comparison."
)


# ----------------------------------------------------------------------
# Health checks
# ----------------------------------------------------------------------
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
        # Show pricing for this model
        rates = llm_review.PRICING_USD_PER_MTOK.get(active["model"])
        if rates:
            st.caption(
                f"💰 Rate: ${rates['input']:.2f} input / "
                f"${rates['output']:.2f} output per MTok"
            )
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
        approx_input_tokens = len(context_text) // 4
        rates = llm_review.PRICING_USD_PER_MTOK.get(active["model"])
        cost_hint = ""
        if rates:
            est_in = approx_input_tokens / 1_000_000 * rates["input"]
            # rough output estimate: 1500 tokens
            est_out = 1500 / 1_000_000 * rates["output"]
            cost_hint = f" · est. cost ~${est_in + est_out:.4f} USD"
        st.caption(
            f"Context: ~{len(context_text):,} chars (~{approx_input_tokens:,} input tokens){cost_hint}"
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

    cost = llm_review.calculate_cost(
        result["model"], result["input_tokens"], result["output_tokens"]
    )

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
        if cost:
            st.metric(
                "Cost (USD)",
                f"${cost['total_usd']:.4f}",
                help=(
                    f"€{cost['total_eur']:.4f} indicative · "
                    f"Rates: ${cost['rate_in']:.2f}/${cost['rate_out']:.2f} per MTok · "
                    f"Latency: {elapsed:.1f}s"
                ),
            )
        else:
            st.metric("Latency", f"{elapsed:.1f}s")

    if cost:
        st.caption(
            f"💰 **Cost breakdown:** input ${cost['input_usd']:.6f} + "
            f"output ${cost['output_usd']:.6f} = **${cost['total_usd']:.6f}** "
            f"(~ €{cost['total_eur']:.6f}) · Latency {elapsed:.1f}s"
        )

    # Try to render the sections nicely
    sections = llm_review.extract_sections(result["raw_response"])

    if "_full" in sections:
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
        new_review_id = rows[0]["id"]
        st.success(f"✓ Logged to audit trail as screening #{new_review_id}.")

        # ---- PDF download for the fresh review --------------------
        pdf_bytes = build_review_pdf({
            "id": new_review_id,
            "created_at": pd.Timestamp.utcnow(),
            "model": result["model"],
            "prompt_name": active["name"],
            "prompt_version": active["version"],
            "risk_level": result.get("risk_level"),
            "recommendation": result.get("recommendation"),
            "raw_response": result["raw_response"],
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
            "context_text": context_text,
            "cost": cost,
        })
        st.download_button(
            "📄 Download as PDF",
            data=pdf_bytes,
            file_name=f"ai_review_{new_review_id}_{result.get('risk_level') or 'unknown'}.pdf",
            mime="application/pdf",
            type="primary",
        )

    except Exception as exc:
        st.warning(f"Result shown above but audit log failed: {exc}")


st.divider()

# ----------------------------------------------------------------------
# History — recent LLM reviews with cost & per-row PDF download
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
    # Enrich with cost
    enriched = []
    total_usd = 0.0
    for r in rows:
        c = llm_review.calculate_cost(
            r["llm_model"] or "",
            r["llm_input_tokens"] or 0,
            r["llm_output_tokens"] or 0,
        )
        cost_usd = c["total_usd"] if c else None
        if cost_usd is not None:
            total_usd += cost_usd
        enriched.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "risk": r["llm_risk_level"],
            "recommendation": r["llm_recommendation"],
            "model": r["llm_model"],
            "in_tok": r["llm_input_tokens"],
            "out_tok": r["llm_output_tokens"],
            "cost_usd": f"${cost_usd:.4f}" if cost_usd is not None else "?",
            "prompt": f'{r.get("prompt_name") or "?"} ({r.get("prompt_version") or "?"})',
        })

    st.dataframe(pd.DataFrame(enriched), hide_index=True, use_container_width=True)
    st.caption(
        f"💰 Total estimated cost for the last {len(rows)} reviews: "
        f"**${total_usd:.4f} USD** (~ €{total_usd * llm_review.USD_TO_EUR:.4f})"
    )

    # ---- PDF download for a historical review ---------------------
    st.markdown("##### 📄 Download a historical review as PDF")
    review_id_options = {
        f"#{r['id']} · {r['created_at'].strftime('%Y-%m-%d %H:%M')} · "
        f"risk={r['llm_risk_level'] or '?'} · {r['llm_model'] or '?'}": r["id"]
        for r in rows
    }
    chosen_label = st.selectbox(
        "Pick a review",
        list(review_id_options.keys()),
        key="history_pdf_select",
    )
    if st.button("📄 Generate PDF", key="history_pdf_btn"):
        rid = review_id_options[chosen_label]
        full = run_query(
            """
            SELECT  s.id, s.created_at,
                    s.llm_model AS model,
                    s.llm_risk_level AS risk_level,
                    s.llm_recommendation AS recommendation,
                    s.llm_raw_response AS raw_response,
                    s.llm_input_tokens AS input_tokens,
                    s.llm_output_tokens AS output_tokens,
                    s.inputs,
                    cp.name AS prompt_name,
                    cp.version AS prompt_version
            FROM    screenings s
            LEFT JOIN compliance_prompts cp ON cp.id = s.prompt_id
            WHERE   s.id = :rid
            """,
            {"rid": rid},
        )
        if not full:
            st.error("Review not found.")
        else:
            r = full[0]
            inputs = r.get("inputs") or {}
            if isinstance(inputs, str):
                try:
                    inputs = json.loads(inputs)
                except Exception:
                    inputs = {}
            ctx = inputs.get("context_text", "") if isinstance(inputs, dict) else ""

            c = llm_review.calculate_cost(
                r["model"] or "",
                r["input_tokens"] or 0,
                r["output_tokens"] or 0,
            )
            pdf_bytes = build_review_pdf({
                "id": r["id"],
                "created_at": r["created_at"],
                "model": r["model"],
                "prompt_name": r.get("prompt_name") or "?",
                "prompt_version": r.get("prompt_version") or "?",
                "risk_level": r["risk_level"],
                "recommendation": r["recommendation"],
                "raw_response": r["raw_response"] or "",
                "input_tokens": r["input_tokens"] or 0,
                "output_tokens": r["output_tokens"] or 0,
                "context_text": ctx,
                "cost": c,
            })
            st.download_button(
                f"⬇ Download ai_review_{r['id']}.pdf",
                data=pdf_bytes,
                file_name=f"ai_review_{r['id']}_{r['risk_level'] or 'unknown'}.pdf",
                mime="application/pdf",
                type="primary",
                key=f"download_pdf_{r['id']}",
            )
else:
    st.info("No AI reviews yet.")
