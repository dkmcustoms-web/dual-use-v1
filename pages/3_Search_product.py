"""Hybrid search: trigram + ILIKE + semantic, language-agnostic.

Strategy:
  1. TRIGRAM on annex_i_items.label (EN Excel)        — fast, lexical
  2. ILIKE on manual_entries (NL + EN TXT, full content) — keyword match
  3. SEMANTIC on both via pgvector cosine similarity  — concept match,
     bridges languages and semantic gaps ("dive material" → "rebreather")

Results are merged on ECN code (entry_key) and ranked by best score.
Each hit is labeled with WHICH route(s) found it, so the user sees
"text match" vs "concept match" badges.
"""
from __future__ import annotations

# --- Ensure project root is on sys.path (Streamlit/Railway compat) -----
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# ----------------------------------------------------------------------

import json as _json
import os
import re

import streamlit as st

from db.connection import run_query
from services import annex_i

CATEGORY_NAMES = {
    "0": "Nuclear materials, facilities and equipment",
    "1": "Special materials and related equipment",
    "2": "Materials processing",
    "3": "Electronics",
    "4": "Computers",
    "5": "Telecommunications and Information Security",
    "6": "Sensors and Lasers",
    "7": "Navigation and Avionics",
    "8": "Marine",
    "9": "Aerospace and Propulsion",
}


def _category_label(category):
    if not category:
        return ""
    return f"Cat {category} — {CATEGORY_NAMES.get(category, '')}"


def _parse_payload(p):
    """Normalise payload to a dict regardless of how it came back from DB."""
    if p is None:
        return {}
    if isinstance(p, dict):
        return p
    if isinstance(p, str):
        try:
            return _json.loads(p)
        except Exception:
            return {}
    return {}


# ----------------------------------------------------------------------
# Search routes
# ----------------------------------------------------------------------
def run_trigram_search(query: str, limit: int = 30) -> list[dict]:
    """Trigram-similarity search on the EN Excel Annex I tree."""
    try:
        return annex_i.search_labels(query, limit=limit)
    except Exception as exc:
        st.error(f"Trigram search failed: {exc}")
        return []


def run_ilike_search(query: str, limit: int = 50) -> list[dict]:
    """ILIKE search on manual_entries (NL/EN TXT) — label + key + full_content."""
    return run_query(
        """
        SELECT  me.entry_key       AS entry_key,
                me.entry_label     AS entry_label,
                me.payload         AS payload,
                ds.source_name     AS source_name,
                ds.version         AS version,
                CASE
                    WHEN me.entry_label ILIKE :like THEN 0.9
                    WHEN me.entry_key   ILIKE :like THEN 0.8
                    ELSE 0.5
                END AS score
        FROM    manual_entries me
        JOIN    data_sources ds ON ds.id = me.source_id
        WHERE   ds.is_active = TRUE
          AND   (
                  me.entry_label              ILIKE :like
               OR me.entry_key                ILIKE :like
               OR me.payload->>'full_content' ILIKE :like
                )
        ORDER BY score DESC, me.entry_key
        LIMIT :limit
        """,
        {"like": f"%{query}%", "limit": limit},
    )


def run_semantic_search(query: str, min_sim: float, limit: int = 30):
    """Embed query + cosine search against annex_i_items and manual_entries."""
    if not os.environ.get("OPENAI_API_KEY"):
        return {"annex": [], "manual": [], "cost": None, "error": "OPENAI_API_KEY missing"}
    try:
        from services import embeddings as emb
        vec = emb.embed_query(query)
        annex_hits = annex_i.semantic_search_annex(vec, limit=limit, min_similarity=min_sim)
        manual_hits = annex_i.semantic_search_manual(vec, limit=limit, min_similarity=min_sim)
        # Approx cost for the single-query embedding
        return {
            "annex": annex_hits,
            "manual": manual_hits,
            "cost": {"tokens": ~50, "usd": 50 / 1_000_000 * emb.EMBEDDING_PRICE_USD_PER_MTOK},
            "error": None,
        }
    except Exception as exc:
        return {"annex": [], "manual": [], "cost": None, "error": str(exc)}


