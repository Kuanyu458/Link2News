# Security and privacy model

This project is a single-owner self-hosted tool. Each installation must use its
own LINE channel, Cloudflare Worker/D1/R2 resources, and provider credentials.
Do not expose it as a shared public service.

## Data flow

- LINE sends message text, URLs, sender IDs, and source IDs to the Worker.
- D1 stores accepted links only for IDs listed in `ALLOWED_SOURCE_IDS`.
- The Mac downloads current-week links and may send extracted content to the
  configured LLM provider.
- Generated PDF and audio files remain local and may be uploaded to a private
  R2 bucket. Signed URLs are bearer credentials until they expire.
- D1 link and term-selection records are deleted after 90 days by default.
  R2 objects use the deployment lifecycle setting. Local files remain until the
  owner deletes them.

## Owner responsibilities

- Keep `secrets.env`, local config, voice samples, downloaded papers, output,
  logs, and signed media URLs out of Git.
- Configure `ALLOWED_SOURCE_IDS` before enabling the LINE webhook.
- Tell group members that shared links may be downloaded, summarized, and sent
  to an external LLM provider.
- Respect source-site terms and copyright. Do not redistribute downloaded
  papers or generated archives without permission.
- Use only your own voice or a voice whose speaker has given informed consent.

## Supported security boundary

The Worker validates LINE signatures, authenticated API requests, source IDs,
artifact hashes, MIME types, sizes, and signed media URLs. It does not provide
user accounts, tenant isolation, quotas, or billing controls.

See `SECURITY.md` for private vulnerability reporting.
