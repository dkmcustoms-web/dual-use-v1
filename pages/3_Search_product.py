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


def _category_label(category: str | None) -> str:
    if not category:
        return ""
    return f"Cat {category} — {CATEGORY_NAMES.get(category, '')}"


def _render_hit_card(hit: dict, query: str | None = None) -> None:
    """Render one search hit as a self-contained card."""
    code = hit.get("code") or "?"
    label = hit.get("label") or ""
    score = float(hit.get("score") or 0)
    parent_label = hit.get("parent_label") or ""
    parent_code = hit.get("parent_code") or ""
    hit_id = hit.get("id")  # may be missing on certain edge cases

    # Severity badge based on similarity score
    if score >= 0.30:
        badge = "🟢 strong match"
    elif score >= 0.15:
        badge = "🟡 partial match"
    else:
        badge = "⚪ weak match"

    with st.container(border=True):
        # Title row: code + badge + score
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(f"### `{code}`")
            cat_label = _category_label(hit.get("category"))
            breadcrumb_parts = [cat_label] if cat_label else []
            if parent_code and parent_code != code:
                breadcrumb_parts.append(f"`{parent_code}` {parent_label[:60]}")
            breadcrumb = "  ›  ".join(p for p in breadcrumb_parts if p)
            if breadcrumb:
                st.caption(breadcrumb)
        with c2:
            st.markdown(f"**{badge}**")
            st.caption(f"Similarity: `{score:.2f}`")

        # Full label (no truncation)
        st.markdown(label)

        # Show sub-entries if any — defensively skip when id missing
        if hit_id is not None:
            try:
                children = annex_i.get_children(hit_id)
                if children:
                    with st.expander(f"📂 {len(children)} sub-entries"):
                        for c in children:
                            st.markdown(
                                f"- **`{c.get('code', '?')}`** — {c.get('label', '')}"
                            )
            except Exception as exc:
                st.caption(f"_(sub-entries unavailable: {exc})_")


def _render_manual_hit(row: dict, query: str | None = None) -> None:
    """Render a hit from manual_entries (e.g. the Dutch TXT data).

    If the match was found in `full_content` (not in label), show a snippet
    around the match so the user sees the context.
    """
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        try:
            import json as _json
            payload = _json.loads(payload)
        except Exception:
            payload = {}

    full_content = payload.get("full_content", "") if isinstance(payload, dict) else ""
    label = row.get("entry_label") or ""
    snippet = None

    # If query was provided and match is in full_content (not in label), extract a snippet
    if query:
        q_low = query.lower()
        if q_low not in label.lower() and full_content and q_low in full_content.lower():
            idx = full_content.lower().find(q_low)
            start = max(0, idx - 80)
            end = min(len(full_content), idx + len(query) + 200)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(full_content) else ""
            snippet = f"{prefix}{full_content[start:end]}{suffix}"

    with st.container(border=True):
        c1, c2 = st.columns([4, 1])
        with c1:
            st.markdown(f"### `{row['entry_key']}`")
            st.caption(f"📚 {row['source_name']} · v{row['version']}")
        with c2:
            if isinstance(payload, dict) and payload.get("language"):
                st.markdown(f"🌐 **{payload['language']}**")

        st.markdown(label)

        if snippet:
            st.caption("**Match in narrative:**")
            # Highlight the query term in the snippet
            import re
            highlighted = re.sub(
                re.escape(query),
                lambda m: f"**`{m.group()}`**",
                snippet,
                flags=re.IGNORECASE,
            )
            st.markdown(f"_{highlighted}_")


# ----------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------
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
        "DG TRADE Excel."
    )
else:
    st.caption(
        f"Using **{active['source_name']}** "
        f"(version `{active['version']}`, {active['row_count']} rows incl. tree nodes)"
    )

