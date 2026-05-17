-- =====================================================================
-- Migration: add web-augmented review columns to screenings.
-- Run once in Neon SQL editor. Idempotent.
-- =====================================================================

ALTER TABLE screenings
    ADD COLUMN IF NOT EXISTS llm_web_raw_response   TEXT,
    ADD COLUMN IF NOT EXISTS llm_web_model          VARCHAR(100),
    ADD COLUMN IF NOT EXISTS llm_web_input_tokens   INTEGER,
    ADD COLUMN IF NOT EXISTS llm_web_output_tokens  INTEGER,
    ADD COLUMN IF NOT EXISTS llm_web_searches_used  INTEGER,
    ADD COLUMN IF NOT EXISTS llm_web_citations      JSONB,
    ADD COLUMN IF NOT EXISTS llm_web_risk_level     VARCHAR(20),
    ADD COLUMN IF NOT EXISTS llm_web_recommendation VARCHAR(100),
    ADD COLUMN IF NOT EXISTS llm_web_search_queries JSONB;
