"""Render a privacy-safe Link2News README preview from synthetic content."""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets"
HTML_PATH = ROOT / "tmp" / "readme-preview.html"
PNG_PATH = ASSET_DIR / "link2news-preview.png"


def main() -> None:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True)
    template = env.get_template("newspaper.html")
    focus = [
        {
            "kicker": "本週主題",
            "headline": "小模型開始接手真正的工具任務",
            "paragraphs": [
                "本週的共同訊號不是模型又變大，而是更小、更可控的模型開始可靠地呼叫工具、整理資料並留下可查核的結果。",
                "對開發團隊而言，重點正從單次回答品質移向完整工作流：權限、重試、觀測與人工覆核缺一不可。",
            ],
            "refs": [1, 2],
        },
        {
            "kicker": "開源觀察",
            "headline": "代理框架把可追蹤性放到第一順位",
            "paragraphs": ["新的開源工具讓每一次模型決策、工具參數與失敗重試都能被還原。"],
            "refs": [3],
        },
        {
            "kicker": "實務提醒",
            "headline": "先設計失敗路徑，再談自動化率",
            "paragraphs": ["能安全停下、交還人工並保留證據，才是可長期運作的自動化。"],
            "refs": [4],
        },
    ]
    featured = [
        {
            "ref": 1,
            "headline": "讓工具呼叫從展示走向可靠執行",
            "meta": "Link2News Demo et al. · 合成示例 · example.org/paper-1",
            "abstract": {
                "problem": "模型會選對工具，卻可能在參數與狀態管理上失敗。",
                "method": "以結構化輸出、可重試執行器與逐步驗證組成閉環。",
                "result": "合成測試中的任務完成率由 71% 提升至 89%。",
                "limitation": "跨服務權限與長任務恢復仍需要額外治理。",
            },
            "intro_story": "當一個看似簡單的查詢需要連續碰觸三個系統，真正困難的往往不是推理，而是確保每一步都可驗證。",
            "method_paragraphs": [],
            "results_paragraphs": [],
            "figures": [{
                "path": (ASSET_DIR / "link2news-workflow-hero.png").resolve().as_uri(),
                "caption": "圖 1：從 LINE 連結到新聞式週報與 Podcast 的合成示意。",
            }],
            "terms": [{"term": "可觀測性", "blurb": "讓執行狀態、錯誤與結果可被持續追蹤。"}],
            "flaws": [{"title": "環境差異", "note": "測試服務仍比真實企業系統單純。"}],
            "vision": "可靠的代理不是永不失敗，而是每次失敗都有清楚出口。",
        },
        {
            "ref": 2,
            "headline": "小模型如何降低私有部署門檻",
            "meta": "Link2News Demo et al. · 合成示例 · example.org/paper-2",
            "abstract": {
                "problem": "大型模型成本與資料邊界限制了內部流程導入。",
                "method": "以任務路由搭配小模型，只把複雜案例交給大型模型。",
                "result": "合成工作負載的推論成本下降 46%。",
                "limitation": "路由器判斷錯誤時會犧牲少數複雜任務品質。",
            },
            "intro_story": "不是每一封摘要、每一筆分類都需要最大模型，關鍵是知道何時升級。",
            "method_paragraphs": ["系統先判斷任務風險與資訊量，再選擇適合的模型層級。"],
            "results_paragraphs": ["大部分日常任務由小模型完成，少數高風險案例保留較高預算。"],
            "figures": [],
            "terms": [],
            "flaws": [{"title": "路由偏誤", "note": "少見案例可能被錯估為簡單任務。"}],
            "vision": "模型組合將比單一最大模型更接近實務需求。",
        },
    ]
    html = template.render(
        masthead="LINK2NEWS",
        issue_no="DEMO-01",
        issue_date="2026 年 07 月 20-26 日",
        issue_title="可靠代理進入實務",
        n_papers=4,
        n_repos=3,
        n_news=5,
        focus=focus,
        featured=featured,
        roundup={
            "headline": "本週還值得留意的三個訊號",
            "paragraphs": ["開源評測、私有部署與安全治理正在匯流成同一條產品路線。"],
        },
        general_terms=[{"term": "任務路由", "blurb": "依風險與成本把工作交給不同模型。"}],
        references=[
            {"ref": i, "authors_str": "Link2News Demo", "title": f"Synthetic reference {i}",
             "url": f"https://example.org/{i}", "citations": None, "pdf": ""}
            for i in range(1, 5)
        ],
        library_dir="local-library/",
        podcast_minutes=15,
    )
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html, encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1200}, device_scale_factor=1)
        page.goto(HTML_PATH.resolve().as_uri())
        page.screenshot(path=str(PNG_PATH), full_page=False)
        browser.close()
    print(PNG_PATH)


if __name__ == "__main__":
    main()
