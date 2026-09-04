-- SQLite DDL for hermes-coord.db — the three tables below are verbatim from
-- proposal.md §1 "SQLite schema (hermes-coord.db)" (source of truth; do not edit here).
-- Applied as migration 001 by coordinator.db.migrate().

CREATE TABLE members (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  telegram_id INTEGER UNIQUE,
  timezone    TEXT NOT NULL DEFAULT 'UTC',   -- IANA name, e.g. 'America/Guayaquil'
  wake        TEXT,                          -- 'HH:MM' in member's local time
  role        TEXT,
  active      INTEGER DEFAULT 1,
  created_at  TEXT, updated_at TEXT
);
CREATE TABLE checkins (
  id         INTEGER PRIMARY KEY,
  member_id  INTEGER REFERENCES members(id),
  date       TEXT NOT NULL,                 -- 'YYYY-MM-DD'
  done       TEXT, next TEXT, blockers TEXT,
  source     TEXT DEFAULT 'auto',           -- auto | manual
  created_at TEXT,
  UNIQUE(member_id, date)                  -- latest wins: one check-in per member per day
);
CREATE TABLE settings (                    -- runtime knobs (key → value, TEXT)
  key   TEXT PRIMARY KEY,                  -- digest_chat, nudge_limit
  value TEXT
);
-- Knowledge cache (v6, D2): rebuildable from Drive at any time — the index, not the
-- record. Per-file reindex = DELETE + reINSERT, idempotent via UNIQUE(file_id, heading).
CREATE TABLE knowledge (
  chunk_id       INTEGER PRIMARY KEY,
  file_id        TEXT NOT NULL,   -- Drive file id (stable across renames)
  path           TEXT NOT NULL,   -- logical path within the Drive root
  title          TEXT NOT NULL,
  heading        TEXT,            -- section heading (chunk label; NULL = preamble)
  body           TEXT NOT NULL,
  modified_time  TEXT NOT NULL,   -- Drive modifiedTime — the sync watermark source
  fetched_at     TEXT NOT NULL,
  UNIQUE(file_id, heading)
);
CREATE INDEX knowledge_file ON knowledge(file_id);
-- External-content FTS5 over the cache rows: one text store, no duplication, no
-- triggers (the sync owns writes). unicode61 + remove_diacritics 2 makes matching
-- accent-insensitive: MATCH 'decision' finds "decisión".
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
  title, body,
  content='knowledge',
  content_rowid='chunk_id',
  tokenize='unicode61 remove_diacritics 2'
);
