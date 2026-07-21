# LINE AI Weekly Report

> Public beta (`v0.1`). A single-user, self-hosted macOS tool; not a hosted
> multi-tenant service.

Share papers, repositories, news, and social posts with a LINE bot. When you
request a report, a Mac pipeline resolves the links, creates a cited weekly
digest and optional podcast, then returns private mobile links through LINE.

## Requirements

- macOS and Python 3.10–3.13
- Node.js 20+ and a Cloudflare account with Workers, D1, and R2
- A LINE Messaging API channel
- Claude CLI, Codex CLI, or an Anthropic API key
- Optional `ffmpeg` for podcast processing

## Quick start

```bash
git clone <YOUR_REPOSITORY_URL>
cd line-ai-weekly-report
./scripts/bootstrap.sh
```

Then edit:

- `~/.config/weekly-report/config.yaml`
- `~/.config/weekly-report/secrets.env`

Deploy the Worker with `./collector/deploy.sh`, bind its `/webhook` URL in the
LINE console, run `./collector/get_push_id.sh`, then install the background
runner with `./launchd/install.sh`.

Validate locally before sending a real request:

```bash
.venv/bin/weekly-report doctor --offline
.venv/bin/weekly-report doctor --live
.venv/bin/weekly-report run --dry-run
```

The complete guide is maintained in Traditional Chinese in [README.md](README.md).
Integration endpoints are documented in [docs/API.md](docs/API.md), and the
data boundary in [docs/SECURITY_AND_PRIVACY.md](docs/SECURITY_AND_PRIVACY.md).

## License

MIT. Optional dependencies retain their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
