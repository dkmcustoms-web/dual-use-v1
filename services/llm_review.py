"""LLM-based compliance review via Anthropic Claude.

Uses prompts stored in the `compliance_prompts` table. The default prompt
(below) is the EU sanctions/customs compliance prompt the user provided.
It can be seeded into the database via the Data Sources page or by calling
seed_default_prompt() at runtime.
"""
from __future__ import annotations

import os
import re
from typing import Any

from db.connection import execute, run_query


# ---------------------------------------------------------------------
# Default system prompt — user-provided EU sanctions compliance prompt
# ---------------------------------------------------------------------
DEFAULT_PROMPT_NAME = "EU Sanctions & Customs Compliance"
DEFAULT_PROMPT_VERSION = "v1"
DEFAULT_PROMPT_MODEL = "claude-sonnet-4-6"

DEFAULT_SYSTEM_PROMPT = """You are a senior EU sanctions and customs compliance assistant specialized in EU Russia/Belarus sanctions, export controls, customs compliance, and trade restrictions.

Your task is to analyse shipments, parties, goods, countries, transport routes, vessels, and transactions for possible sanctions or export control risks.

You must ALWAYS apply a conservative compliance-first approach.

You may ONLY use official sources when additional information is required:
- EU Sanctions Map
- European Commission sanctions pages
- EUR-Lex
- Belgian FOD Financiën
- Dutch Customs Tariff (tarief.douane.nl)
- EU TARIC
- Belgian Tariff Browser
- OFAC
- UK OFSI
- UN sanctions lists

NEVER use blogs, forums, unofficial summaries, Reddit, LinkedIn posts, or commercial websites as legal authority.

==================================================
CORE ANALYSIS REQUIREMENTS
==================================================

You must verify and analyse:

1. Parties involved
- Exporter
- Importer
- Consignee
- Notify party
- End user
- Banks
- Vessel operator
- Freight forwarder if relevant

2. Sanctions screening
Check for:
- EU sanctions listings
- OFAC SDN listings
- UK sanctions
- UN sanctions
- Ownership/control risks
- 50% ownership rule
- Indirect control indicators
- Possible aliases or spelling variations

3. Goods analysis
Analyse:
- HS/TARIC codes
- Product descriptions
- Technical characteristics
- Possible dual-use classification
- Russia annex restrictions
- Luxury goods restrictions
- Industrial goods restrictions
- Energy sector restrictions
- Aviation/maritime restrictions
- Military/end-use concerns

4. Route analysis
Evaluate:
- Country of export
- Country of destination
- Transit countries
- High-risk circumvention routes
- Re-export risks
- Suspicious logistics patterns

5. Circumvention indicators
Actively assess:
- Sudden route changes
- Third-country diversion risks
- Missing end-user clarity
- Shell/distribution companies
- Mismatch between goods and customer activity
- High-risk jurisdictions
- Unusual payment structures
- Suspicious vessel activity

6. Vessel screening (if applicable)
Review:
- Vessel sanctions
- Shadow fleet indicators
- AIS manipulation indicators
- Russian-linked operators
- Ownership/operator concerns

==================================================
HIGH-RISK COUNTRIES
==================================================

Treat the following countries as elevated circumvention risk jurisdictions for Russia sanctions:

- Turkey
- UAE
- Kazakhstan
- Armenia
- Kyrgyzstan
- Serbia
- Uzbekistan
- China
- Hong Kong
- Georgia

This does NOT automatically mean prohibited, but requires enhanced scrutiny.

==================================================
REQUIRED OUTPUT STRUCTURE
==================================================

Always structure your answer as follows:

1. Summary
- Short overall compliance conclusion

2. Risk Level
Use:
- LOW
- MEDIUM
- HIGH
- CRITICAL

3. Findings
List all identified concerns.

4. Sanctions Analysis
Explain:
- Listed party checks
- Ownership concerns
- Annex risks
- Dual-use concerns
- Transit concerns
- Circumvention concerns

5. Missing Information
Clearly state what additional information is required.

6. Recommendation
Use compliance-first recommendations:
- Allowed
- Allowed with caution
- Escalate to compliance
- Do not proceed
- Require legal review

7. Legal Basis
Always reference:
- EU regulation numbers
- Relevant annexes
- Official sources consulted

==================================================
STRICT RULES
==================================================

- NEVER state something is compliant unless sufficiently verified.
- If uncertain, explicitly say uncertainty exists.
- NEVER invent legal interpretations.
- NEVER assume end-use.
- NEVER assume ownership structures.
- NEVER ignore circumvention indicators.
- ALWAYS escalate doubtful cases.
- ALWAYS explain reasoning.
- ALWAYS distinguish between confirmed facts and assumptions.

==================================================
CUSTOMS-SPECIFIC RULES
==================================================

For customs declarations also evaluate:
- Import/export prohibition risks
- License requirements
- Additional document requirements
- Transit restrictions
- Indirect representation risk
- Fiscal representation exposure
- CBAM or dual-use implications if relevant

==================================================
AI BEHAVIOUR
==================================================

You must behave like:
- a cautious compliance officer
- an EU sanctions specialist
- a customs legal analyst

NOT like:
- a salesman
- a general chatbot
- a casual assistant

When in doubt:
ESCALATE."""


