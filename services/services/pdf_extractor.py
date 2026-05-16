"""PDF text extraction for uploaded invoices.

Uses pdfplumber. Returns raw text plus a heuristic attempt to identify
candidate parties and product descriptions. The heuristics are intentionally
simple — the user reviews/corrects them in the UI before screening runs.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pdfplumber


@dataclass
class ExtractedInvoice:
    full_text: str
    page_count: int
    candidate_parties: list[str] = field(default_factory=list)
    candidate_countries: list[str] = field(default_factory=list)
    candidate_products: list[str] = field(default_factory=list)


# Common invoice labels in EN/NL/FR/DE that precede a party name.
PARTY_LABELS = [
    r"consignor",
    r"consignee",
    r"ship\s*to",
    r"bill\s*to",
    r"sold\s*to",
    r"deliver\s*to",
    r"exporter",
    r"importer",
    r"shipper",
    r"buyer",
    r"seller",
    r"afzender",
    r"geadresseerde",
    r"verzender",
    r"ontvanger",
    r"expéditeur",
    r"destinataire",
    r"absender",
    r"empfänger",
]

# 2-letter country codes that commonly appear in invoices.
COUNTRY_CODE_RE = re.compile(r"\b([A-Z]{2})\b")

# Common product-line patterns (description column heuristics).
PRODUCT_LINE_RE = re.compile(
    r"^\s*(?:\d+\s+)?([A-Za-z][A-Za-z0-9 \-,./()&]{15,120})\s*(?:\d+[.,]?\d*)?",
    re.MULTILINE,
)


def extract_pdf(file_bytes: bytes) -> ExtractedInvoice:
    """Read PDF bytes and return text + heuristic candidates."""
    text_chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            t = page.extract_text() or ""
            text_chunks.append(t)
    full_text = "\n\n".join(text_chunks)

    return ExtractedInvoice(
        full_text=full_text,
        page_count=page_count,
        candidate_parties=_find_parties(full_text),
        candidate_countries=_find_countries(full_text),
        candidate_products=_find_products(full_text),
    )


def _find_parties(text: str) -> list[str]:
    """Look for lines after party-labels — return the next 1-3 non-empty lines."""
    out: list[str] = []
    lines = text.splitlines()
    label_pattern = re.compile(
        r"^\s*(?:" + "|".join(PARTY_LABELS) + r")\b[:\s]*", re.IGNORECASE
    )
    for i, line in enumerate(lines):
        if label_pattern.match(line):
            # Same line after the label
            remainder = label_pattern.sub("", line).strip()
            if remainder and len(remainder) > 2:
                out.append(remainder)
            # Plus the next 1-2 non-empty lines (often the name & address)
            for j in range(i + 1, min(i + 3, len(lines))):
                candidate = lines[j].strip()
                if candidate and len(candidate) > 2 and not label_pattern.match(candidate):
                    out.append(candidate)
                    break
    # De-duplicate while preserving order
    seen: set[str] = set()
    unique = []
    for p in out:
        norm = p.lower()
        if norm not in seen:
            seen.add(norm)
            unique.append(p)
    return unique[:10]


def _find_countries(text: str) -> list[str]:
    """Extract candidate ISO-2 country codes that appear at line ends or in
    address blocks. Returns unique codes sorted."""
    # Sample ISO-2 list — not exhaustive, just enough to filter noise
    iso2 = {
        "BE", "NL", "FR", "DE", "LU", "GB", "UK", "IE", "ES", "PT", "IT",
        "AT", "CH", "DK", "SE", "NO", "FI", "PL", "CZ", "SK", "HU", "RO",
        "BG", "GR", "HR", "SI", "EE", "LV", "LT", "MT", "CY",
        "US", "CA", "MX", "BR", "AR", "CL",
        "CN", "JP", "KR", "KP", "IN", "PK", "AF", "IR", "IQ", "SA", "AE",
        "RU", "BY", "UA", "TR", "IL", "EG", "ZA", "AU", "NZ",
    }
    found = set()
    for match in COUNTRY_CODE_RE.findall(text):
        if match in iso2:
            found.add(match)
    return sorted(found)


def _find_products(text: str) -> list[str]:
    """Extract candidate product-description lines.

    Very rough — picks lines that look like 'description text 123.45' (text
    followed by a number, typical of invoice line items).
    """
    matches = PRODUCT_LINE_RE.findall(text)
    out = []
    for m in matches:
        m = m.strip()
        # Filter out obvious header/footer noise
        if len(m) < 12:
            continue
        if any(skip in m.lower() for skip in [
            "invoice", "page ", "total", "subtotal", "vat", "btw", "tva",
            "address", "phone", "email", "iban", "swift",
        ]):
            continue
        out.append(m)
    # De-duplicate, cap
    seen = set()
    unique = []
    for p in out:
        norm = p.lower()
        if norm not in seen:
            seen.add(norm)
            unique.append(p)
    return unique[:20]
