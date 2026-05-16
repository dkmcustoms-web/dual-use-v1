"""Parser for the consolidated EU dual-use regulation text (Dual_use.txt).

The file is a plain-text export of the HTML EUR-Lex consolidated regulation.
Structure is messy (amendment markers ►B / ►M, line wraps, etc.), but ECN-style
codes (0A001, 1A001, 3A001.b.7, ...) reliably appear at the start of lines,
followed by their description.

This parser does NOT try to reconstruct the full hierarchy or technical
thresholds — it extracts code → first-line label pairs, which is what the
sandbox needs for Dutch keyword search alongside the structured English Excel.
"""
from __future__ import annotations

import re

# Match: code at line start, then whitespace, then a non-trivial label.
# Code patterns: 0A001, 0A001.a, 0A001.a.1, 3A001.b.7.a, etc.
_CODE_RE = re.compile(
    r"^([0-9][A-E]\d{3}(?:\.[a-z0-9]+)*?)\.?\s+(.{20,})$",
    re.MULTILINE,
)

# Skip these prefixes — they are EU regulation annotations, not entries
_NOISE_PREFIXES = (
    "NB", "Noot", "NB:", "Noot:", "ZIE OOK", "Zie ook", "Zie",
    "Technical Note", "Technische opmerking",
)


def parse_eu_regulation_txt(text: str) -> list[dict]:
    """Extract ECN-code → label pairs from a plaintext EU regulation file.

    Returns list of dicts with keys: code, label, category, subgroup, depth.
    Each code appears once (first occurrence wins).
    """
    entries: list[dict] = []
    seen: set[str] = set()

    for m in _CODE_RE.finditer(text):
        code = m.group(1).rstrip(".")
        label = m.group(2).strip()

        # Drop noise lines (NB notes, Noot definitions, ZIE OOK cross-refs)
        if label.startswith(_NOISE_PREFIXES):
            continue
        # Skip duplicates
        if code in seen:
            continue
        seen.add(code)

        # Derive category/subgroup/depth from the code
        category = code[0] if code and code[0].isdigit() else None
        subgroup = code[1] if len(code) >= 2 and code[1] in "ABCDE" else None
        depth = code.count(".")

        entries.append({
            "code": code,
            "label": label[:1000],  # cap very long labels
            "category": category,
            "subgroup": subgroup,
            "depth": depth,
        })

    return entries
