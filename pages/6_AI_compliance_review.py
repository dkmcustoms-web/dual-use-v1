"""AI Compliance Review — local-by-default + optional web-augmented comparison.

Flow:
  1. User enters context, clicks Run.
  2. Local review runs immediately (no internet, Claude's training only).
  3. After local result is shown, optional "🌐 Add web-augmented review" button
     re-runs the same prompt with Anthropic's web_search tool enabled.
     Constrained to OFFICIAL_DOMAINS (EUR-Lex, OFAC, EU sanctions map, etc).
  4. If both exist: side-by-side comparison, with citations on web side.
  5. One audit row per screening, with both reviews stored on it.
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

from db.connection import execute, run_query
from services import llm_review
from services.pdf_export import build_review_pdf


st.set_page_config(page_title="AI compliance review", page_icon="🤖", layout="wide")
st.title("🤖 AI compliance review")
st.caption(
    "Run a compliance assessment on a shipment/declaration. Local review uses "
    "Claude's training knowledge (deterministic, cheap). Optionally add a "
    "web-augmented comparison using Anthropic's web search, constrained to "
    "official EU/OFAC/UN sources only."
)


# ----------------------------------------------------------------------
# Session-state init — local + web reviews must survive reruns
# ----------------------------------------------------------------------
if "review_local" not in st.session_state:
    st.session_state.review_local = None
if "review_web" not in st.session_state:
    st.session_state.review_web = None
if "review_context" not in st.session_state:
    st.session_state.review_context = ""
if "review_screening_id" not in st.session_state:
    st.session_state.review_screening_id = None


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
        st.markdown(f"**Active prompt:** {active['name']} ({active['version']})")
        rates = llm_review.PRICING_USD_PER_MTOK.get(active["model"])
        if rates:
            st.caption(
                f"💰 Rate: ${rates['input']:.2f} input / "
                f"${rates['output']:.2f} output per MTok · "
                f"Web search: ${llm_review.WEB_SEARCH_PRICE_USD_PER_SEARCH:.3f}/search"
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
        value=st.session_state.review_context if st.session_state.review_context else "",
        placeholder=(
            "Example:\n\n"
            "Exporter: ABC Trading BV, Antwerp, BE\n"
            "Consignee: XYZ Holdings LLC, Dubai, UAE\n"
            "End user: Unknown\n"
            "Goods: 50x industrial CNC milling machine controllers, HS 8537.10\n"
            "Country of origin: DE\n"
            "Destination: AE (via TR)\n"
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
        st.info("No previous screenings to pull from.")
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
# Run local review
# ----------------------------------------------------------------------
col_run, col_info = st.columns([1, 2])
with col_run:
    run_clicked = st.button(
        "🚀 Run review (local)",
        type="primary",
        disabled=not context_text.strip() or not os.environ.get("ANTHROPIC_API_KEY"),
        use_container_width=True,
    )
with col_info:
    if context_text:
        approx_in = len(context_text) // 4
        rates = llm_review.PRICING_USD_PER_MTOK.get(active["model"])
        cost_hint = ""
        if rates:
            est = approx_in / 1_000_000 * rates["input"] + 1500 / 1_000_000 * rates["output"]
            cost_hint = f" · est. cost ~${est:.4f} USD"
        st.caption(
            f"Local: ~{len(context_text):,} chars (~{approx_in:,} input tokens){cost_hint}. "
            "No internet, deterministic."
        )


if run_clicked:
    # Reset session state for a fresh review
    st.session_state.review_local = None
    st.session_state.review_web = None
    st.session_state.review_context = context_text
    st.session_state.review_screening_id = None

    with st.spinner(f"Querying {active['model']} (local, no internet)..."):
        t0 = time.time()
        try:
            result = llm_review.run_llm_review(active, context_text)
            result["elapsed"] = time.time() - t0
        except Exception as exc:
            st.error(f"Local review failed: {exc}")
            st.exception(exc)
            st.stop()

    cost = llm_review.calculate_cost(
        result["model"], result["input_tokens"], result["output_tokens"]
    )
    result["cost"] = cost

    # Persist as a new screening row
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
        st.session_state.review_screening_id = rows[0]["id"]
        result["audit_logged"] = True
    except Exception as exc:
        result["audit_logged"] = False
        result["audit_error"] = str(exc)

    st.session_state.review_local = result


# ----------------------------------------------------------------------
# Render reviews from session state
# ----------------------------------------------------------------------
def _risk_emoji(risk: str | None) -> str:
    return {
        "LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"
    }.get((risk or "").upper(), "⚪")


def _render_review_metrics(rev: dict, label: str, extra_cost_usd: float = 0.0):
    """Render the 4-metric tile row for a review."""
    cost = rev.get("cost") or {}
    risk = rev.get("risk_level") or "—"
    reco = (rev.get("recommendation") or "—")[:28]
    total_usd = (cost.get("total_usd") or 0) + extra_cost_usd

    st.markdown(f"#### {_risk_emoji(rev.get('risk_level'))} {label}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Risk", risk)
    c2.metric("Recommendation", reco)
    c3.metric("Tokens", f"{rev['input_tokens']} → {rev['output_tokens']}")
    if cost:
        c4.metric(
            "Cost (USD)",
            f"${total_usd:.4f}",
            help=(f"Tokens ${cost['total_usd']:.4f}"
                  + (f" + web search ${extra_cost_usd:.4f}" if extra_cost_usd else "")),
        )
    else:
        c4.metric("Latency", f"{rev.get('elapsed', 0):.1f}s")


def _render_review_body(rev: dict):
    """Render the structured sections of a review (or raw if not structured)."""
    sections = llm_review.extract_sections(rev["raw_response"])
    if "_full" in sections:
        st.markdown(sections["_full"])
    else:
        ordered = [
            "Summary", "Risk Level", "Findings", "Sanctions Analysis",
            "Missing Information", "Recommendation", "Legal Basis",
        ]
        for title in ordered:
            body = sections.get(title, "").strip()
            if body:
                with st.container(border=True):
                    st.markdown(f"**{title}**")
                    st.markdown(body)


if st.session_state.review_local:
    local = st.session_state.review_local

    st.divider()

    # If web review exists → side-by-side, otherwise full width
    if st.session_state.review_web:
        web = st.session_state.review_web
        web_search_cost = llm_review.calculate_web_search_cost(web.get("searches_used", 0))
        left, right = st.columns(2)

        with left:
            _render_review_metrics(local, "Local review (training only)")
            st.caption(f"⏱ {local.get('elapsed', 0):.1f}s · 🚫 no internet")
            _render_review_body(local)

        with right:
            _render_review_metrics(web, "Web-augmented review", extra_cost_usd=web_search_cost)
            st.caption(
                f"⏱ {web.get('elapsed', 0):.1f}s · "
                f"🌐 {web.get('searches_used', 0)} web searches "
                f"(${web_search_cost:.4f})"
            )
            _render_review_body(web)

            # Citations from web search
            citations = web.get("citations") or []
            if citations:
                with st.expander(f"📎 {len(citations)} citations from official sources"):
                    seen_urls = set()
                    for cite in citations:
                        url = cite.get("url")
                        if not url or url in seen_urls:
                            continue
                        seen_urls.add(url)
                        title = cite.get("title") or url
                        st.markdown(f"- [{title}]({url})")
                        cited = cite.get("cited_text")
                        if cited:
                            st.caption(f"_« {cited[:200]}{'…' if len(cited) > 200 else ''} »_")
            else:
                st.caption("_(Claude did not cite specific sources)_")

            # Search queries Claude ran
            queries = web.get("search_queries") or []
            if queries:
                with st.expander(f"🔍 {len(queries)} search queries Claude ran"):
                    for q in queries:
                        st.markdown(f"- `{q}`")

    else:
        # Single column (local only)
        _render_review_metrics(local, "Local review (training only)")
        st.caption(f"⏱ {local.get('elapsed', 0):.1f}s · 🚫 no internet — Claude's training knowledge")
        _render_review_body(local)

        # Web augmentation offer
        st.divider()
        wcol1, wcol2 = st.columns([1, 2])
        with wcol1:
            web_clicked = st.button(
                "🌐 Add web-augmented review",
                type="secondary",
                use_container_width=True,
                key="web_review_btn",
            )
        with wcol2:
            st.caption(
                f"Re-runs the same prompt with Anthropic web search enabled, "
                f"constrained to {len(llm_review.OFFICIAL_DOMAINS)} official "
                f"domains (EUR-Lex, OFAC, EU sanctions map, etc). "
                f"~$0.05-0.15 extra · ~10-20s extra."
            )

        if web_clicked:
            with st.spinner("Running web-augmented review (Claude is searching official sources)..."):
                t0 = time.time()
                try:
                    web_result = llm_review.run_llm_review_web_augmented(active, context_text)
                    web_result["elapsed"] = time.time() - t0
                except Exception as exc:
                    st.error(f"Web-augmented review failed: {exc}")
                    st.exception(exc)
                    web_result = None

            if web_result:
                web_cost = llm_review.calculate_cost(
                    web_result["model"], web_result["input_tokens"], web_result["output_tokens"]
                )
                web_result["cost"] = web_cost

                # Update the existing screening row with web columns
                if st.session_state.review_screening_id:
                    try:
                        execute(
                            """
                            UPDATE screenings
                            SET llm_web_raw_response    = :raw,
                                llm_web_model           = :model,
                                llm_web_input_tokens    = :it,
                                llm_web_output_tokens   = :ot,
                                llm_web_searches_used   = :su,
                                llm_web_citations       = CAST(:cites AS JSONB),
                                llm_web_risk_level      = :rl,
                                llm_web_recommendation  = :rc,
                                llm_web_search_queries  = CAST(:sq AS JSONB)
                            WHERE id = :id
                            """,
                            {
                                "raw": web_result["raw_response"],
                                "model": web_result["model"],
                                "it": web_result["input_tokens"],
                                "ot": web_result["output_tokens"],
                                "su": web_result.get("searches_used", 0),
                                "cites": json.dumps(web_result.get("citations", [])),
                                "rl": web_result.get("risk_level"),
                                "rc": web_result.get("recommendation"),
                                "sq": json.dumps(web_result.get("search_queries", [])),
                                "id": st.session_state.review_screening_id,
                            },
                        )
                    except Exception as exc:
                        st.warning(f"Web review shown but audit update failed: {exc}")

                st.session_state.review_web = web_result
                st.rerun()


# ----------------------------------------------------------------------
# PDF download for the local review (shown when at least local exists)
# ----------------------------------------------------------------------
if st.session_state.review_local and st.session_state.review_screening_id:
    st.divider()
    local = st.session_state.review_local
    try:
        pdf_bytes = build_review_pdf({
            "id": st.session_state.review_screening_id,
            "created_at": pd.Timestamp.utcnow(),
            "model": local["model"],
            "prompt_name": active["name"],
            "prompt_version": active["version"],
            "risk_level": local.get("risk_level"),
            "recommendation": local.get("recommendation"),
            "raw_response": local["raw_response"],
            "input_tokens": local["input_tokens"],
            "output_tokens": local["output_tokens"],
            "context_text": st.session_state.review_context,
            "cost": local.get("cost"),
        })
        st.download_button(
            "📄 Download local review as PDF",
            data=pdf_bytes,
            file_name=f"ai_review_{st.session_state.review_screening_id}_"
                      f"{local.get('risk_level') or 'unknown'}.pdf",
            mime="application/pdf",
        )
    except Exception as exc:
        st.warning(f"PDF generation failed: {exc}")


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------
st.divider()
st.subheader("Recent AI reviews")
rows = run_query(
    """
    SELECT  s.id, s.created_at, s.llm_risk_level, s.llm_recommendation,
            s.llm_model, s.llm_input_tokens, s.llm_output_tokens,
            s.llm_web_risk_level, s.llm_web_recommendation,
            s.llm_web_searches_used,
            cp.name AS prompt_name, cp.version AS prompt_version
    FROM    screenings s
    LEFT JOIN compliance_prompts cp ON cp.id = s.prompt_id
    WHERE   s.screening_type = 'llm_review'
    ORDER BY s.created_at DESC
    LIMIT 20
    """
)
if rows:
    enriched = []
    total_usd = 0.0
    for r in rows:
        c = llm_review.calculate_cost(
            r["llm_model"] or "",
            r["llm_input_tokens"] or 0,
            r["llm_output_tokens"] or 0,
        )
        cost_usd = c["total_usd"] if c else 0
        web_cost = llm_review.calculate_web_search_cost(r.get("llm_web_searches_used") or 0)
        total_usd += cost_usd + web_cost
        enriched.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "local_risk": r["llm_risk_level"],
            "web_risk": r["llm_web_risk_level"] or "—",
            "web_searches": r.get("llm_web_searches_used") or 0,
            "model": r["llm_model"],
            "cost_usd": f"${cost_usd + web_cost:.4f}",
            "prompt": f'{r.get("prompt_name") or "?"} ({r.get("prompt_version") or "?"})',
        })
    st.dataframe(pd.DataFrame(enriched), hide_index=True, use_container_width=True)
    st.caption(
        f"💰 Total estimated cost for the last {len(rows)} reviews: "
        f"**${total_usd:.4f} USD** (~ €{total_usd * llm_review.USD_TO_EUR:.4f})"
    )
else:
    st.info("No AI reviews yet.")
