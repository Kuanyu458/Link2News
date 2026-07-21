#!/bin/bash
# Cloudflare collector 一鍵部署：在 `wrangler login` 之後執行此腳本即可。
# 自動完成：建 D1 → 填 database_id → 建表 → 設 secrets → 部署 → 回填 config.yaml
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"
SECRETS="$HOME/.config/weekly-report/secrets.env"
CONFIG="${WEEKLY_REPORT_CONFIG:-$HOME/.config/weekly-report/config.yaml}"
WRANGLER_CONFIG="wrangler.toml"
export WRANGLER_LOG_PATH="${WRANGLER_LOG_PATH:-../tmp/wrangler-logs}"
mkdir -p "$WRANGLER_LOG_PATH"

if [ ! -f "$CONFIG" ]; then
  mkdir -p "$(dirname "$CONFIG")"
  if [ -f ../pipeline/config.yaml ]; then
    cp ../pipeline/config.yaml "$CONFIG"
  else
    cp ../pipeline/config.example.yaml "$CONFIG"
  fi
  chmod 600 "$CONFIG"
fi
if [ ! -f "$WRANGLER_CONFIG" ]; then
  cp wrangler.example.toml "$WRANGLER_CONFIG"
fi
SOURCE_ID=$(grep 'push_to:' "$CONFIG" | sed 's/.*"\([^\"]*\)".*/\1/' | head -1)
if [[ "$SOURCE_ID" =~ ^[UCR][0-9a-f]{32}$ ]]; then
  if grep -q '^ALLOWED_SOURCE_IDS' "$WRANGLER_CONFIG"; then
    sed -i '' "s|^ALLOWED_SOURCE_IDS.*|ALLOWED_SOURCE_IDS = \"$SOURCE_ID\"|" "$WRANGLER_CONFIG"
  else
    printf '\n[vars]\nALLOWED_SOURCE_IDS = "%s"\nDATA_RETENTION_DAYS = "90"\n' \
      "$SOURCE_ID" >> "$WRANGLER_CONFIG"
  fi
else
  echo "❌ config.yaml 尚未設定有效的 line.push_to；公開版預設拒絕未授權來源。"
  echo "   請先設定 U/C/R 開頭的 LINE 來源 ID。"
  exit 1
fi
command -v npx >/dev/null || { echo "❌ 請先安裝 Node.js 與 npm"; exit 1; }
WRANGLER=(npx wrangler --config "$WRANGLER_CONFIG")
"${WRANGLER[@]}" whoami >/dev/null 2>&1 || { echo "❌ 尚未登入 Cloudflare，請先執行：npx wrangler login"; exit 1; }