# ----------------------------------------------------------------------
# Merging logic
# ----------------------------------------------------------------------
def merge_results(trigram_hits, ilike_hits, sem_annex_hits, sem_manual_hits):
    """Merge by ECN code. Each result tracks which routes found it + best score.

    Returns list of unified dicts sorted by best score:
        {
          'code': '8A002.q',
          'label': '...',
          'parent_label': ...,
          'parent_code': ...,
          'category': '8',
          'source': 'annex_i' | 'manual',
          'language': 'EN' | 'NL' | None,
          'routes': {'trigram': 0.45, 'semantic': 0.78, ...},
          'best_score': 0.78,
          'manual_payload': dict | None,
          'manual_source_name': str | None,
        }
    """
    merged: dict[tuple[str, str], dict] = {}

    def _key(source, code):
        return (source, code)

    def _upsert(source, code, base_dict, route_name, score):
        k = _key(source, code)
        if k in merged:
            merged[k]["routes"][route_name] = max(score, merged[k]["routes"].get(route_name, 0))
            merged[k]["best_score"] = max(merged[k]["best_score"], score)
        else:
            base_dict["routes"] = {route_name: score}
            base_dict["best_score"] = score
            base_dict["source"] = source
            merged[k] = base_dict

    # Trigram (annex_i)
    for h in trigram_hits or []:
        d = {
            "code": h.get("code"),
            "label": h.get("label"),
            "category": h.get("category"),
            "subgroup": h.get("subgroup"),
            "parent_label": h.get("parent_label"),
            "parent_code": h.get("parent_code"),
            "language": "EN",  # Excel labels are EN
            "manual_payload": None,
            "manual_source_name": None,
            "id": h.get("id"),
        }
        _upsert("annex_i", h.get("code"), d, "trigram", float(h.get("score") or 0))

    # Semantic on annex_i
    for h in sem_annex_hits or []:
        d = {
            "code": h.get("code"),
            "label": h.get("label"),
            "category": h.get("category"),
            "subgroup": h.get("subgroup"),
            "parent_label": h.get("parent_label"),
            "parent_code": h.get("parent_code"),
            "language": "EN",
            "manual_payload": None,
            "manual_source_name": None,
            "id": h.get("id"),
        }
        _upsert("annex_i", h.get("code"), d, "semantic", float(h.get("score") or 0))

    # ILIKE (manual_entries)
    for h in ilike_hits or []:
        payload = _parse_payload(h.get("payload"))
        d = {
            "code": h.get("entry_key"),
            "label": h.get("entry_label"),
            "category": payload.get("category"),
            "subgroup": payload.get("subgroup"),
            "parent_label": None,
            "parent_code": None,
            "language": payload.get("language"),
            "manual_payload": payload,
            "manual_source_name": f"{h.get('source_name')} v{h.get('version')}",
            "id": None,
        }
        _upsert("manual", h.get("entry_key"), d, "ilike", float(h.get("score") or 0))

    # Semantic on manual
    for h in sem_manual_hits or []:
        payload = _parse_payload(h.get("payload"))
        d = {
            "code": h.get("entry_key"),
            "label": h.get("entry_label"),
            "category": payload.get("category"),
            "subgroup": payload.get("subgroup"),
            "parent_label": None,
            "parent_code": None,
            "language": payload.get("language"),
            "manual_payload": payload,
            "manual_source_name": f"{h.get('source_name')} v{h.get('version')}",
            "id": None,
        }
        _upsert("manual", h.get("entry_key"), d, "semantic", float(h.get("score") or 0))

    return sorted(merged.values(), key=lambda x: x["best_score"], reverse=True)


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------
def _route_badges(routes: dict) -> str:
    badges = []
    if "trigram" in routes:
        badges.append(f"🔡 text {routes['trigram']:.2f}")
    if "ilike" in routes:
        badges.append(f"🔍 keyword {routes['ilike']:.2f}")
    if "semantic" in routes:
        badges.append(f"🧠 semantic {routes['semantic']:.2f}")
    return " · ".join(badges)


def _highlight_query(text: str, query: str) -> str:
    if not query or not text:
        return text or ""
    return re.sub(re.escape(query), lambda m: f"**`{m.group()}`**", text, flags=re.IGNORECASE)


