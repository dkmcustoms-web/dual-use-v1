"""Parser for the consolidated EU dual-use regulation text (Dual_use.txt).

The file is a plain-text export of the HTML EUR-Lex consolidated regulation.
Sub-codes (like 8A002.q.1) are shown as bullets in the HTML but lose their
code prefix in plain-text conversion. To compensate, this parser captures
TWO things per top-level code:

  - label:        the first line (short description, ~10-200 chars)
  - full_content: ALL narrative text until the next code (often 100s-1000s
                  of chars, includes sub-bullets like "rebreathers")

The full_content is what makes free-text search like "rebreathers" or
"diving" actually find the right top-level ECN code, even though those
specific terms don't appear in the short label.
"""
from __future__ import annotations

import re

# Match an ECN-style code at the start of a line.
# Examples: 0A001, 1A001, 3A001.b.7
_CODE_RE = re.compile(
    r"^([0-9][A-E]\d{3}(?:\.[a-z0-9]+)*?)\.?\s+(.{15,})$",
    re.MULTILINE,
)

# Skip these as label prefixes (annotations, not entries)
_NOISE_PREFIXES = (
    "NB", "Noot", "NB:", "Noot:", "ZIE OOK", "Zie ook", "Zie",
    "Technical Note", "Technische opmerking",
)

# Cap full_content per entry. Raised to 20k because some top-level entries
# (like 8A002 with many sub-bullets) span thousands of chars before the
# next code appears. Postgres TEXT and JSONB handle this easily.
_MAX_CONTENT_CHARS = 20000


def parse_eu_regulation_txt(text: str) -> list[dict]:
    """Extract ECN entries with both short label and full narrative content.

    Returns list of dicts with keys:
        code, label, category, subgroup, depth, full_content
    """
    # First pass: find all code occurrences (position + match details)
    code_positions: list[tuple[int, int, str, str]] = []
    for m in _CODE_RE.finditer(text):
        code = m.group(1).rstrip(".")
        label = m.group(2).strip()
        if label.startswith(_NOISE_PREFIXES):
            continue
        code_positions.append((m.start(), m.end(), code, label))

    # Second pass: for each code, full_content = text from end-of-this-match
    # to start-of-next-match (so it includes all the narrative between codes).
    entries: list[dict] = []
    seen: set[str] = set()
    for i, (_, end_pos, code, label) in enumerate(code_positions):
        if code in seen:
            continue
        seen.add(code)

        if i + 1 < len(code_positions):
            next_start = code_positions[i + 1][0]
            chunk = text[end_pos:next_start]
        else:
            chunk = text[end_pos:end_pos + _MAX_CONTENT_CHARS]

        # Clean up the chunk: collapse whitespace, cap length
        chunk = re.sub(r"\s+", " ", chunk).strip()
        full_content = chunk[:_MAX_CONTENT_CHARS]

        # Derive metadata
        category = code[0] if code and code[0].isdigit() else None
        subgroup = code[1] if len(code) >= 2 and code[1] in "ABCDE" else None
        depth = code.count(".")

        entries.append({
            "code": code,
            "label": label[:500],
            "category": category,
            "subgroup": subgroup,
            "depth": depth,
            "full_content": full_content,
        })

    return entries
