#!/bin/bash
set -euo pipefail

git rev-parse --is-inside-work-tree >/dev/null
for path in .venv output tmp 文獻庫 pipeline/config.yaml collector/wrangler.toml \
  data/known_terms.json 背景執行產出 背景執行文獻庫; do
  if git ls-files --error-unmatch "$path" >/dev/null 2>&1 || \
      git ls-files "$path/" | grep -q .; then
    echo "forbidden tracked path: $path" >&2
    exit 1
  fi
done

if git grep -nE '/Users/[^/]+' -- \
    ':!scripts/check_public_tree.sh'; then
  echo "personal path or deployment identifier found" >&2
  exit 1
fi

if git grep -nE '(gh[pousr]_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'; then
  echo "possible credential found" >&2
  exit 1
fi

echo "public tree check: ok"