# 1. D1 資料庫（已填過 database_id 就跳過）
if grep -q "REPLACE_WITH_YOUR_D1_DATABASE_ID" wrangler.toml; then
  echo "▶ 建立 D1 資料庫 weekly-report…"
  OUT=$("${WRANGLER[@]}" d1 create weekly-report 2>&1) || { echo "$OUT"; exit 1; }
  DB_ID=$(echo "$OUT" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
  [ -n "$DB_ID" ] || { echo "❌ 無法從輸出解析 database_id："; echo "$OUT"; exit 1; }
  sed -i '' "s/REPLACE_WITH_YOUR_D1_DATABASE_ID/$DB_ID/" wrangler.toml
  echo "  database_id = $DB_ID 已寫入 wrangler.toml"
else
  echo "▶ D1 database_id 已設定，跳過建立"
fi

# 2. 舊版資料庫兼容：在 migrations 接管前補上 webhook 去重欄位。
TABLE_INFO=$("${WRANGLER[@]}" d1 execute weekly-report --remote \
  --command "PRAGMA table_info(links)" --json 2>/dev/null || echo "[]")
if echo "$TABLE_INFO" | grep -Eq '"name"[[:space:]]*:[[:space:]]*"id"' && \
    ! echo "$TABLE_INFO" | grep -Eq '"name"[[:space:]]*:[[:space:]]*"webhook_event_id"'; then
  echo "▶ 升級舊版 links schema…"
  "${WRANGLER[@]}" d1 execute weekly-report --remote \
    --command "ALTER TABLE links ADD COLUMN webhook_event_id TEXT" >/dev/null
fi

# 3. 套用版本化 D1 migrations
echo "▶ 套用資料庫 migrations…"
"${WRANGLER[@]}" d1 migrations apply weekly-report --remote >/dev/null

# 4. 私有 R2 手機閱讀庫（帳號需先在 Dashboard 啟用 R2）
BUCKET="weekly-report-artifacts"
if ! R2_BUCKETS=$("${WRANGLER[@]}" r2 bucket list 2>&1); then
  echo "$R2_BUCKETS" | tail -12
  echo ""
  echo "❌ 尚未啟用 Cloudflare R2。"
  echo "   請到 Cloudflare Dashboard → R2 Object Storage 完成一次啟用，再重跑本腳本。"
  exit 1
fi
if echo "$R2_BUCKETS" | grep -q "$BUCKET"; then
  echo "▶ R2 bucket $BUCKET 已存在，跳過建立"
else
  echo "▶ 建立私有 R2 bucket：${BUCKET}…"
  "${WRANGLER[@]}" r2 bucket create "$BUCKET" >/dev/null
fi
LIFECYCLE=$("${WRANGLER[@]}" r2 bucket lifecycle list "$BUCKET" 2>/dev/null || echo "")
if echo "$LIFECYCLE" | grep -q "weekly-report-90-days"; then
  echo "▶ R2 90 天保留規則已存在，跳過"
else
  echo "▶ 設定 R2 產物 90 天後自動刪除…"
  "${WRANGLER[@]}" r2 bucket lifecycle add "$BUCKET" weekly-report-90-days reports/ \
    --expire-days 90 -y >/dev/null
fi

# 5. Secrets（已設定過的跳過，重跑不會重複詢問）
EXISTING=$("${WRANGLER[@]}" secret list 2>/dev/null || echo "")
if echo "$EXISTING" | grep -q '"API_SECRET"'; then
  echo "▶ API_SECRET 已設定，跳過"
else
  API_SECRET=$(grep '^COLLECTOR_API_SECRET=' "$SECRETS" | cut -d= -f2-)
  [ -n "$API_SECRET" ] || { echo "❌ $SECRETS 中沒有 COLLECTOR_API_SECRET"; exit 1; }
  echo "▶ 設定 API_SECRET（取自 secrets.env）…"
  printf '%s' "$API_SECRET" | "${WRANGLER[@]}" secret put API_SECRET >/dev/null
fi
if echo "$EXISTING" | grep -q '"LINE_CHANNEL_SECRET"'; then
  echo "▶ LINE_CHANNEL_SECRET 已設定，跳過"
else
  echo "▶ 請貼上 LINE Channel secret（LINE Developers → Basic settings 頁，32 位字串）："
  read -r CHANNEL_SECRET
  printf '%s' "$CHANNEL_SECRET" | "${WRANGLER[@]}" secret put LINE_CHANNEL_SECRET >/dev/null
fi
if echo "$EXISTING" | grep -q '"LINE_CHANNEL_ACCESS_TOKEN"'; then
  echo "▶ LINE_CHANNEL_ACCESS_TOKEN 已設定，跳過"
else
  LINE_TOKEN=$(grep '^LINE_CHANNEL_ACCESS_TOKEN=' "$SECRETS" | cut -d= -f2-)
  [ -n "$LINE_TOKEN" ] || { echo "❌ $SECRETS 中沒有 LINE_CHANNEL_ACCESS_TOKEN"; exit 1; }
  printf '%s' "$LINE_TOKEN" | "${WRANGLER[@]}" secret put LINE_CHANNEL_ACCESS_TOKEN >/dev/null
fi
if echo "$EXISTING" | grep -q '"ARTIFACT_SIGNING_SECRET"'; then
  echo "▶ ARTIFACT_SIGNING_SECRET 已設定，跳過"
else
  echo "▶ 產生手機閱讀連結簽章密鑰…"
  SIGNING_SECRET=$(openssl rand -hex 32)
  printf '%s' "$SIGNING_SECRET" | "${WRANGLER[@]}" secret put ARTIFACT_SIGNING_SECRET >/dev/null
fi

# 6. 部署並取得網址
echo "▶ 部署 Worker…"
if ! DEPLOY_OUT=$("${WRANGLER[@]}" deploy 2>&1); then
  echo "$DEPLOY_OUT" | tail -15
  echo ""
  if echo "$DEPLOY_OUT" | grep -q "workers.dev subdomain"; then
    echo "❌ 部署失敗：帳號還沒註冊 workers.dev 子網域。"
    echo "   請直接在終端機執行 npx wrangler deploy（互動模式），回答 yes 並選一個子網域名稱，"
    echo "   完成後重跑本腳本即可。"
  else
    echo "❌ 部署失敗，錯誤訊息如上。"
  fi
  exit 1
fi
echo "$DEPLOY_OUT" | tail -5
URL=$(echo "$DEPLOY_OUT" | grep -oE 'https://[a-z0-9.-]+\.workers\.dev' | head -1)
[ -n "$URL" ] || { echo "⚠️ 無法解析部署網址，請手動填入 $CONFIG 的 base_url"; exit 1; }

# 7. 回填 config.yaml
sed -i '' "s|base_url:.*|base_url: \"$URL\"|" "$CONFIG"
echo ""
echo "✅ 部署完成！base_url 已寫入 config.yaml"
echo ""
echo "最後兩步（需要在 LINE 上操作）："
echo "1. LINE Developers → Messaging API → Webhook URL 填：$URL/webhook"
echo "   按 Verify 應顯示 Success，並開啟 Use webhook"
echo "2. 往聊天室丟一則含網址的訊息，然後執行 ./get_push_id.sh 取得推送目標 ID"
