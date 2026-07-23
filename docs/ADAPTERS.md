# Link2News Adapter 契約

Adapter 讓第三方聊天平台接入 Link2News，但不要求核心專案維護 Telegram、
Discord 或 Slack bot。未設定 adapter 時仍使用既有 `collector + line`，LINE
指令、D1 狀態、R2 key、輸出檔名、完整週報與 15 分鐘 Podcast 均不改變。

## 契約

公開型別位於 `pipeline.adapters`：

- `SourceLink`：網址、伴隨文字、平台 external ID、發生時間與來源 ID。
- `CollectRequest`：週次、時間範圍、數量上限與執行模式。
- `Artifact`：種類、路徑、MIME、SHA-256 與選填音訊長度。
- `DeliveryEvent`：`queued`、`running`、`completed`、`failed` 狀態及進度、摘要、產物。
- `DeliveryReceipt`：交付是否成功、各產物結果與平台端 ID。

```python
from typing import Protocol

class SourceAdapter(Protocol):
    def collect(self, request: CollectRequest) -> list[SourceLink]: ...

class DeliveryAdapter(Protocol):
    def publish(self, event: DeliveryEvent) -> DeliveryReceipt: ...
```

`collect()` 只收集輸入，不應生成內容；`publish()` 只交付事件與產物，不應改寫
報告。內容已成功生成但交付失敗時，工作仍記錄為「生成完成、交付失敗」。

## 內建 adapter 與設定

| 類型 | 名稱 | 行為 |
|---|---|---|
| source | `collector` | 使用既有 Worker／D1 來源 |
| source | `file` | 從純文字檔或 stdin 讀取 URL |
| delivery | `line` | 使用既有 R2、LINE Flex、文字與音訊交付 |
| delivery | `local` | 產物只留在輸出資料夾 |

設定檔可選填：

```yaml
source:
  adapter: collector
delivery:
  adapter: line
```

本機檔案流程：

```bash
weekly-report run --input links.txt --output ./out
cat links.txt | weekly-report run --input - --output ./out
```

只要出現 `--input` 而未指定其他 adapter，預設切成 `file + local`。`--output`
是輸出根目錄，產物仍置於該目錄下的 ISO 週次子目錄。

## 第三方 entry point

套件透過 Python entry points 註冊：

```toml
[project.entry-points."link2news.sources"]
my-chat = "my_link2news:ChatSource"

[project.entry-points."link2news.deliveries"]
my-chat = "my_link2news:ChatDelivery"
```

工廠或 class 會收到 `cfg`、`secrets`、`options` keyword arguments。最小 fake：

```python
from pipeline.adapters import DeliveryReceipt, SourceLink

class FakeSource:
    def __init__(self, **_):
        pass

    def collect(self, request):
        return [SourceLink(
            url="https://example.org/article",
            external_id="message-123",
            source_id="room-456",
        )]

class FakeDelivery:
    def __init__(self, **_):
        pass

    def publish(self, event):
        return DeliveryReceipt(
            ok=True,
            items={item.kind: "platform-file-id" for item in event.artifacts},
            platform_id="message-789",
        )
```

使用：

```bash
weekly-report run --source-adapter my-chat --delivery-adapter my-chat
```

不存在、載入失敗或未實作必要方法時，錯誤會包含 entry-point group、adapter
名稱、套件目標與原始錯誤；系統不會靜默退回 LINE。

## 平台 bot 與 HTTP API 的映射

不需要在 Python pipeline 內實作 bot。第三方平台可直接使用
[HTTP API v1](API.md)：

| 平台行為 | Link2News API |
|---|---|
| 使用者貼上公開連結 | `POST /api/v1/links` |
| 使用者要求生成 | `POST /api/v1/jobs` |
| 查詢目前／歷史工作 | `GET /api/v1/jobs` |
| 輪詢單一工作與進度 | `GET /api/v1/jobs/:id` |

平台端應保存自己的 message／channel ID 作為 external ID，將 Link2News job ID
映射回原訊息。Bearer token、使用者授權、速率限制、重送與平台簽章驗證由第三方
adapter 或 bot 負責。

## 生命週期與錯誤語意

1. source adapter 收到 `CollectRequest`，回傳已去重的 `SourceLink`。
2. 核心 pipeline 解析、擷取及生成完整內容。
3. delivery adapter 收到完成事件與本機產物。
4. `DeliveryReceipt.ok=False` 表示交付失敗，不等同內容生成失敗。

同一來源的 `external_id` 應穩定；第三方 source adapter 應自行去重。Adapter
不可吞掉例外或自行改用 LINE。移除設定中的新 adapter 欄位即可回到
`collector + line`。
