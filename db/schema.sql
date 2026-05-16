-- Y901 sandbox database schema (Postgres / Neon)
-- Safe to run multiple times: every CREATE uses IF NOT EXISTS.


-- =====================================================================
-- EXTENSIONS — must come first because indices below depend on pg_trgm.
-- pg_trgm provides the trigram-similarity operators/functions used by
-- the fuzzy search on annex_i_items.label.
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- =====================================================================
-- DATA SOURCES — registry of every dataset loaded into the sandbox.
-- Every screening references a specific source version for audit trail.
-- =====================================================================
CREATE TABLE IF NOT EXISTS data_sources (
    id              SERIAL PRIMARY KEY,
    source_type     VARCHAR(50) NOT NULL,   -- 'annex_i', 'country_risk', 'manual', ...
    source_name     VARCHAR(200) NOT NULL,  -- 'EU Annex I Dual-Use Sept 2024'
    version         VARCHAR(50) NOT NULL,   -- '2024-09', '2025-11', 'v1', ...
    loaded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    row_count       INTEGER,
    notes           TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (source_type, version)
);

CREATE INDEX IF NOT EXISTS idx_data_sources_type_active
    ON data_sources (source_type, is_active);


-- =====================================================================
-- ANNEX I — the EU dual-use control list as a parent-child tree.
-- Loaded from the DG TRADE Excel via scripts/load_annex_i.py.
-- =====================================================================
CREATE TABLE IF NOT EXISTS annex_i_items (
    id                  INTEGER NOT NULL,            -- from Excel ID column
    parent_id           INTEGER,                     -- from Excel PARENT_ID
    code                VARCHAR(50) NOT NULL,        -- '0A001', '3A001.b.7.', 'ROOT'
    label               TEXT NOT NULL,
    category            VARCHAR(2),                  -- '0'..'9' (NULL for ROOT/CATEGORY rows)
    subgroup            VARCHAR(2),                  -- 'A','B','C','D','E'
    depth               INTEGER NOT NULL,            -- number of dots in code
    source_id           INTEGER NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    PRIMARY KEY (id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_annex_code        ON annex_i_items (code, source_id);
CREATE INDEX IF NOT EXISTS idx_annex_parent      ON annex_i_items (parent_id, source_id);
CREATE INDEX IF NOT EXISTS idx_annex_category    ON annex_i_items (category, subgroup, source_id);
-- Trigram index for fuzzy search on labels (uses pg_trgm extension created above)
CREATE INDEX IF NOT EXISTS idx_annex_label_trgm  ON annex_i_items
    USING gin (label gin_trgm_ops);


-- =====================================================================
-- MANUAL ENTRIES — free-form knowledge added via the Data Sources page.
-- E.g. country-risk lists, CN-to-ECN snippets you collect manually,
-- internal compliance notes, etc.
-- =====================================================================
CREATE TABLE IF NOT EXISTS manual_entries (
    id              SERIAL PRIMARY KEY,
    source_id       INTEGER NOT NULL REFERENCES data_sources (id) ON DELETE CASCADE,
    entry_key       VARCHAR(200) NOT NULL,   -- e.g. country code, CN code, product code
    entry_label     TEXT,
    payload         JSONB NOT NULL,          -- full row from uploaded CSV/JSON
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_manual_source_key ON manual_entries (source_id, entry_key);
CREATE INDEX IF NOT EXISTS idx_manual_payload    ON manual_entries USING gin (payload);


-- =====================================================================
-- SCREENINGS — audit log of every screening run.
-- Captures the inputs, the source-version snapshot, and outcomes.
-- =====================================================================
CREATE TABLE IF NOT EXISTS screenings (
    id                  SERIAL PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    screening_type      VARCHAR(50) NOT NULL,   -- 'invoice', 'name_lookup', 'product_lookup'
    inputs              JSONB NOT NULL,         -- captured inputs (parties, country, text, ...)
    pdf_filename        VARCHAR(500),
    pdf_text_excerpt    TEXT,                   -- first ~2000 chars of extracted text
    annex_i_source_id   INTEGER REFERENCES data_sources (id),
    opensanctions_dataset_version VARCHAR(100), -- captured at time of call
    summary_status      VARCHAR(20),            -- 'OK', 'REVIEW', 'ALERT'
    summary_text        TEXT,
    operator_decision   VARCHAR(50)             -- optional, set by user after review
);

CREATE INDEX IF NOT EXISTS idx_screenings_created ON screenings (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_screenings_type    ON screenings (screening_type, created_at DESC);


-- =====================================================================
-- SCREENING HITS — individual hits found during a screening.
-- One screening can produce many hits across many sources.
-- =====================================================================
CREATE TABLE IF NOT EXISTS screening_hits (
    id                  SERIAL PRIMARY KEY,
    screening_id        INTEGER NOT NULL REFERENCES screenings (id) ON DELETE CASCADE,
    hit_type            VARCHAR(50) NOT NULL,    -- 'opensanctions', 'annex_i', 'manual'
    hit_severity        VARCHAR(20) NOT NULL,    -- 'INFO', 'REVIEW', 'ALERT'
    matched_term        TEXT,                    -- what we searched for
    matched_entity      TEXT,                    -- what we found
    match_score         NUMERIC(5, 2),           -- 0.00..1.00 where applicable
    source_reference    TEXT,                    -- URL or list name
    payload             JSONB,                   -- full hit details
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hits_screening ON screening_hits (screening_id);
CREATE INDEX IF NOT EXISTS idx_hits_type      ON screening_hits (hit_type);
