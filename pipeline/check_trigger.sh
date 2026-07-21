#!/bin/bash
# Every three minutes launchd starts this wrapper. The Python runner acquires
# the local lock before claiming a cloud job, so a job cannot be consumed by a
# machine that is already busy.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/pipeline/runner.py"
