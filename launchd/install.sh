#!/bin/bash
# 安裝（或更新）週報背景 runtime 與 launchd 排程。
# runtime 刻意不放在 Desktop/Documents，避免 macOS TCC 阻擋無人值守的 LaunchAgent。
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_ROOT="$(cd "$DIR/.." && pwd)"
RUNTIME="$HOME/Library/Application Support/WeeklyReport"
AGENTS="$HOME/Library/LaunchAgents"
REQUIREMENTS="$SOURCE_ROOT/requirements.lock"
mkdir -p "$AGENTS"

echo "▶ 同步背景 runtime：$RUNTIME"
mkdir -p "$RUNTIME" "$RUNTIME/output/logs" "$RUNTIME/data" "$RUNTIME/文獻庫"

# 程式碼與模板每次同步；產出、文獻庫、已解釋術語則保留 runtime 版本。
for component in pipeline templates; do
  mkdir -p "$RUNTIME/$component"
  COPYFILE_DISABLE=1 rsync -a --delete --exclude '.DS_Store' --exclude 'config.yaml' \
    "$SOURCE_ROOT/$component/" "$RUNTIME/$component/"
done
if [ ! -f "$RUNTIME/data/known_terms.json" ]; then
  cp "$SOURCE_ROOT/data/known_terms.json" "$RUNTIME/data/known_terms.json"
fi
echo "▶ 檢查背景 Python 執行環境"
[ -f "$REQUIREMENTS" ] || { echo "❌ 找不到 requirements.lock，請先執行 ./scripts/bootstrap.sh"; exit 1; }
LOCK_HASH=$(shasum -a 256 "$REQUIREMENTS" | awk '{print $1}')
INSTALLED_HASH=$(cat "$RUNTIME/.requirements.sha256" 2>/dev/null || true)
if [ ! -x "$RUNTIME/.venv/bin/python" ] || [ "$LOCK_HASH" != "$INSTALLED_HASH" ]; then
  rm -rf "$RUNTIME/.venv.new"
  python3 -m venv "$RUNTIME/.venv.new"
  "$RUNTIME/.venv.new/bin/python" -m pip install --require-hashes -r "$REQUIREMENTS"
  rm -rf "$RUNTIME/.venv"
  mv "$RUNTIME/.venv.new" "$RUNTIME/.venv"
  printf '%s\n' "$LOCK_HASH" > "$RUNTIME/.requirements.sha256"
fi

# 在專案中留可見捷徑，Finder 仍可直接開啟背景 runtime 的產出。
ln -sfn "$RUNTIME/output" "$SOURCE_ROOT/背景執行產出"
ln -sfn "$RUNTIME/文獻庫" "$SOURCE_ROOT/背景執行文獻庫"

# 卸載並移除舊版排程（v1 兩段式、v2/v3 週日定時）
for old in com.weekly.stage1 com.weekly.stage2 com.weekly.run; do
  if [ -f "$AGENTS/$old.plist" ]; then
    launchctl unload "$AGENTS/$old.plist" 2>/dev/null || true
    rm "$AGENTS/$old.plist"
    echo "removed legacy $old"
  fi
done

RUNTIME_XML=$(printf '%s' "$RUNTIME" | sed 's/&/\\\&amp;/g')
PATH_PARTS="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
for tool in claude codex ffmpeg; do
  if TOOL_PATH=$(command -v "$tool" 2>/dev/null); then
    TOOL_DIR=$(dirname "$TOOL_PATH")
    case ":$PATH_PARTS:" in *":$TOOL_DIR:"*) ;; *) PATH_PARTS="$TOOL_DIR:$PATH_PARTS" ;; esac
  fi
done
PATH_XML=$(printf '%s' "$PATH_PARTS" | sed 's/&/\\\&amp;/g')
sed -e "s|__WEEKLY_REPORT_RUNTIME__|$RUNTIME_XML|g" \
    -e "s|__WEEKLY_REPORT_PATH__|$PATH_XML|g" \
  "$DIR/com.weekly.trigger.plist" > "$AGENTS/com.weekly.trigger.plist"
plutil -lint "$AGENTS/com.weekly.trigger.plist" >/dev/null

launchctl bootout "gui/$UID/com.weekly.trigger" 2>/dev/null || \
  launchctl unload "$AGENTS/com.weekly.trigger.plist" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$AGENTS/com.weekly.trigger.plist"
launchctl enable "gui/$UID/com.weekly.trigger"
launchctl kickstart -k "gui/$UID/com.weekly.trigger"
echo "loaded com.weekly.trigger（每 3 分鐘檢查 LINE 觸發工作）"
echo "runtime：$RUNTIME"
echo "使用方式：在 LINE 對機器人（或群組）輸入「生成週報」即開始製作"
