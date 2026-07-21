# Contributing

Thanks for improving LINE AI Weekly Report. This is a macOS-first, single-user
self-hosted project. Keep changes inside that boundary unless an issue has
explicitly accepted a larger design.

## Development setup

```bash
./scripts/bootstrap.sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
npm test
bash scripts/check_public_tree.sh
```

Never use production LINE, Cloudflare, LLM, or voice credentials in tests.
Fixtures must be synthetic or redistributable. Add tests for behavior changes,
run `git diff --check`, and update the relevant user/API documentation.

## Pull requests

- Keep one coherent change per PR.
- Explain user impact, migration, verification, and rollback.
- Call out schema, API, security, provider-cost, or data-retention changes.
- Do not commit generated reports, papers, logs, configuration, or secrets.

By contributing, you agree that your contribution is licensed under MIT.
