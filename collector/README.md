# Collector 部署步驟（一次性）

前置：Cloudflare 免費帳號、Node.js、LINE Developers 帳號。

## 1. LINE Bot 建立
1. 到 https://developers.line.biz/console/ 建立 Provider → 建立 **Messaging API** channel。
2. 記下 **Channel secret**（Basic settings 頁）與 **Channel access token**（Messaging API 頁，點 Issue）。
3. Messaging API 頁設定：
   - 「Allow bot to join group chats」→ 啟用
   - LINE Official Account Manager 中：回應設定 → 關閉「自動回應訊息」、開啟「Webhook」。
4. 用 QR code 把 bot 加為好友，並邀請進你分享連結的聊天室/群組。

## 2. 部署 Worker
```bash
cd collector
npm ci
npx wrangler login
./deploy.sh
```

## 3. 綁 Webhook
LINE console → Messaging API → Webhook URL 填：
```
https://weekly-report-collector.<你>.workers.dev/webhook
```
按 **Verify** 應顯示 Success，並開啟「Use webhook」。

## 4. 驗證
往聊天室丟一個含網址的訊息，然後：
```bash
curl -H "Authorization: Bearer <你的API_SECRET>" \
  "https://weekly-report-collector.<你>.workers.dev/api/v1/links?since=0"
```
應回傳剛剛的連結 JSON。

## 5. 取得群組 ID（供 pipeline 推送訊息用）
上一步回傳 JSON 中的 `source_id` 就是群組/聊天室 ID，填進 `~/.config/weekly-report/config.yaml` 的 `line.push_to`。
