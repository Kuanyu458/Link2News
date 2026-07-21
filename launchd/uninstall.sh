#!/bin/bash
# Disable the LaunchAgent. Runtime data is preserved unless --purge-runtime is
# explicitly supplied.
set -euo pipefail

AGENT="$HOME/Library/LaunchAgents/com.weekly.trigger.plist"
RUNTIME="$HOME/Library/Application Support/WeeklyReport"

launchctl bootout "gui/$UID/com.weekly.trigger" 2>/dev/null || \
  launchctl unload "$AGENT" 2>/dev/null || true
if [ -f "$AGENT" ]; then
  rm "$AGENT"
fi

if [ "${1:-}" = "--purge-runtime" ]; then
  case "$RUNTIME" in
    "$HOME/Library/Application Support/WeeklyReport") rm -rf "$RUNTIME" ;;
    *) echo "refusing to remove unexpected runtime path: $RUNTIME"; exit 1 ;;
  esac
  echo "LaunchAgent and runtime removed"
else
  echo "LaunchAgent removed; runtime preserved at $RUNTIME"
fi
