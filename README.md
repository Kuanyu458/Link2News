# Link2News

把一週散落在 LINE 裡的論文、GitHub repository、新聞與社群連結，自動整理成可在手機、平板、網頁、甚至紙本印出閱讀的新聞式 PDF 與 Podcast。

[![CI](https://github.com/Kuanyu458/Link2News/actions/workflows/ci.yml/badge.svg)](https://github.com/Kuanyu458/Link2News/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10--3.13-blue)
![Platform](https://img.shields.io/badge/Platform-macOS-lightgrey)

[English](README.en.md) · [安裝與技術文件](docs/TECHNICAL.md) · [Adapter 契約](docs/ADAPTERS.md) · [公開 Demo](docs/DEMO.md) · [HTTP API](docs/API.md) · [安全與隱私](docs/SECURITY_AND_PRIVACY.md)

## 成果展示

### LINE 交付

<p align="center">
  <img src="docs/assets/line-weekly-report-demo.jpg" alt="Link2News 在 LINE 中交付週報 PDF、Podcast 與 Rich Menu 操作入口" width="420">
</p>
<p align="center"><sub>完成卡提供 PDF 與 Podcast，Rich Menu 可重新生成、切換模型或查看待處理清單。</sub></p>

### Podcast Demo

[🎧 播放／下載 Podcast 音檔範例（約 30 秒）](docs/assets/link2news-podcast-demo.mp3)

> Podcast Demo 使用合成文本與系統語音。repository 不納管生成週報、下載論文、使用者訊息、私有報告或憑證。

本週焦點、重點文獻與學術動向每篇文章都會附上對應原文圖表，圖說保留來源資訊。

## 它會做什麼

- **LINE 收件匣**：平日把論文、repository、新聞或社群貼文丟給 bot。
- **AI 編輯台**：自動解析來源、整理引用、選出焦點並產生繁體中文報導。
- **一份真正可讀的週報**：輸出自適應 HTML、A4 PDF、Markdown 報告與選配 Podcast。
- **手機直接交付**：透過私有 Cloudflare R2 簽章連結把 PDF 和音訊送回 LINE。
- **可串接**：以 Bearer token 呼叫 `/api/v1/links` 與 `/api/v1/jobs`，接上自己的書籤、bot 或知識管理工具。

## 使用方式

1. 平日把論文、GitHub repository、新聞或社群網址分享到已授權的 LINE 聊天室。
2. 從 Rich Menu 選擇生成週報、重新生成、指定模型或查看待處理清單。
3. Mac 背景服務整理來源、生成新聞式 PDF，並視設定產生 Podcast。
4. 完成後直接從 LINE 卡片閱讀 PDF、下載 Podcast，或播放收到的語音訊息。

也可以從終端機手動執行：

```bash
# 完整流程：收集 → 生成 → 發布
.venv/bin/weekly-report run

# 使用既有內容重新套用目前版型，不呼叫 LLM 或 TTS
.venv/bin/weekly-report rerender --week 2026-W28

# 不連外、不寫入產物的預檢
.venv/bin/weekly-report run --dry-run

# 不經 LINE：從文字檔讀取網址，產物留在 ./out/<週次>/
.venv/bin/weekly-report run --input links.txt --output ./out
```

## 完整自架與公開 Demo

完整自架版保留既有 LINE、Cloudflare、Mac runner、完整週報與原設定長度的
Podcast。聊天平台可透過公開的
[source／delivery adapter 契約](docs/ADAPTERS.md) 更換；核心專案只維護契約、
LINE 相容實作與測試。

`demo/` 則是完全獨立的 Hugging Face Docker Space 預覽：訪客不用提供 key，
可輸入最多 5 個公開網址，取得一頁 A4 PDF 與約 3 分鐘 Podcast。Demo 無 SLA、
會有冷啟動、每日免費額度與 60 分鐘保存限制；目前部署與隱私設定請看
[公開 Demo 文件](docs/DEMO.md)。

## 使用限制

- 目前是 `v0.1.0b1` 公開測試版，只支援 macOS 單機自架與一個受信任的 LINE 使用者或聊天室。
- 不是現成的多人 SaaS；公開 Worker API 前必須自行落實 Bearer token、權限與速率限制。
- 手機私密連結需要 Cloudflare R2；未啟用 R2 時仍可在本機生成，但無法從 LINE 直接開啟產物。
- 使用者需自行準備 LINE、Cloudflare 與 LLM 帳號，並承擔各服務的用量與費用。
- LLM 摘要、引用配對與語音內容仍可能出錯；對外發布、研究引用或決策使用前應人工覆核。
- 原文與圖表保留各自授權，不會因收錄於 MIT repository 而改為 MIT；請依 [第三方聲明](THIRD_PARTY_NOTICES.md) 使用。

## 安裝

目前需要一台長期登入的 Mac，以及你自己的 LINE Messaging API、Cloudflare Workers/D1/R2 和 Claude CLI、Codex CLI 或 Anthropic API。

```bash
git clone https://github.com/Kuanyu458/Link2News.git
cd Link2News
./scripts/bootstrap.sh
```

接著完成三件事：

1. 編輯 `~/.config/weekly-report/config.yaml` 與 `secrets.env`。
2. 從 LINE Developers 取得自己的 `U...` User ID，填入 `line.push_to`，再執行 `./collector/deploy.sh`。
3. 綁定 Worker `/webhook`、執行健檢，最後安裝背景 runner。

```bash
.venv/bin/weekly-report doctor --live
./launchd/install.sh
.venv/bin/python scripts/setup_richmenu.py
```

完整的帳號設定、部署順序與 macOS 背景服務說明請看 [安裝與技術文件](docs/TECHNICAL.md)。

## 文件

| 文件 | 用途 |
|---|---|
| [安裝與技術文件](docs/TECHNICAL.md) | 架構、資料流、部署、CLI、維運與疑難排解 |
| [HTTP API v1](docs/API.md) | 外部工具新增連結、建立與查詢工作 |
| [Adapter 契約](docs/ADAPTERS.md) | 替換聊天來源與交付平台、entry point 與錯誤語意 |
| [公開 Demo](docs/DEMO.md) | 一頁／三分鐘試用版、安全限制、Docker 與 Space 部署 |
| [安全與隱私](docs/SECURITY_AND_PRIVACY.md) | 信任邊界、資料保存、R2 簽章網址與責任範圍 |
| [貢獻指南](CONTRIBUTING.md) | 開發環境、測試與 pull request 規範 |
| [Security Policy](SECURITY.md) | 私下回報安全問題 |

## 開發

```bash
./scripts/bootstrap.sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
npm test
./scripts/check_public_tree.sh
```

Podcast Demo 可使用合成資料重建：

```bash
./scripts/generate_podcast_demo.sh
```

## 授權

[MIT](LICENSE)。選配工具與相依套件保留各自授權，詳見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
