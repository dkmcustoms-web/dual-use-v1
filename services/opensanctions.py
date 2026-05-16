"""OpenSanctions API client.

Two endpoints are used:
    - /search/default     → free-text search (for the search-by-name page)
    - /match/default      → structured entity matching (for invoice screening)

Docs: https://api.opensanctions.org/
"""
from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE = "https://api.opensanctions.org"
DEFAULT_DATASET = "default"  # consolidated sanctions list


def _headers() -> dict[str, str]:
    key = os.environ.get("OPENSANCTIONS_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENSANCTIONS_API_KEY is not set. Add it to .env or to Railway variables."
        )
    return {
        "Authorization": f"ApiKey {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _base() -> str:
    return os.environ.get("OPENSANCTIONS_API_BASE", DEFAULT_BASE).rstrip("/")


def free_text_search(query: str, limit: int = 10, schema: str | None = None) -> dict[str, Any]:
    """Free-text search across the consolidated sanctions dataset.

    Returns the raw JSON response from OpenSanctions, containing `results`
    (list of entity matches with score, properties, datasets).
    """
    params: dict[str, Any] = {"q": query, "limit": limit}
    if schema:
        params["schema"] = schema  # 'Person', 'Company', 'LegalEntity', 'Vessel'
    resp = requests.get(
        f"{_base()}/search/{DEFAULT_DATASET}",
        headers=_headers(),
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def match_entity(
    name: str,
    schema: str = "LegalEntity",
    country: str | None = None,
    birth_date: str | None = None,
) -> dict[str, Any]:
    """Structured entity match — better precision than free-text.

    Use this from the invoice-screening page where you have a clear party
    name and (optionally) a country.

    `schema` should be 'Person', 'Company', 'LegalEntity', 'Vessel', or 'Organization'.
    """
    properties: dict[str, list[str]] = {"name": [name]}
    if country:
        properties["country"] = [country.upper()]
    if birth_date:
        properties["birthDate"] = [birth_date]

    payload = {
        "queries": {
            "q1": {
                "schema": schema,
                "properties": properties,
            }
        }
    }
    resp = requests.post(
        f"{_base()}/match/{DEFAULT_DATASET}",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("responses", {}).get("q1", {})


def dataset_metadata() -> dict[str, Any]:
    """Fetch metadata about the default dataset — used to capture the
    version/updated-at timestamp into the audit log."""
    resp = requests.get(
        f"{_base()}/datasets/{DEFAULT_DATASET}",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def hit_severity(score: float) -> str:
    """Map a match score to a severity label for the UI.

    OpenSanctions returns scores 0..1. The cutoffs below are conservative
    starting points — tune in your sandbox once you have feedback.
    """
    if score >= 0.85:
        return "ALERT"
    if score >= 0.65:
        return "REVIEW"
    return "INFO"