def render_hit(hit: dict, query: str):
    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            lang = hit.get("language") or ("EN" if hit["source"] == "annex_i" else "?")
            lang_tag = f"🌐 {lang}" if lang else ""
            st.markdown(f"### `{hit['code']}` &nbsp;&nbsp; <small>{lang_tag}</small>", unsafe_allow_html=True)
            cat = _category_label(hit.get("category"))
            crumb_parts = [p for p in [cat,
                f"`{hit['parent_code']}` {(hit.get('parent_label') or '')[:60]}"
                    if hit.get("parent_code") and hit["parent_code"] != hit["code"] else None,
                hit.get("manual_source_name"),
            ] if p]
            if crumb_parts:
                st.caption(" › ".join(crumb_parts))
        with c2:
            st.markdown(f"**score {hit['best_score']:.2f}**")
            st.caption(_route_badges(hit["routes"]))

        # Label
        st.markdown(hit.get("label") or "")

        # Snippet from full_content if the query appears there
        payload = hit.get("manual_payload") or {}
        full_content = payload.get("full_content", "") if isinstance(payload, dict) else ""
        if query and full_content and query.lower() in full_content.lower() and query.lower() not in (hit.get("label") or "").lower():
            idx = full_content.lower().find(query.lower())
            start = max(0, idx - 80)
            end = min(len(full_content), idx + len(query) + 200)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(full_content) else ""
            snippet = f"{prefix}{full_content[start:end]}{suffix}"
            st.caption("**Found in narrative:**")
            st.markdown(f"_{_highlight_query(snippet, query)}_")

        # Annex I sub-entries
        if hit.get("id"):
            try:
                children = annex_i.get_children(hit["id"])
                if children:
                    with st.expander(f"📂 {len(children)} sub-entries"):
                        for c in children:
                            st.markdown(f"- **`{c.get('code', '?')}`** — {c.get('label', '')}")
            except Exception:
                pass


# ----------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------
st.set_page_config(page_title="Search product", page_icon="📦", layout="wide")
st.title("📦 Search product")
st.caption(
    "**Gebruik dit voor:** snel checken of een product/technologie mogelijk dual-use "
    "gecontroleerd is onder EU Annex I (Reg. 2021/821). Geeft je een leesbaar verdict "
    "+ verificatievragen + relevante ECN-codes.  \n"
    "**Niet hiervoor:** volledige shipment-review met parties/route/betaling → gebruik dan "
    "**AI compliance review**."
)

active = annex_i.get_active_annex_source()
if not active:
    st.warning(
        "No Annex I source is loaded yet. Go to **Data sources** to upload the "
        "DG TRADE Excel and run the embedding build."
    )

# Stats banner
annex_stats = annex_i.count_embedded_annex_items()
manual_stats = annex_i.count_embedded_manual_entries()
with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric(
            "Annex I (Excel)",
            f"{annex_stats['embedded']}/{annex_stats['total']} embedded",
        )
    with c2:
        st.metric(
            "Manual entries (NL/EN TXT)",
            f"{manual_stats['embedded']}/{manual_stats['total']} embedded",
        )
    with c3:
        sem_ready = annex_stats["embedded"] + manual_stats["embedded"] > 0
        st.metric("Semantic search", "✓ ready" if sem_ready else "× not built")

if annex_stats["remaining"] + manual_stats["remaining"] > 0:
    st.info(
        f"⚠ {annex_stats['remaining'] + manual_stats['remaining']} entries are not embedded yet. "
        "Build embeddings on **Data sources** → **Semantic search**."
    )

# ----------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------
col_q, col_code = st.columns([2, 1])
with col_q:
    query = st.text_input(
        "Search query (NL or EN — both work)",
        placeholder="e.g. 'dive material', 'rebreathers', 'duikuitrusting', 'centrifuge'",
    )
with col_code:
    exact_code = st.text_input(
        "Or look up an exact ECN code",
        placeholder="e.g. 3A001",
    )

col_run, col_routes, col_thr = st.columns([1, 2, 1])
with col_run:
    search_clicked = st.button("🔍 Search", type="primary", use_container_width=True)
with col_routes:
    routes_enabled = st.multiselect(
        "Search routes",
        options=["trigram", "ilike", "semantic"],
        default=["trigram", "ilike", "semantic"],
        help="trigram = fuzzy text on Annex I labels; ilike = keyword in NL/EN narrative; semantic = vector similarity",
    )
with col_thr:
    min_sem = st.slider(
        "Min semantic similarity",
        min_value=0.20, max_value=0.80, value=0.30, step=0.05,
        help="Higher = stricter semantic matches. Try 0.30–0.45 for broad search.",
    )


# ----------------------------------------------------------------------
# Session-state init — search results + verdict must survive reruns
# (Streamlit buttons are one-shot: their True state is gone after a click)
# ----------------------------------------------------------------------
if "last_search" not in st.session_state:
    st.session_state.last_search = None
if "last_verdict" not in st.session_state:
    st.session_state.last_verdict = None


