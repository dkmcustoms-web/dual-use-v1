"""Queries against the loaded Annex I tree.

The active source is the most recent row in `data_sources` of type 'annex_i'
with is_active = TRUE.
"""
from __future__ import annotations

from db.connection import run_query


def get_active_annex_source() -> dict | None:
    rows = run_query("""
        SELECT id, source_name, version, loaded_at, row_count
        FROM   data_sources
        WHERE  source_type = 'annex_i' AND is_active = TRUE
        ORDER BY loaded_at DESC
        LIMIT 1
    """)
    return rows[0] if rows else None


def search_labels(query: str, limit: int = 50) -> list[dict]:
    """Trigram-similarity search on labels for actual product entries.

    Filters out internal "navigation" nodes (ROOT, category headers like "0",
    subgroup headers like "0A", "5C") and returns only real ECN entries
    (codes matching `^[0-9][A-E]\\d{3}`, optionally with sub-paths).

    Each row includes the parent's label so the UI can render breadcrumb
    context like "Category 5 — Telecommunications › 5C001 Resistors...".
    """
    source = get_active_annex_source()
    if not source:
        return []
    return run_query(
        """
        SELECT  a.id           AS id,
                a.parent_id    AS parent_id,
                a.code         AS code,
                a.label        AS label,
                a.category     AS category,
                a.subgroup     AS subgroup,
                a.depth        AS depth,
                p.label        AS parent_label,
                p.code         AS parent_code,
                similarity(a.label, :q) AS score
        FROM    annex_i_items a
        LEFT JOIN annex_i_items p
               ON p.id = a.parent_id
              AND p.source_id = a.source_id
        WHERE   a.source_id = :sid
          AND   a.code ~ '^[0-9][A-E][0-9]{3}'   -- real entries only
          AND   similarity(a.label, :q) > 0.05
        ORDER BY similarity(a.label, :q) DESC, a.depth ASC, a.code ASC
        LIMIT   :limit
        """,
        {"q": query, "sid": source["id"], "limit": limit},
    )


def get_by_code(code: str) -> dict | None:
    """Look up a specific Annex I item by exact code (e.g. '3A001.b.7.')."""
    source = get_active_annex_source()
    if not source:
        return None
    rows = run_query(
        """
        SELECT id, parent_id, code, label, category, subgroup, depth
        FROM   annex_i_items
        WHERE  source_id = :sid AND code = :code
        LIMIT  1
        """,
        {"sid": source["id"], "code": code},
    )
    return rows[0] if rows else None


def get_ancestors(item_id: int) -> list[dict]:
    """Walk up the tree to return all ancestors (used for breadcrumb context)."""
    source = get_active_annex_source()
    if not source:
        return []
    return run_query(
        """
        WITH RECURSIVE anc(id, parent_id, code, label, depth) AS (
            SELECT id, parent_id, code, label, depth
            FROM   annex_i_items
            WHERE  source_id = :sid AND id = :item_id
          UNION ALL
            SELECT p.id, p.parent_id, p.code, p.label, p.depth
            FROM   annex_i_items p
            JOIN   anc c ON c.parent_id = p.id
            WHERE  p.source_id = :sid
        )
        SELECT id, parent_id, code, label, depth FROM anc
        ORDER BY depth ASC
        """,
        {"sid": source["id"], "item_id": item_id},
    )


def get_children(parent_id: int | None) -> list[dict]:
    """Direct children of a node (or top-level if parent_id is None)."""
    source = get_active_annex_source()
    if not source:
        return []
    if parent_id is None:
        return run_query(
            """
            SELECT id, code, label, depth
            FROM   annex_i_items
            WHERE  source_id = :sid AND parent_id IS NULL
            ORDER BY code
            """,
            {"sid": source["id"]},
        )
    return run_query(
        """
        SELECT id, code, label, depth
        FROM   annex_i_items
        WHERE  source_id = :sid AND parent_id = :pid
        ORDER BY code
        """,
        {"sid": source["id"], "pid": parent_id},
    )


def list_categories() -> list[dict]:
    """Top-level categories (10 of them: '0' through '9')."""
    source = get_active_annex_source()
    if not source:
        return []
    return run_query(
        """
        SELECT id, code, label
        FROM   annex_i_items
        WHERE  source_id = :sid
          AND  code ~ '^[0-9]$'
        ORDER BY code
        """,
        {"sid": source["id"]},
    )
