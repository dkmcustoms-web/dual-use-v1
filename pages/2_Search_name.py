"""Search a name (person or company) against OpenSanctions."""
from __future__ import annotations

# --- Ensure project root is on sys.path (Streamlit/Railway compat) -----
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
# ----------------------------------------------------------------------


import streamlit as st

from services import opensanctions
from services.screening import ScreeningInput, run_screening

st.set_page_config(page_title="Search name", page_icon="🔍", layout="wide")
st.title("🔍 Search name")
st.caption(
    "Free-text lookup against the OpenSanctions consolidated dataset. "
    "Each search is logged to the audit trail."
)

col_q, col_schema = st.columns([3, 1])
with col_q:
    query = st.text_input(
        "Name to search",
        placeholder="e.g. 'Vladimir Putin' or 'Rosneft Oil Company'",
    )
with col_schema:
    schema = st.selectbox(
        "Type",
        options=["Any", "Person", "Company", "LegalEntity", "Vessel", "Organization"],
        index=0,
    )

col_country, col_log = st.columns([1, 3])
with col_country:
    country = st.text_input(
        "Country hint (ISO-2)",
        max_chars=2,
        help="Optional. Improves precision for common names.",
    ).upper()

with col_log:
    log_to_audit = st.checkbox(
        "Log this lookup to the audit trail",
        value=True,
        help="Recommended for any production-relevant check.",
    )

if st.button("Search", type="primary"):
    if not query.strip():
        st.warning("Enter a name first.")
        st.stop()

    try:
        with st.spinner("Querying OpenSanctions..."):
            data = opensanctions.free_text_search(
                query=query,
                limit=20,
                schema=schema if schema != "Any" else None,
            )

        results = data.get("results", [])
        st.success(f"{len(results)} match(es).")

        if log_to_audit:
            inp = ScreeningInput(
                screening_type="name_lookup",
                parties=[query],
                destination_country=country or None,
            )
            run_screening(inp)

        if not results:
            st.info("No matches in the OpenSanctions consolidated dataset.")
        else:
            for r in results:
                score = r.get("score", 0) or 0
                severity = opensanctions.hit_severity(float(score))
                icon = {"ALERT": "🛑", "REVIEW": "⚠️", "INFO": "ℹ️"}.get(severity, "•")
                with st.container(border=True):
                    cols = st.columns([5, 1])
                    with cols[0]:
                        st.markdown(f"### {icon} {r.get('caption', '(unnamed)')}")
                        st.caption(
                            f"Schema: `{r.get('schema', '?')}` · "
                            f"Datasets: {', '.join(r.get('datasets', []))}"
                        )
                    with cols[1]:
                        st.metric("Score", f"{float(score):.2f}")
                        st.caption(severity)

                    props = r.get("properties", {})
                    # Show key properties only
                    key_props = ["name", "alias", "country", "birthDate",
                                 "address", "topics", "sanctions"]
                    shown_any = False
                    for key in key_props:
                        if key in props:
                            shown_any = True
                            val = props[key]
                            if isinstance(val, list):
                                val = ", ".join(str(v) for v in val)
                            st.markdown(f"**{key}:** {val}")
                    if not shown_any:
                        st.caption("(no key properties available)")

                    with st.expander("Full record"):
                        st.json(r)
    except Exception as exc:
        st.error(f"Search failed: {exc}")
        st.caption(
            "Common causes: invalid API key, hitting the free-tier rate limit, "
            "or temporary OpenSanctions outage."
        )
