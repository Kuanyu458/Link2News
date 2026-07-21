#!/bin/bash
# 部署完成、往聊天室丟過一則含網址的訊息後執行：
# 從 collector 讀出訊息來源 ID，寫入 pipeline/config.yaml 的 line.push_to
set -euo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"
SECRETS="$HOME/.config/weekly-report/secrets.env"
CONFIG="${WEEKLY_REPORT_CONFIG:-$HOME/.config/weekly-report/config.yaml}"
[ -f "$CONFIG" ] || CONFIG="../pipeline/config.yaml"  # legacy private install

API_SECRET=$(grep '^COLLECTOR_API_SECRET=' "$SECRETS" | cut -d= -f2-)
URL=$(grep 'base_url:' "$CONFIG" | sed 's/.*"\(https[^"]*\)".*/\1/')
if [[ "$URL" != *workers.dev* ]] || [[ "$URL" == *YOURNAME* ]]; then
  echo "❌ config.yaml 的 base_url 尚未設定（先跑 deploy.sh 完成部署）"; exit 1
fi

RESP=$(curl -s -H "X-Api-Secret: $API_SECRET" "$URL/links?since=0")
if [ "$RESP" = "forbidden" ]; then
  echo "❌ 密鑰不符（403）：Worker 上的 API_SECRET 與 secrets.env 的 COLLECTOR_API_SECRET 不一致"; exit 1
fi
if [ "$RESP" = "[]" ] || [ -z "$RESP" ]; then
  echo "❌ collector 還沒收到任何連結。請確認："
  echo "   1. Webhook URL 已在 LINE console 綁定且 Verify 成功"
  echo "   2. Bot 已被邀進聊天室"
  echo "   3. 已往聊天室傳過一則「含網址」的訊息"
  exit 1
fi

SOURCE_ID=$(echo "$RESP" | python3 -c "import sys, json; rows=json.load(sys.stdin); print(rows[-1]['source_id'])")
echo "最新訊息的來源 ID：$SOURCE_ID"
sed -i '' "s/push_to:.*/push_to: \"$SOURCE_ID\"            # 自動填入/" "$CONFIG"
echo "✅ 已寫入 config.yaml 的 line.push_to"
echo ""
echo "全部設定完成後，建議跑健檢：../.venv/bin/python ../pipeline/doctor.py"
