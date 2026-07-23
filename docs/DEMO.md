# 公開 Demo：一頁週報與三分鐘 Podcast

`demo/` 是與既有 LINE／D1／R2／Mac runner 完全分離的公開預覽。它只接受
1–5 個不需登入的公開 HTTP/HTTPS 網址，輸出單頁 HTML、正好一頁的 A4 PDF
及 170–190 秒 MP3。它不執行 GitHub star、完整策展、文獻庫或 LINE 交付。

免費 Hugging Face CPU Space 閒置後會休眠，冷啟動需要等待；Demo 無 SLA，
免費政策或模型可用性改變時會直接暫停，不切換付費方案。

## 模型與資料告知

- 文字：`gemini-3.5-flash-lite`
- 語音：`gemini-3.1-flash-tts-preview`
- Provider：Google Gemini Developer API（API key 通常由 Google AI Studio 建立）

模型名稱集中在 `DEMO_TEXT_MODEL`、`DEMO_TTS_MODEL`。每個工作最多一次文字
呼叫與一次 TTS；429 依 `Retry-After` fail closed，5xx 最多一次短退避重試。
專用 Google project 不得連結付費 billing。Gemini 免費層送出的內容可能被用於
改善 Google 產品，因此 UI 在送出前要求明確同意。

## 安全與隱私

- 每次 redirect 都重新解析 DNS，拒絕 localhost、私有／保留 IP、metadata
  endpoint、帳密 URL 與非 HTTP scheme。
- 最多 3 次 redirect；連線 5 秒、讀取 20 秒、抓取階段 60 秒。
- HTML 最多 2 MB、PDF 15 MB、每來源 12,000 字、總 prompt 40,000 字。
- 遵守 `robots.txt`；不帶 cookie、不登入、不接受私人檔案。
- 來源內容一律視為不可信資料；prompt injection 不會成為模型指令。
- Jinja autoescape；不嵌入第三方圖表。
- `concurrency_limit=1`、queue 上限 5、每 IP 每日 2 次、全站每日 20 次。
- Turnstile token 須由伺服器驗證且不可重播。
- UUID 工作目錄在 60 分鐘後清除；不使用 R2 或永久磁碟。
- 日誌只記錄 job ID、來源網域、階段、耗時與錯誤類別，不記完整 URL、內文或 key。

SQLite 配額位於暫存磁碟，Space restart 後會歸零；它是零成本 abuse guard，不是
計費或帳號系統。

## 環境變數與 Space secrets

| 名稱 | 類型 | 用途 |
|---|---|---|
| `GEMINI_API_KEY` | Secret | 未連結 billing 的專用 Gemini Free Tier key |
| `TURNSTILE_SITE_KEY` | Variable | 公開 widget site key |
| `TURNSTILE_VERIFY_URL` | Variable | Turnstile Spin 受管驗證 Worker URL |
| `DEMO_RATE_SALT` | Secret | 將來源 IP 雜湊後寫入配額資料庫 |
| `DEMO_TEXT_MODEL` | Variable | 選填文字模型覆寫 |
| `DEMO_TTS_MODEL` | Variable | 選填 TTS 模型覆寫 |

`/healthz` 只顯示版本、文字／TTS／Turnstile 是否已設定及 queue 狀態，不回傳
任何 secret。

## 本機 Docker

```bash
docker build -f demo/Dockerfile -t link2news-demo .
docker run --rm -p 7860:7860 \
  -e GEMINI_API_KEY \
  -e TURNSTILE_SITE_KEY \
  -e TURNSTILE_VERIFY_URL \
  -e DEMO_RATE_SALT \
  link2news-demo
```

開啟 `http://127.0.0.1:7860`；停止容器即停止服務。測試環境可設定
`DEMO_ALLOW_TEST_TURNSTILE=1` 並只使用 token `test-pass`，正式部署不得設定。

## Hugging Face Space 同步

GitHub workflow 只同步建置所需的 `demo/`、`pipeline/`、`pyproject.toml`、
`README.md` 與授權文件，不同步產物或 secrets。Repository secrets：

- `HF_TOKEN`：只有目標 Space 寫入權限。
- `HF_SPACE_REPO`：例如 `owner/link2news-demo`。

workflow_dispatch 可手動部署；push 只有在 `demo/**` 或相關 pipeline 檔案變更
時觸發。Pause 或刪除 Space 不影響現有 LINE 系統。

Turnstile widget、驗證 Worker、Hugging Face Space 與 secrets 都是外部資源；
第一次建立時應依部署精靈逐步確認，不可把 secret 寫入 repository。