# ---------------------------------------------------------------------
# Prompt CRUD
# ---------------------------------------------------------------------
def list_prompts() -> list[dict]:
    return run_query(
        """
        SELECT id, name, version, model, temperature, max_tokens,
               is_active, created_at, notes,
               LENGTH(system_prompt) AS prompt_length
        FROM   compliance_prompts
        ORDER BY created_at DESC
        """
    )


def get_active_prompt() -> dict | None:
    rows = run_query(
        """
        SELECT id, name, version, system_prompt, model, temperature, max_tokens
        FROM   compliance_prompts
        WHERE  is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    return rows[0] if rows else None


def get_prompt(prompt_id: int) -> dict | None:
    rows = run_query(
        "SELECT * FROM compliance_prompts WHERE id = :id",
        {"id": prompt_id},
    )
    return rows[0] if rows else None


def save_prompt(
    name: str,
    version: str,
    system_prompt: str,
    model: str = DEFAULT_PROMPT_MODEL,
    temperature: float = 0.0,
    max_tokens: int = 4000,
    notes: str = "",
    activate: bool = True,
) -> int:
    """Insert a new prompt and optionally activate it (deactivating others)."""
    rows = run_query(
        """
        INSERT INTO compliance_prompts
            (name, version, system_prompt, model, temperature, max_tokens, is_active, notes)
        VALUES (:n, :v, :sp, :m, :t, :mt, :act, :notes)
        ON CONFLICT (name, version) DO UPDATE
        SET system_prompt = EXCLUDED.system_prompt,
            model         = EXCLUDED.model,
            temperature   = EXCLUDED.temperature,
            max_tokens    = EXCLUDED.max_tokens,
            notes         = EXCLUDED.notes
        RETURNING id
        """,
        {
            "n": name,
            "v": version,
            "sp": system_prompt,
            "m": model,
            "t": temperature,
            "mt": max_tokens,
            "act": activate,
            "notes": notes,
        },
    )
    new_id = rows[0]["id"]
    if activate:
        execute(
            "UPDATE compliance_prompts SET is_active = FALSE WHERE id <> :id",
            {"id": new_id},
        )
    return new_id


def activate_prompt(prompt_id: int) -> None:
    execute(
        "UPDATE compliance_prompts SET is_active = (id = :id)",
        {"id": prompt_id},
    )


def delete_prompt(prompt_id: int) -> None:
    execute("DELETE FROM compliance_prompts WHERE id = :id", {"id": prompt_id})


def seed_default_prompt() -> int:
    """Insert the default EU compliance prompt if no prompts exist."""
    existing = run_query("SELECT COUNT(*) AS n FROM compliance_prompts")
    if existing and existing[0]["n"] > 0:
        # Already have prompts; check whether the default one exists
        match = run_query(
            "SELECT id FROM compliance_prompts WHERE name = :n AND version = :v",
            {"n": DEFAULT_PROMPT_NAME, "v": DEFAULT_PROMPT_VERSION},
        )
        if match:
            return match[0]["id"]
    return save_prompt(
        name=DEFAULT_PROMPT_NAME,
        version=DEFAULT_PROMPT_VERSION,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        model=DEFAULT_PROMPT_MODEL,
        temperature=0.0,
        max_tokens=4000,
        notes="Default EU sanctions & customs compliance prompt (seeded automatically).",
        activate=True,
    )


# ---------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------
def run_llm_review(prompt: dict, user_context: str) -> dict:
    """Call Anthropic with the given prompt + user context.

    Returns dict with raw_response, model, input_tokens, output_tokens,
    risk_level, recommendation (parsed best-effort).
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env locally or to "
            "Railway Variables."
        )
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "anthropic package not installed. Add it to requirements.txt "
            "and redeploy."
        ) from exc

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=prompt["model"],
        max_tokens=int(prompt.get("max_tokens", 4000)),
        temperature=float(prompt.get("temperature", 0.0)),
        system=prompt["system_prompt"],
        messages=[{"role": "user", "content": user_context}],
    )

    # Anthropic returns content as a list of blocks; we expect one text block
    raw_text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            raw_text += block.text
        elif hasattr(block, "text"):
            raw_text += block.text

    parsed = parse_structured_response(raw_text)
    return {
        "raw_response": raw_text,
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "stop_reason": response.stop_reason,
        **parsed,
    }