def _run_and_store_verdict(query: str, merged: list[dict], routes_enabled_list: list[str]):
    """Run Claude on top hits, persist to DB, store result in session state."""
    from services import llm_review as _llmr
    import time as _time

    top_candidates = []
    for hit in merged[:8]:
        top_candidates.append({
            "code": hit["code"],
            "label": hit.get("label", ""),
            "parent_code": hit.get("parent_code"),
            "parent_label": hit.get("parent_label"),
            "language": hit.get("language"),
            "manual_payload": hit.get("manual_payload"),
        })

    t0 = _time.time()
    try:
        verdict = _llmr.run_product_verdict(query, top_candidates)
        verdict["elapsed"] = _time.time() - t0
        verdict["candidate_codes"] = [c["code"] for c in top_candidates]

        # Persist to audit trail
        inputs_json = _json.dumps({
            "query": query,
            "candidate_codes": verdict["candidate_codes"],
            "candidates_used": verdict.get("candidates_used", len(top_candidates)),
            "routes_enabled": routes_enabled_list,
        })
        try:
            run_query(
                """
                INSERT INTO screenings
                    (screening_type, inputs, summary_status, summary_text,
                     llm_model, llm_raw_response,
                     llm_input_tokens, llm_output_tokens)
                VALUES
                    ('product_verdict', CAST(:inp AS JSONB), :ss, :stx,
                     :model, :raw, :it, :ot)
                """,
                {
                    "inp": inputs_json,
                    "ss": "verdict",
                    "stx": f"Product verdict for: {query[:80]}",
                    "model": verdict["model"],
                    "raw": verdict["verdict_text"],
                    "it": verdict["input_tokens"],
                    "ot": verdict["output_tokens"],
                },
            )
            verdict["audit_logged"] = True
        except Exception as audit_exc:
            verdict["audit_logged"] = False
            verdict["audit_error"] = str(audit_exc)

        st.session_state.last_verdict = verdict
    except Exception as exc:
        st.session_state.last_verdict = {"error": str(exc)}


# ----------------------------------------------------------------------
# Step 1: when Search button is clicked, RUN search AND verdict together
# ----------------------------------------------------------------------
if search_clicked and query.strip():
    q = query.strip()
    try:
        with st.spinner("🔍 Searching…"):
            trigram_hits = run_trigram_search(q) if "trigram" in routes_enabled else []
            ilike_hits = run_ilike_search(q) if "ilike" in routes_enabled else []
            if "semantic" in routes_enabled:
                sem = run_semantic_search(q, min_sim=min_sem)
                sem_annex = sem["annex"]
                sem_manual = sem["manual"]
                sem_err = sem["error"]
                sem_cost = sem["cost"]
            else:
                sem_annex, sem_manual, sem_err, sem_cost = [], [], None, None

            merged = merge_results(trigram_hits, ilike_hits, sem_annex, sem_manual)

        st.session_state.last_search = {
            "query": q,
            "trigram_hits": trigram_hits,
            "ilike_hits": ilike_hits,
            "sem_annex": sem_annex,
            "sem_manual": sem_manual,
            "sem_err": sem_err,
            "sem_cost": sem_cost,
            "merged": merged,
            "routes_enabled": list(routes_enabled),
        }
        st.session_state.last_verdict = None  # clear previous

        # Auto-run Claude if we have results AND an API key
        if merged and os.environ.get("ANTHROPIC_API_KEY"):
            with st.spinner(f"🤖 Claude analyzing top {min(8, len(merged))} hits..."):
                _run_and_store_verdict(q, merged, list(routes_enabled))
    except Exception as exc:
        st.error(f"⚠ Search failed: {exc}")
        st.exception(exc)
        # Keep page usable even after error
        st.session_state.last_search = None


# ----------------------------------------------------------------------
# Step 2: exact-code lookup (renders inline, no session state needed)
# ----------------------------------------------------------------------
if search_clicked and exact_code.strip():
    item = annex_i.get_by_code(exact_code.strip())
    if item:
        st.success(f"Exact match for `{item['code']}`")
        with st.container(border=True):
            st.markdown(f"### `{item['code']}`")
            st.write(item["label"])
            children = annex_i.get_children(item["id"])
            if children:
                st.markdown(f"**Sub-entries ({len(children)}):**")
                for c in children:
                    st.markdown(f"- `{c['code']}` — {c['label']}")
    else:
        st.warning(f"No entry with code `{exact_code}`.")


