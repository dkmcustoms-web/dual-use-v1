"""Y901 Sandbox — Streamlit home page.

Run locally:
    streamlit run app.py
"""
from __future__ import annotations

# --- Ensure project root is on sys.path (Streamlit/Railway compat) -----
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# ----------------------------------------------------------------------

import streamlit as st

from db.connection import run_query
from services import annex_i
from services.screening import recent_screenings

st.set_page_config(
    page_title="Y901 Sandbox",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🛡️ Y901 Dual-Use Sandbox")
st.caption(
    "Research/playground app for dual-use export compliance screening. "
    "Use the sidebar to upload an invoice, search names, search products, "
    "browse the knowledge base, or manage data sources."
)

# ----------------------------------------------------------------------
# System health check — fast queries to confirm DB + sources are in place
# ----------------------------------------------------------------------
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("Database")
    try:
        rows = run_query("SELECT COUNT(*) AS n FROM data_sources")
        n_sources = rows[0]["n"]
        st.metric("Data sources", n_sources)
        st.success("Connected to Postgres ✓")
    except Exception as exc:
        st.error(f"DB error: {exc}")
        st.info(
            "Check that DATABASE_URL is set and that the schema is initialized "
            "(`python -m db.init_db`)."
        )

with col2:
    st.subheader("Annex I")
    try:
        active = annex_i.get_active_annex_source()
        if active:
            st.metric(
                "Active version",
                active["version"],
                help=active["source_name"],
            )
            st.metric("Rows loaded", active["row_count"] or 0)
            st.success("Knowledge base ready ✓")
        else:
            st.warning("No Annex I source loaded yet.")
            st.info("Run: `python scripts/load_annex_i.py <excel.xlsx>`")
    except Exception as exc:
        st.error(f"Source check failed: {exc}")

with col3:
    st.subheader("OpenSanctions")
    import os
    if os.environ.get("OPENSANCTIONS_API_KEY"):
        st.success("API key configured ✓")
        st.caption(
            "Free-tier limits apply. Test the connection on the "
            "**Search name** page."
        )
    else:
        st.error("OPENSANCTIONS_API_KEY not set.")
        st.info("Add it to `.env` locally or to Railway Variables in production.")


st.divider()

# ----------------------------------------------------------------------
# Recent screenings
# ----------------------------------------------------------------------
st.subheader("Recent screenings")
try:
    rows = recent_screenings(limit=15)
    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "created_at": st.column_config.DatetimeColumn("When"),
                "screening_type": "Type",
                "summary_status": "Status",
                "summary_text": "Summary",
                "pdf_filename": "PDF",
            },
        )
    else:
        st.info("No screenings yet. Try the Upload invoice page.")
except Exception as exc:
    st.warning(f"Could not load screenings: {exc}")


st.divider()

with st.expander("ℹ️ About this sandbox", expanded=False):
    st.markdown(
        """
        This is a **research playground** for the Y901 dual-use compliance flow.
        It is intentionally separate from the production DKM app so logic can
        be tuned and extended freely.

        **What's here:**
        - OpenSanctions screening for parties (consignor/consignee/etc.)
        - Annex I knowledge base (EU Regulation 2021/821)
        - PDF invoice upload + extraction
        - Audit log of every screening with source-version snapshots
        - Extensible data sources — upload your own CSV/JSON via the
          **Data sources** page

        **What it does *not* do (yet):**
        - The CN ↔ ECN correlation table is not loaded — add it when you have it
        - No automatic decision: every hit needs operator review

        Treat any output as **advisory**, not legally binding.
        """
    )