# ---------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------
_RISK_RE = re.compile(
    r"(?:risk\s*level|2\.\s*risk\s*level)[:\s\n\-]*(LOW|MEDIUM|HIGH|CRITICAL)",
    re.IGNORECASE,
)
_RECO_RE = re.compile(
    r"(?:recommendation|6\.\s*recommendation)[:\s\n\-]*"
    r"(Allowed with caution|Allowed|Escalate to compliance|"
    r"Do not proceed|Require legal review)",
    re.IGNORECASE,
)


def parse_structured_response(text: str) -> dict[str, Any]:
    """Best-effort extraction of Risk Level and Recommendation."""
    out: dict[str, Any] = {"risk_level": None, "recommendation": None}

    m = _RISK_RE.search(text)
    if m:
        out["risk_level"] = m.group(1).upper()

    m = _RECO_RE.search(text)
    if m:
        out["recommendation"] = m.group(1).strip()

    return out


def extract_sections(text: str) -> dict[str, str]:
    """Split the response into the 7 sections the prompt mandates.

    Used for nicer rendering. Falls back to a single 'full' section if the
    LLM didn't follow the structure.
    """
    section_titles = [
        "Summary",
        "Risk Level",
        "Findings",
        "Sanctions Analysis",
        "Missing Information",
        "Recommendation",
        "Legal Basis",
    ]
    # Build a regex that splits on numbered or unnumbered section headers
    pattern = (
        r"(?:^|\n)\s*(?:\d+\.\s*)?(" +
        "|".join(re.escape(t) for t in section_titles) +
        r")\s*[\n:]"
    )
    parts = re.split(pattern, text, flags=re.IGNORECASE)

    if len(parts) < 3:
        return {"_full": text.strip()}

    # parts = ['prefix', 'Section1', 'body1', 'Section2', 'body2', ...]
    sections: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        title = parts[i].strip().title()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections[title] = body
    return sections


# ---------------------------------------------------------------------
# Pricing & cost calculation
# ---------------------------------------------------------------------
# Standard tier pricing per MILLION tokens (USD).
# Source: https://www.anthropic.com/pricing (verified May 2026).
# Does NOT account for prompt caching (90% off cached input) or
# batch processing (50% off) — those need separate handling.
PRICING_USD_PER_MTOK = {
    "claude-opus-4-7":           {"input": 5.00, "output": 25.00},
    "claude-opus-4-6":           {"input": 5.00, "output": 25.00},
    "claude-sonnet-4-6":         {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 1.00, "output": 5.00},
    "claude-haiku-4-5":          {"input": 1.00, "output": 5.00},
    # Legacy fallbacks (best-effort)
    "claude-sonnet-4-5":         {"input": 3.00, "output": 15.00},
    "claude-opus-4-5":           {"input": 15.00, "output": 75.00},
}

# Indicative USD → EUR rate. Kept as a constant; refresh occasionally.
# This is purely for display — actual billing is in USD.
USD_TO_EUR = 0.92


def calculate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> dict | None:
    """Compute estimated USD + EUR cost for a single API call.

    Returns None if the model is not in the pricing table. Falls back to
    prefix matching for models with date suffixes (e.g. claude-haiku-4-5-X).
    """
    if not model:
        return None
    rates = PRICING_USD_PER_MTOK.get(model)
    if not rates:
        # Prefix match for date-suffixed model strings
        for key in PRICING_USD_PER_MTOK:
            if model.startswith(key):
                rates = PRICING_USD_PER_MTOK[key]
                break
    if not rates:
        return None

    input_usd = (input_tokens or 0) / 1_000_000 * rates["input"]
    output_usd = (output_tokens or 0) / 1_000_000 * rates["output"]
    total_usd = input_usd + output_usd
    return {
        "input_usd": round(input_usd, 6),
        "output_usd": round(output_usd, 6),
        "total_usd": round(total_usd, 6),
        "total_eur": round(total_usd * USD_TO_EUR, 6),
        "rate_in":  rates["input"],
        "rate_out": rates["output"],
    }


