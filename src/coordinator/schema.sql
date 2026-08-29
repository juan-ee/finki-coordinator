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
  status_days TEXT,                          -- JSON: ["mon","wed","fri"] (empty = every day)
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
  key   TEXT PRIMARY KEY,                  -- digest_time, digest_chat, nudge_limit
  value TEXT
);
