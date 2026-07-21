-- D1 schema for the weekly-report link collector
CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  message_text TEXT,
  source_type TEXT,          -- 'group' | 'room' | 'user'
  source_id TEXT,            -- LINE group/room/user id the message came from
  sender_id TEXT,
  webhook_event_id TEXT,    -- LINE webhookEventId or api:<external_id>
  line_timestamp INTEGER,    -- ms since epoch, from LINE event
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_links_ts ON links (line_timestamp);
CREATE INDEX IF NOT EXISTS idx_links_source_ts ON links (source_id, line_timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_links_event_url
  ON links (webhook_event_id, url) WHERE webhook_event_id IS NOT NULL;

-- Numeric replies during the term-confirmation window, e.g. "1 3 7"
CREATE TABLE IF NOT EXISTS term_selections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week TEXT NOT NULL,        -- ISO week key, e.g. '2026-W28'
  raw_reply TEXT NOT NULL,
  sender_id TEXT,
  line_timestamp INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sel_week ON term_selections (week);

-- Single-row state: which week is currently awaiting a term selection reply
CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT DEFAULT (datetime('now'))
);