# ---------------------------------------------------------------------
# Product verdict — focused per-product compliance Q&A
# Different from the full shipment review: takes a search term + the
# top hybrid-search candidates, returns ONE readable paragraph + a
# short verification checklist. ~150 words, written for a Belgian
# operator. Costs ~$0.005-0.02 per call (Sonnet, ~3k in / ~600 out).
# ---------------------------------------------------------------------
PRODUCT_VERDICT_SYSTEM_PROMPT = """Je bent een Belgische compliance assistent voor EU export controls (dual-use \
Verordening 2021/821, Annex I). Een operator zoekt een product op in de dual-use \
database en wil weten of het mogelijk gecontroleerd is.

Je krijgt:
- De zoekterm van de operator
- De top kandidaat ECN-codes uit Annex I (uit hybrid search), met labels en — \
waar beschikbaar — de volledige Annex I tekst van die entry

Je doel: een leesbaar, praktisch antwoord van ~150 woorden gevolgd door een korte \
verificatielijst en de relevante codes.

FORMAT van je antwoord (gewone Markdown, geen JSON, geen extra titels boven de paragraaf):

[Eén leesbaar paragraaf van max 180 woorden:
 - Begin met de samenvattende conclusie: wel/niet dual-use, en welke voorwaarden bepalen dat
 - Noem het kernonderscheid dat bepaalt of het gecontroleerd is (bv. type apparaat, technische spec, eindgebruik)
 - Vermeld de meest relevante ECN-code(s) inline (bv. "valt onder 8A002.q als rebreather")
 - Indien relevant: vermeld catch-all artikel 4 van 2021/821 of de Militaire Goederenlijst
 - Vermijd "het hangt af van" zonder concrete voorbeelden te geven]

**Verificatievragen voor de exporteur:**
- [3 tot 5 specifieke vragen die de operator MOET stellen om vergunningplicht vast te stellen. \
Concreet, niet vaag. Bijvoorbeeld: "Is dit een rebreather (gesloten of half-gesloten circuit) of een gewone open-circuit aqualung?"]

**Relevante ECN-codes:**
- `CODE` — korte uitleg in één zin
- `CODE` — korte uitleg in één zin

BELANGRIJK:
- Schrijf in het Nederlands (operator is Belgisch)
- Praktisch, geen juridisch jargon
- Verwijs nooit naar ECN-codes die NIET in de kandidatenlijst staan — die ken je niet en kan je niet bevestigen
- Geen juridisch advies, alleen compliance-guidance
- Als de zoekterm te vaag is voor een conclusie, zeg dat eerlijk en stel betere zoektermen voor
- De kandidaten kunnen Engels of Nederlands zijn — gebruik beide om context op te bouwen, maar antwoord in NL
"""


def run_product_verdict(
    query: str,
    candidates: list[dict],
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 1500,
) -> dict:
    """Get a focused compliance verdict for a single product query.

    Args:
        query: the user's search term (NL or EN)
        candidates: list of dicts; each should have at least
                    {code, label, full_content?, parent_code?, parent_label?, language?}
        model: Anthropic model id
        max_tokens: cap on output

    Returns dict with: verdict_text, model, input_tokens, output_tokens, stop_reason.
    Raises if ANTHROPIC_API_KEY is missing or the call fails.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package not installed.") from exc

    # Build the candidate block, capping each entry's full_content
    block_parts = []
    for i, c in enumerate(candidates[:10], start=1):
        code = c.get("code") or "?"
        label = c.get("label") or ""
        lang = c.get("language") or ("EN" if not c.get("manual_payload") else "?")
        parent = ""
        if c.get("parent_code") and c.get("parent_code") != code:
            parent = f" (parent: `{c['parent_code']}` {(c.get('parent_label') or '')[:80]})"
        chunk = f"### Kandidaat {i}: `{code}` [{lang}]{parent}\n**Label:** {label}\n"
        # Full content from manual_entries (NL/EN TXT) — cap at 1500 chars per entry
        full = ""
        payload = c.get("manual_payload")
        if isinstance(payload, dict):
            full = payload.get("full_content", "") or ""
        elif c.get("full_content"):
            full = c["full_content"]
        if full:
            full = full[:1500]
            chunk += f"**Annex I content excerpt:**\n{full}\n"
        block_parts.append(chunk)

    candidate_block = "\n\n".join(block_parts) if block_parts else "(geen kandidaten gevonden — zoekterm matchte niets)"

    user_msg = (
        f"**Zoekterm van de operator:** {query}\n\n"
        f"**Top kandidaat ECN-codes uit hybrid search:**\n\n"
        f"{candidate_block}\n\n"
        f"Geef de operator een compliance-verdict voor deze zoekterm."
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
        system=PRODUCT_VERDICT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text += block.text
        elif hasattr(block, "text"):
            text += block.text

    return {
        "verdict_text": text,
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "stop_reason": response.stop_reason,
        "candidates_used": len(block_parts),
    }
