"""Search a product description against the loaded Annex I and manual sources."""
from __future__ import annotations

# --- Ensure project root is on sys.path (Streamlit/Railway compat) -----
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# ----------------------------------------------------------------------


import streamlit as st

from db.connection import run_query
from services import annex_i
from services.screening import ScreeningInput, run_screening

st.set_page_config(page_title="Search product", page_icon="📦", layout="wide")
st.title("📦 Search product")
st.caption(
    "Search a product description against Annex I (Reg. 2021/821) and any "
    "manually loaded sources. Uses trigram similarity for fuzzy matching."
)

active = annex_i.get_active_annex_source()
if not active:
    st.warning(
        "No Annex I source is loaded yet. Go to **Data sources** to upload the "
        "DG TRADE Excel, or run `python scripts/load_annex_i.py path/to/excel.xlsx`."
    )
else:
    st.caption(
        f"Using **{active['source_name']}** "
        f"(version `{active['version']}`, {active['row_count']} entries)"
    )

query = st.text_input(
    "Product description or keyword",
    placeholder="e.g. 'quantum amplifier', 'lithography', 'gas centrifuge'",
)

col_a, col_b = st.columns([1, 3])
with col_a:
    exact_code = st.text_input(
        "Or look up an ECN code",
        placeholder="e.g. 3A001",
        help="Exact match — bypasses similarity search.",
    )

if st.button("Search", type="primary") and (query or exact_code):
    # ---- Exact code lookup ------------------------------------------
    if exact_code.strip():
        item = annex_i.get_by_code(exact_code.strip())
        if item:
            st.success(f"Found {item['code']}")
            ancestors = annex_i.get_ancestors(item["id"])
            with st.container(border=True):
                breadcrumb = " → ".join(
                    f"`{a['code']}` {a['label'][:50]}"
                    for a in ancestors[:-1]
                )
                if breadcrumb:
                    st.caption(breadcrumb)
                st.markdown(f"### `{item['code']}`")
                st.write(item["label"])
                children = annex_i.get_children(item["id"])
                if children:
                    st.markdown("**Sub-entries:**")
                    for c in children:
                        st.markdown(f"- `{c['code']}` — {c['label'][:200]}")
        else:
            st.warning(f"No entry with code `{exact_code}`.")

    # ---- Similarity search -------------------------------------------
    if query.strip():
        try:
            matches = annex_i.search_labels(query.strip(), limit=30)
        except Exception as exc:
            st.error(f"Search failed: {exc}")
            st.caption(
                "If the error mentions `gin_trgm_ops` or `%`, the pg_trgm "
                "extension is missing. Re-run `python -m db.init_db`."
            )
            matches = []

        if not matches:
            st.info("No similar Annex I entries.")
        else:
            st.subheader(f"Annex I hits ({len(matches)})")
            import pandas as pd
            df = pd.DataFrame([{
                "ECN": m["code"],
                "Similarity": round(float(m["score"]), 3),
                "Category": m["category"],
                "Subgroup": m["subgroup"],
                "Label": (m["label"] or "")[:300],
            } for m in matches])
            st.dataframe(df, hide_index=True, use_container_width=True)

        # Also search manual entries (free-form sources)
        manual = run_query(
            """
            SELECT  me.entry_key, me.entry_label, me.payload, ds.source_name, ds.version
            FROM    manual_entries me
            JOIN    data_sources ds ON ds.id = me.source_id
            WHERE   ds.is_active = TRUE
              AND   (
                    me.entry_label ILIKE :like
                 OR  me.entry_key ILIKE :like
                  )
            LIMIT 50
            """,
            {"like": f"%{query.strip()}%"},
        )
        if manual:
            st.subheader(f"Manual source hits ({len(manual)})")
            import pandas as pd
            df = pd.DataFrame([{
                "Key": m["entry_key"],
                "Label": (m["entry_label"] or "")[:300],
                "Source": f"{m['source_name']} ({m['version']})",
            } for m in manual])
            st.dataframe(df, hide_index=True, use_container_width=True)

        # Log it
        inp = ScreeningInput(
            screening_type="product_lookup",
            product_descriptions=[query.strip()],
        )
        try:
            run_screening(inp)
        except Exception:
            pass  # Audit log is best-effort here
