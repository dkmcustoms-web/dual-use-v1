"""Orchestrate a full screening run: parties + country + products → hits → audit log."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from db.connection import execute, run_query
from services import annex_i, opensanctions


@dataclass
class ScreeningInput:
    screening_type: str                          # 'invoice', 'name_lookup', 'product_lookup'
    parties: list[str] = field(default_factory=list)
    destination_country: str | None = None
    product_descriptions: list[str] = field(default_factory=list)
    pdf_filename: str | None = None
    pdf_text_excerpt: str | None = None


@dataclass
class Hit:
    hit_type: str          # 'opensanctions', 'annex_i', 'manual'
    severity: str          # 'INFO', 'REVIEW', 'ALERT'
    matched_term: str
    matched_entity: str
    score: float | None
    source_reference: str
    payload: dict[str, Any]


@dataclass
class ScreeningResult:
    screening_id: int
    hits: list[Hit]
    summary_status: str    # 'OK', 'REVIEW', 'ALERT'
    summary_text: str


_SEVERITY_RANK = {"INFO": 0, "OK": 0, "REVIEW": 1, "ALERT": 2}


def _worst(severities: list[str]) -> str:
    if not severities:
        return "OK"
    return max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0))


def run_screening(inp: ScreeningInput) -> ScreeningResult:
    """Run all available checks and persist the audit trail."""
    hits: list[Hit] = []

    # ---- Party screening via OpenSanctions ---------------------------
    for party in inp.parties:
        if not party.strip():
            continue
        try:
            data = opensanctions.match_entity(
                name=party,
                schema="LegalEntity",
                country=inp.destination_country,
            )
            for result in data.get("results", []):
                score = float(result.get("score") or 0)
                if score < 0.50:
                    continue
                hits.append(Hit(
                    hit_type="opensanctions",
                    severity=opensanctions.hit_severity(score),
                    matched_term=party,
                    matched_entity=result.get("caption") or "(unnamed)",
                    score=score,
                    source_reference=", ".join(result.get("datasets", [])),
                    payload=result,
                ))
        except Exception as exc:
            hits.append(Hit(
                hit_type="opensanctions",
                severity="REVIEW",
                matched_term=party,
                matched_entity="(API error)",
                score=None,
                source_reference="opensanctions",
                payload={"error": str(exc)},
            ))

    # ---- Product screening against Annex I labels --------------------
    for desc in inp.product_descriptions:
        if not desc.strip() or len(desc.strip()) < 8:
            continue
        matches = annex_i.search_labels(desc, limit=5)
        for m in matches:
            score = float(m["score"]) if m.get("score") is not None else 0
            if score < 0.10:
                continue
            severity = "REVIEW" if score >= 0.30 else "INFO"
            hits.append(Hit(
                hit_type="annex_i",
                severity=severity,
                matched_term=desc,
                matched_entity=f"{m['code']} — {(m['label'] or '')[:120]}",
                score=score,
                source_reference="EU Annex I",
                payload=m,
            ))

    summary_status = _worst([h.severity for h in hits])
    summary_text = _build_summary(hits, summary_status)

    # ---- Capture dataset version for audit ---------------------------
    try:
        ds_meta = opensanctions.dataset_metadata()
        os_version = ds_meta.get("version") or ds_meta.get("updated_at") or "unknown"
    except Exception:
        os_version = "unavailable"

    annex_source = annex_i.get_active_annex_source()
    annex_source_id = annex_source["id"] if annex_source else None

    # ---- Persist the screening ---------------------------------------
    inputs_json = json.dumps({
        "parties": inp.parties,
        "destination_country": inp.destination_country,
        "product_descriptions": inp.product_descriptions,
    })

    screening_id = _insert_screening(
        screening_type=inp.screening_type,
        inputs_json=inputs_json,
        pdf_filename=inp.pdf_filename,
        pdf_text_excerpt=inp.pdf_text_excerpt,
        annex_source_id=annex_source_id,
        os_version=os_version,
        summary_status=summary_status,
        summary_text=summary_text,
    )

    for h in hits:
        execute(
            """
            INSERT INTO screening_hits
                (screening_id, hit_type, hit_severity, matched_term,
                 matched_entity, match_score, source_reference, payload)
            VALUES (:sid, :ht, :sev, :mt, :me, :sc, :sr, CAST(:pl AS JSONB))
            """,
            {
                "sid": screening_id,
                "ht": h.hit_type,
                "sev": h.severity,
                "mt": h.matched_term,
                "me": h.matched_entity,
                "sc": h.score,
                "sr": h.source_reference,
                "pl": json.dumps(h.payload, default=str),
            },
        )

    return ScreeningResult(
        screening_id=screening_id,
        hits=hits,
        summary_status=summary_status,
        summary_text=summary_text,
    )


def _build_summary(hits: list[Hit], status: str) -> str:
    if not hits:
        return "No matches found. All inputs cleared against current sources."
    n_alert = sum(1 for h in hits if h.severity == "ALERT")
    n_review = sum(1 for h in hits if h.severity == "REVIEW")
    n_info = sum(1 for h in hits if h.severity == "INFO")
    return (
        f"{status} — {len(hits)} hit(s): "
        f"{n_alert} alert, {n_review} review, {n_info} info."
    )


def _insert_screening(
    screening_type: str,
    inputs_json: str,
    pdf_filename: str | None,
    pdf_text_excerpt: str | None,
    annex_source_id: int | None,
    os_version: str,
    summary_status: str,
    summary_text: str,
) -> int:
    rows = run_query(
        """
        INSERT INTO screenings
            (screening_type, inputs, pdf_filename, pdf_text_excerpt,
             annex_i_source_id, opensanctions_dataset_version,
             summary_status, summary_text)
        VALUES (:st, CAST(:inp AS JSONB), :fn, :ex, :asid, :osv, :ss, :stx)
        RETURNING id
        """,
        {
            "st": screening_type,
            "inp": inputs_json,
            "fn": pdf_filename,
            "ex": (pdf_text_excerpt or "")[:2000],
            "asid": annex_source_id,
            "osv": os_version,
            "ss": summary_status,
            "stx": summary_text,
        },
    )
    return rows[0]["id"]


def recent_screenings(limit: int = 20) -> list[dict]:
    return run_query(
        """
        SELECT id, created_at, screening_type, summary_status, summary_text,
               pdf_filename
        FROM   screenings
        ORDER BY created_at DESC
        LIMIT  :limit
        """,
        {"limit": limit},
    )
