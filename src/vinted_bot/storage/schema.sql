-- Schema Version 1. Änderungen später nur über neue Migrationen in db.py.

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS filters (
  id               INTEGER PRIMARY KEY,
  name             TEXT    NOT NULL,
  source_url       TEXT    NOT NULL,              -- so wie eingefügt
  canonical_url    TEXT    NOT NULL,              -- normalisiert
  domain           TEXT    NOT NULL,              -- "at", "de"
  api_params_json  TEXT    NOT NULL,              -- gemappte API-Parameter
  channel_id       INTEGER NOT NULL,              -- Discord-Kanal
  -- "AT,DE"; leer = alle Länder. NOT NULL, weil NULL in UNIQUE-Constraints nie gleich ist
  countries        TEXT    NOT NULL DEFAULT '',
  interval_s       INTEGER NOT NULL DEFAULT 60 CHECK (interval_s >= 30),
  paused           INTEGER NOT NULL DEFAULT 0,
  baseline_done    INTEGER NOT NULL DEFAULT 0,    -- 0 = nächster Lauf setzt nur die Baseline
  max_seen_id      INTEGER,                       -- ID-Wasserstand (siehe core/dedupe.py)
  created_at       TEXT    NOT NULL,
  last_success_at  TEXT,
  last_error       TEXT,
  fail_count       INTEGER NOT NULL DEFAULT 0,
  -- Derselbe Filter darf in verschiedenen Kanälen laufen, aber nicht doppelt im selben
  UNIQUE (canonical_url, countries, channel_id)
);

-- Gesehene Artikel + Outbox in einem: posted_at NULL und status 'pending' = noch zu senden
CREATE TABLE IF NOT EXISTS seen_items (
  filter_id     INTEGER NOT NULL REFERENCES filters(id) ON DELETE CASCADE,
  item_id       INTEGER NOT NULL,
  first_seen_at TEXT    NOT NULL,
  -- 'baseline' | 'pending' | 'posted' | 'skipped' (Länderfilter, Wasserstand, Limit) | 'failed'
  status        TEXT    NOT NULL,
  payload_json  TEXT,                             -- nur solange 'pending'
  posted_at     TEXT,
  attempts      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (filter_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_outbox ON seen_items(status, first_seen_at);

-- Verkäufer-Cache: nur, was das Embed braucht (Datensparsamkeit)
CREATE TABLE IF NOT EXISTS sellers (
  user_id             INTEGER PRIMARY KEY,
  country_iso         TEXT,
  feedback_reputation REAL,
  feedback_count      INTEGER,
  photo_url           TEXT,
  fetched_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