# ----------------------------------------------------------------------
# Inputs
# ----------------------------------------------------------------------
col_q, col_code = st.columns([2, 1])
with col_q:
    query = st.text_input(
        "Product description or keyword (EN — matches structured Annex I)",
        placeholder="e.g. 'centrifuge', 'lithography', 'rebreather', 'quantum'",
    )
with col_code:
    exact_code = st.text_input(
        "Or look up an exact ECN code",
        placeholder="e.g. 3A001",
        help="Exact match — bypasses similarity search.",
    )

col_search, col_options = st.columns([1, 3])
with col_search:
    search_clicked = st.button("🔍 Search", type="primary", use_container_width=True)
with col_options:
    min_score = st.slider(
        "Minimum similarity score",
        min_value=0.05, max_value=0.50, value=0.10, step=0.05,
        help="Higher = stricter. Default 0.10 hides very weak matches.",
    )


if search_clicked and (query.strip() or exact_code.strip()):

    # ---- Exact code lookup ------------------------------------------
    if exact_code.strip():
        item = annex_i.get_by_code(exact_code.strip())
        if item:
            st.success(f"Exact match for `{item['code']}`")
            ancestors = annex_i.get_ancestors(item["id"])
            with st.container(border=True):
                if len(ancestors) > 1:
                    breadcrumb = " › ".join(
                        f"`{a['code']}` {(a['label'] or '')[:50]}"
                        for a in ancestors[:-1]
                    )
                    st.caption(breadcrumb)
                st.markdown(f"### `{item['code']}`")
                st.write(item["label"])
                children = annex_i.get_children(item["id"])
                if children:
                    st.markdown(f"**Sub-entries ({len(children)}):**")
                    for c in children:
                        st.markdown(f"- `{c['code']}` — {c['label']}")
        else:
            st.warning(f"No entry with code `{exact_code}`.")

    # ---- Similarity search against Annex I --------------------------
    if query.strip():
        try:
            matches = annex_i.search_labels(query.strip(), limit=50)
        except Exception as exc:
            st.error(f"Search failed: {exc}")
            matches = []

        # Apply user's min_score filter
        matches = [m for m in matches if float(m.get("score") or 0) >= min_score]

        if matches:
            st.subheader(f"📚 Annex I — {len(matches)} hit(s)")
            for m in matches[:20]:  # cap rendering
                _render_hit_card(m, query=query.strip())
            if len(matches) > 20:
                st.caption(f"…and {len(matches) - 20} more weaker matches (raise the threshold above to filter).")
        else:
            st.info(
                f"No Annex I entries above similarity {min_score}. "
                "Try a different keyword or lower the threshold."
            )

        # ---- Search manual_entries too (Dutch TXT etc) --------------
        manual = run_query(
            """
            SELECT  me.entry_key,
                    me.entry_label,
                    me.payload,
                    ds.source_name,
                    ds.version
            FROM    manual_entries me
            JOIN    data_sources ds ON ds.id = me.source_id
            WHERE   ds.is_active = TRUE
              AND   (
                       me.entry_label                ILIKE :like
                    OR me.entry_key                  ILIKE :like
                    OR me.payload->>'full_content'   ILIKE :like
                    )
            ORDER BY
                CASE
                    WHEN me.entry_label ILIKE :like THEN 1
                    WHEN me.entry_key   ILIKE :like THEN 2
                    ELSE 3
                END,
                me.entry_key
            LIMIT 50
            """,
            {"like": f"%{query.strip()}%"},
        )
        if manual:
            st.subheader(f"📝 Custom / language sources — {len(manual)} hit(s)")
            for m in manual[:20]:
                _render_manual_hit(m, query=query.strip())
            if len(manual) > 20:
                st.caption(f"…and {len(manual) - 20} more.")
        else:
            st.info(
                "No hits in custom/language sources. "
                "If you expected Dutch hits: did you load the bundled NL data "
                "via Data sources → 'Import bundled Dutch Annex I'?"
            )
