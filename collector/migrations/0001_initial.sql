CREATE TABLE IF NOT EXISTS links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  message_text TEXT,
  source_type TEXT,
  source_id TEXT,
  sender_id TEXT,
  webhook_event_id TEXT,
  line_timestamp INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_links_ts ON links (line_timestamp);

CREATE TABLE IF NOT EXISTS term_selections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week TEXT NOT NULL,
  raw_reply TEXT NOT NULL,
  sender_id TEXT,
  line_timestamp INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sel_week ON term_selections (week);

CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT
);
