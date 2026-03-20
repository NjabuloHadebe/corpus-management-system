-- ══════════════════════════════════════════════════
-- IsiZulu Corpus Management System — Database Schema
-- Paste this entire file into:
--   Supabase Dashboard → SQL Editor → New Query → Run
-- ══════════════════════════════════════════════════

-- ── INC: documents ────────────────────────────────
CREATE TABLE IF NOT EXISTS inc_documents (
    id           SERIAL PRIMARY KEY,
    title        TEXT NOT NULL,
    filename     TEXT,
    filepath     TEXT,
    domain       TEXT,
    year         INTEGER,
    region       TEXT,
    ocr_score    REAL,
    word_count   INTEGER DEFAULT 0,
    token_count  INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'uploaded',
    uploaded_by  TEXT DEFAULT 'anonymous',
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ── INC: saved text versions ──────────────────────
CREATE TABLE IF NOT EXISTS inc_texts (
    id           SERIAL PRIMARY KEY,
    document_id  INTEGER REFERENCES inc_documents(id) ON DELETE CASCADE,
    text         TEXT NOT NULL,
    word_count   INTEGER DEFAULT 0,
    saved_by     TEXT DEFAULT 'anonymous',
    saved_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ── EIPC: bilingual document pairs ────────────────
CREATE TABLE IF NOT EXISTS eipc_documents (
    id            SERIAL PRIMARY KEY,
    title         TEXT,
    domain        TEXT,
    year          INTEGER,
    en_filename   TEXT,
    en_filepath   TEXT,
    zu_filename   TEXT,
    zu_filepath   TEXT,
    en_sentences  INTEGER DEFAULT 0,
    zu_sentences  INTEGER DEFAULT 0,
    uploaded_by   TEXT DEFAULT 'anonymous',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ── EIPC: aligned sentence pairs ──────────────────
CREATE TABLE IF NOT EXISTS eipc_pairs (
    id             SERIAL PRIMARY KEY,
    source_doc_id  INTEGER REFERENCES eipc_documents(id) ON DELETE CASCADE,
    en_text        TEXT NOT NULL,
    zu_text        TEXT NOT NULL,
    confidence     REAL,
    status         TEXT DEFAULT 'pending',
    aligned_by     TEXT DEFAULT 'anonymous',
    verified_at    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ── IOC: audio files ──────────────────────────────
CREATE TABLE IF NOT EXISTS ioc_files (
    id                SERIAL PRIMARY KEY,
    filename          TEXT NOT NULL,
    filepath          TEXT,
    region            TEXT,
    speaker_gender    TEXT,
    speaker_age_range TEXT,
    topic             TEXT,
    duration_seconds  INTEGER,
    status            TEXT DEFAULT 'uploaded',
    uploaded_by       TEXT DEFAULT 'anonymous',
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ── IOC: transcripts ──────────────────────────────
CREATE TABLE IF NOT EXISTS ioc_transcripts (
    id              SERIAL PRIMARY KEY,
    audio_file_id   INTEGER REFERENCES ioc_files(id) ON DELETE CASCADE,
    auto_text       TEXT,
    corrected_text  TEXT,
    is_approved     BOOLEAN DEFAULT FALSE,
    saved_by        TEXT DEFAULT 'anonymous',
    saved_at        TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes for fast search ───────────────────────
CREATE INDEX IF NOT EXISTS idx_inc_status   ON inc_documents(status);
CREATE INDEX IF NOT EXISTS idx_eipc_status  ON eipc_pairs(status);
CREATE INDEX IF NOT EXISTS idx_eipc_doc     ON eipc_pairs(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_ioc_status   ON ioc_files(status);

-- Full-text search index on INC text
CREATE INDEX IF NOT EXISTS idx_inc_text_fts
    ON inc_texts USING gin(to_tsvector('simple', text));

-- Full-text on EIPC
CREATE INDEX IF NOT EXISTS idx_eipc_en_fts
    ON eipc_pairs USING gin(to_tsvector('simple', en_text));
CREATE INDEX IF NOT EXISTS idx_eipc_zu_fts
    ON eipc_pairs USING gin(to_tsvector('simple', zu_text));

-- ── Sample data (optional — delete if not needed) ─
INSERT INTO inc_documents (title, domain, year, region, word_count, token_count, status)
VALUES
  ('Ilanga Lase Natal — Vol. 84', 'News', 1984, 'KwaZulu-Natal', 42100, 54730, 'in_progress'),
  ('IFM Newsletter Vol. 3',        'News', 2001, 'KwaZulu-Natal', 12100, 15730, 'completed'),
  ('Izincwadi zeZulu Vol. 1',      'Literature', 1978, 'KwaZulu-Natal', 89400, 116220, 'completed'),
  ('UmAfrika — 1972 archive',      'News', 1972, 'KwaZulu-Natal', 8050, 10465, 'needs_review')
ON CONFLICT DO NOTHING;

INSERT INTO ioc_files (filename, region, speaker_gender, speaker_age_range, topic, duration_seconds, status)
VALUES
  ('Interview_KZN_045.wav', 'Eshowe', 'Female', '31-45', 'Life experience', 1471, 'in_progress'),
  ('Oral_PMB_012.wav', 'Pietermaritzburg', 'Male', '46-60', 'Rural life', 2468, 'completed'),
  ('Story_JHB_007.wav', 'Johannesburg', 'Female', '61+', 'Cultural traditions', 1135, 'completed')
ON CONFLICT DO NOTHING;
