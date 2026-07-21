#!/bin/bash
# Prepare a clean local environment without deploying cloud resources or
# sending external messages.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/weekly-report"
CONFIG="${WEEKLY_REPORT_CONFIG:-$CONFIG_DIR/config.yaml}"
SECRETS="$CONFIG_DIR/secrets.env"

mkdir -p "$CONFIG_DIR" "$ROOT/data"
if [ ! -f "$CONFIG" ]; then
  if [ -f "$ROOT/pipeline/config.yaml" ]; then
    cp "$ROOT/pipeline/config.yaml" "$CONFIG"
  else
    cp "$ROOT/pipeline/config.example.yaml" "$CONFIG"
  fi
  chmod 600 "$CONFIG"
  echo "created $CONFIG"
fi
if [ ! -f "$SECRETS" ]; then
  cp "$ROOT/secrets.env.example" "$SECRETS"
  chmod 600 "$SECRETS"
  echo "created $SECRETS"
fi
if [ ! -f "$ROOT/collector/wrangler.toml" ]; then
  cp "$ROOT/collector/wrangler.example.toml" "$ROOT/collector/wrangler.toml"
fi
if [ ! -f "$ROOT/data/known_terms.json" ]; then
  cp "$ROOT/data/known_terms.example.json" "$ROOT/data/known_terms.json"
fi

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  python3 -m venv "$ROOT/.venv"
fi
"$ROOT/.venv/bin/python" -m pip install --require-hashes -r "$ROOT/requirements.lock"
"$ROOT/.venv/bin/python" -m pip install --no-deps -e "$ROOT"
"$ROOT/.venv/bin/python" -m playwright install chromium

cd "$ROOT"
npm install --ignore-scripts

echo "bootstrap complete"
echo "next: edit $CONFIG and $SECRETS, then run .venv/bin/weekly-report --help"
