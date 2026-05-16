"""Browse the Annex I tree by category → subgroup → entry."""
from __future__ import annotations

import streamlit as st

from services import annex_i

st.set_page_config(page_title="Knowledge base", page_icon="📚", layout="wide")
st.title("📚 Annex I knowledge base")

active = annex_i.get_active_annex_source()
if not active:
    st.warning(
        "No Annex I source is loaded. Upload the DG TRADE Excel via the "
        "**Data sources** page or run `python scripts/load_annex_i.py`."
    )
    st.stop()

st.caption(
    f"Source: **{active['source_name']}** · version `{active['version']}` · "
    f"{active['row_count']} entries"
)

categories = annex_i.list_categories()
if not categories:
    st.error("Source is registered but no items were loaded. Re-run the loader.")
    st.stop()

# ----------------------------------------------------------------------
# Two-pane browser: category list on the left, content on the right
# ----------------------------------------------------------------------
col_nav, col_view = st.columns([1, 3])

with col_nav:
    st.markdown("**Categories**")
    selected_cat_code = st.radio(
        "Pick a category",
        options=[c["code"] for c in categories],
        format_func=lambda c: f"{c} — " + next(
            (cat["label"][:50] for cat in categories if cat["code"] == c), ""
        ),
        label_visibility="collapsed",
    )

with col_view:
    selected_cat = next((c for c in categories if c["code"] == selected_cat_code), None)
    if selected_cat:
        st.markdown(f"### Category {selected_cat['code']}")
        st.caption(selected_cat["label"])

        # Direct children = subgroups (A/B/C/D/E)
        subgroups = annex_i.get_children(selected_cat["id"])
        if not subgroups:
            st.info("This category has no children loaded.")
        else:
            tabs = st.tabs([sg["code"] for sg in subgroups])
            for tab, sg in zip(tabs, subgroups):
                with tab:
                    st.markdown(f"**{sg['code']} — {sg['label']}**")
                    entries = annex_i.get_children(sg["id"])
                    if not entries:
                        st.caption("No entries in this subgroup.")
                    for entry in entries:
                        with st.expander(
                            f"`{entry['code']}` — {entry['label'][:100]}",
                            expanded=False,
                        ):
                            st.write(entry["label"])
                            children = annex_i.get_children(entry["id"])
                            if children:
                                st.markdown("---")
                                st.markdown("**Sub-entries:**")
                                for c in children:
                                    st.markdown(
                                        f"- `{c['code']}` — {c['label'][:300]}"
                                    )
                                    # One more level (depth 4 in the tree)
                                    grandchildren = annex_i.get_children(c["id"])
                                    for gc in grandchildren:
                                        st.markdown(
                                            f"   - `{gc['code']}` — {gc['label'][:300]}"
                                        )
