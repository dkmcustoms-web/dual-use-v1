"""Load the DG TRADE Annex I Excel into Postgres.

Usage:
    python scripts/load_annex_i.py path/to/excel.xlsx [version_label]

If version_label is omitted, defaults to the file's basename.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import openpyxl

# Make the project root importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import execute, execute_many, run_query  # noqa: E402


def load(excel_path: str, version_label: str | None = None) -> None:
    excel_path = str(excel_path)
    if version_label is None:
        # Try to extract a year-month from the filename, fall back to stem
        m = re.search(r"(20\d{2})[_\-]?(\d{2})", Path(excel_path).name)
        version_label = f"{m.group(1)}-{m.group(2)}" if m else Path(excel_path).stem

    wb = openpyxl.load_workbook(excel_path, data_only=True, read_only=True)
    ws = wb["Export Worksheet"]

    rows = list(ws.iter_rows(min_row=2, values_only=True))
    print(f"Read {len(rows)} rows from {excel_path}")

    # Register the source
    existing = run_query(
        "SELECT id FROM data_sources WHERE source_type='annex_i' AND version=:v",
        {"v": version_label},
    )
    if existing:
        source_id = existing[0]["id"]
        print(f"Source already registered (id={source_id}); replacing rows.")
        execute(
            "DELETE FROM annex_i_items WHERE source_id=:sid",
            {"sid": source_id},
        )
    else:
        new_rows = run_query(
            """
            INSERT INTO data_sources (source_type, source_name, version, row_count, notes)
            VALUES ('annex_i', :name, :version, :rc, :notes)
            RETURNING id
            """,
            {
                "name": f"EU Annex I Dual-Use ({version_label})",
                "version": version_label,
                "rc": len(rows),
                "notes": f"Loaded from {Path(excel_path).name}",
            },
        )
        source_id = new_rows[0]["id"]
        # Deactivate older versions
        execute(
            """
            UPDATE data_sources SET is_active = FALSE
            WHERE  source_type = 'annex_i' AND id <> :sid
            """,
            {"sid": source_id},
        )

    # Bulk insert
    insert_sql = """
        INSERT INTO annex_i_items
            (id, parent_id, code, label, category, subgroup, depth, source_id)
        VALUES
            (:id, :pid, :code, :label, :cat, :sub, :depth, :sid)
    """
    batch: list[dict] = []
    for r in rows:
        item_id, parent_id, code, label = r
        if item_id is None or code is None:
            continue
        code_str = str(code).strip()
        # Category = first character if it's a digit
        category = code_str[0] if code_str and code_str[0].isdigit() else None
        # Subgroup = second character if it's A-E (and we have a category digit)
        subgroup = None
        if category and len(code_str) >= 2 and code_str[1] in "ABCDE":
            subgroup = code_str[1]
        depth = code_str.count(".")
        # parent_id may be an empty string in some rows — normalize to None
        if parent_id in ("", None):
            pid_val: int | None = None
        else:
            try:
                pid_val = int(parent_id)
            except (TypeError, ValueError):
                pid_val = None

        batch.append({
            "id": int(item_id),
            "pid": pid_val,
            "code": code_str,
            "label": str(label) if label is not None else "",
            "cat": category,
            "sub": subgroup,
            "depth": depth,
            "sid": source_id,
        })

        if len(batch) >= 500:
            execute_many(insert_sql, batch)
            batch.clear()

    if batch:
        execute_many(insert_sql, batch)

    print(f"✓ Loaded {len(rows)} rows into annex_i_items (source_id={source_id}, version={version_label}).")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/load_annex_i.py path/to/excel.xlsx [version_label]")
        sys.exit(1)
    path = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else None
    load(path, label)
