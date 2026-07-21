# HTTP API v1

The Collector exposes a small authenticated API for personal integrations.
Except for `/healthz` and the LINE webhook, send the local
`COLLECTOR_API_SECRET` as a Bearer token:

```http
Authorization: Bearer <COLLECTOR_API_SECRET>
```

The API belongs to one self-hosted owner. It is not a multi-tenant service.
Items submitted through the API are assigned to the configured default LINE
source so they appear in the same weekly report.

## Health

```bash
curl https://YOUR_WORKER.workers.dev/healthz
```

Returns the service name and API version without configuration or user data.

## Add links

`POST /api/v1/links` accepts at most 100 items. Reusing `external_id` with the
same URL is idempotent.

```bash
curl -X POST https://YOUR_WORKER.workers.dev/api/v1/links \
  -H "Authorization: Bearer $COLLECTOR_API_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"items":[{"url":"https://example.com/article","text":"optional context","external_id":"rss:123"}]}'
```

Response: `202 {"ok":true,"accepted":1,"source_id":"..."}`.

`GET /api/v1/links?since=<epoch-ms>&source_id=<id>` returns links for one
configured source. The legacy `/links` alias remains available during v0.1.

## Create and inspect jobs

```bash
curl -X POST https://YOUR_WORKER.workers.dev/api/v1/jobs \
  -H "Authorization: Bearer $COLLECTOR_API_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"all"}'

curl https://YOUR_WORKER.workers.dev/api/v1/jobs/JOB_ID \
  -H "Authorization: Bearer $COLLECTOR_API_SECRET"
```

`mode` is `all` or `regenerate`; regeneration also accepts `week`. Only one
job may be active in a self-hosted instance. A second request returns `409`.

## Runner-only endpoint

`POST /api/v1/jobs/claim` is used by the macOS runner. A claim has a five-minute
lease and does not acknowledge the pending request until the pipeline sends its
first running heartbeat. This lets another poll recover a job if startup fails.

## Errors

- `400`: invalid request or missing source configuration.
- `403`: missing or incorrect Bearer token.
- `404`: job not found.
- `409`: another job is active or the job ID does not match.
- `5xx`: storage or provider failure; retry only idempotent requests.