# ----------------------------------------------------------------------
# Step 3: render from session state — TABS for verdict + raw hits
# ----------------------------------------------------------------------
if st.session_state.last_search:
    s = st.session_state.last_search
    q = s["query"]
    merged = s["merged"]

    if s["sem_err"]:
        st.warning(f"Semantic search skipped: {s['sem_err']}")

    # Summary metrics ABOVE tabs (always visible)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trigram hits", len(s["trigram_hits"]))
    c2.metric("ILIKE hits", len(s["ilike_hits"]))
    c3.metric("Semantic hits", len(s["sem_annex"]) + len(s["sem_manual"]))
    c4.metric("Unique results", len(merged))

    if s["sem_cost"] and s["sem_cost"].get("usd"):
        st.caption(f"💰 Semantic query cost: ~${s['sem_cost']['usd']:.6f} USD")

    if not merged:
        st.info("No results across any route. Try a broader term or lower the semantic threshold.")
    else:
        # ===== TABS =====
        tab_verdict, tab_results = st.tabs([
            "🤖 Compliance verdict",
            f"📊 Ranked results ({len(merged)})",
        ])

        # ------ TAB 1: Compliance verdict ------
        with tab_verdict:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                st.warning("🔑 ANTHROPIC_API_KEY not set — verdict skipped. Add it in Railway → Variables.")
                st.info("👉 Switch to the **Ranked results** tab to see the raw hits.")
            elif st.session_state.last_verdict is None:
                # Should not happen, but guard
                st.info("Run a search to get a verdict.")
            elif "error" in st.session_state.last_verdict:
                st.error(f"Verdict failed: {st.session_state.last_verdict['error']}")
                if st.button("🔄 Retry verdict", key="retry_verdict_btn"):
                    with st.spinner("Retrying Claude..."):
                        _run_and_store_verdict(q, merged, s["routes_enabled"])
                    st.rerun()
            else:
                v = st.session_state.last_verdict
                from services import llm_review as _llmr
                cost = _llmr.calculate_cost(
                    v["model"], v["input_tokens"], v["output_tokens"]
                )

                with st.container(border=True):
                    st.markdown(v["verdict_text"])

                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Model", v["model"].replace("claude-", ""))
                mc2.metric("Tokens", f"{v['input_tokens']} → {v['output_tokens']}")
                if cost:
                    mc3.metric("Cost (USD)", f"${cost['total_usd']:.4f}")
                else:
                    mc3.metric("Cost", "?")
                mc4.metric("Latency", f"{v.get('elapsed', 0):.1f}s")

                # Subtle status caption
                if v.get("audit_logged"):
                    st.caption("✓ Verdict logged to audit trail.")
                elif "audit_error" in v:
                    st.caption(f"_(audit log failed: {v['audit_error']})_")

                # Regenerate option (low key, secondary)
                with st.expander("Need a second opinion?"):
                    if st.button("🔄 Regenerate verdict", key="regen_verdict_btn"):
                        with st.spinner("Re-running Claude on the same hits..."):
                            _run_and_store_verdict(q, merged, s["routes_enabled"])
                        st.rerun()

        # ------ TAB 2: Ranked results (raw hits) ------
        with tab_results:
            st.caption(
                f"Top {min(len(merged), 30)} unique ECN codes from hybrid search. "
                "Useful for double-checking the verdict, or as primary view if you don't want Claude."
            )
            for hit in merged[:30]:
                render_hit(hit, q)


# ----------------------------------------------------------------------
# Debug panel — always visible, helps diagnose "page goes blank" issues
# ----------------------------------------------------------------------
with st.expander("🔧 Debug — session state inspector"):
    last_s = st.session_state.get("last_search")
    last_v = st.session_state.get("last_verdict")
    st.write({
        "search_clicked_this_run": search_clicked,
        "query": query,
        "routes_enabled": list(routes_enabled),
        "last_search_set": last_s is not None,
        "last_search_query": last_s.get("query") if last_s else None,
        "last_search_merged_count": len(last_s.get("merged", [])) if last_s else 0,
        "last_search_trigram_count": len(last_s.get("trigram_hits", [])) if last_s else 0,
        "last_search_ilike_count": len(last_s.get("ilike_hits", [])) if last_s else 0,
        "last_search_sem_annex_count": len(last_s.get("sem_annex", [])) if last_s else 0,
        "last_search_sem_manual_count": len(last_s.get("sem_manual", [])) if last_s else 0,
        "last_search_sem_err": last_s.get("sem_err") if last_s else None,
        "last_verdict_set": last_v is not None,
        "last_verdict_error": (last_v or {}).get("error") if isinstance(last_v, dict) else None,
        "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
    })
