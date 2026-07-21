CREATE INDEX IF NOT EXISTS idx_links_source_ts ON links (source_id, line_timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_links_event_url
  ON links (webhook_event_id, url) WHERE webhook_event_id IS NOT NULL;
