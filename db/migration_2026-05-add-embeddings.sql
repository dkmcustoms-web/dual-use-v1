-- =====================================================================
-- Migration: add semantic search (pgvector) to the y901-sandbox DB.
-- Run this once in your Neon SQL editor before deploying the new code.
-- Everything is idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
-- =====================================================================

-- 1. Enable the pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Add embedding columns to annex_i_items (Excel) and manual_entries (TXT)
ALTER TABLE annex_i_items
    ADD COLUMN IF NOT EXISTS embedding         vector(1536),
    ADD COLUMN IF NOT EXISTS embedding_model   VARCHAR(50),
    ADD COLUMN IF NOT EXISTS embedded_at       TIMESTAMPTZ;

ALTER TABLE manual_entries
    ADD COLUMN IF NOT EXISTS embedding         vector(1536),
    ADD COLUMN IF NOT EXISTS embedding_model   VARCHAR(50),
    ADD COLUMN IF NOT EXISTS embedded_at       TIMESTAMPTZ;

-- 3. HNSW indexes for fast cosine-similarity search
CREATE INDEX IF NOT EXISTS idx_annex_i_embedding
    ON annex_i_items USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS idx_manual_entries_embedding
    ON manual_entries USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Verification queries (optional)
-- SELECT extversion FROM pg_extension WHERE extname = 'vector';
-- SELECT column_name FROM information_schema.columns WHERE table_name = 'annex_i_items' AND column_name = 'embedding';
