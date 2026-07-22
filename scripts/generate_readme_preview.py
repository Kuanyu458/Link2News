"""Render a privacy-safe README preview from public, attributed paper examples."""
from pathlib import Path
from urllib.parse import unquote, urlparse

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
            "headline": "AI 消費正在成為新的市場因子",
            "paragraphs": [
                "研究團隊用 380.8 兆 token 的實際消費資料，觀察 AI 擴散如何反映在公司報酬與工作技能上。",
                "對開發團隊而言，使用深度可能比「有沒有使用 AI」更能解釋組織差異。",
            ],
            "refs": [1],
        },
        {
            "kicker": "離線智慧",
            "headline": "把模糊需求編譯成可重複使用的小模型",
            "paragraphs": ["Program-as-Weights 讓雲端模型只需編譯一次，後續即可在本機以輕量解譯器離線執行。"],
            "refs": [2],
        },
        {
            "kicker": "實務提醒",
            "headline": "先區分一次性編譯與長期推論成本",
            "paragraphs": ["當同一個判斷函數會重複執行，把能力編譯成本機產物，才能同時改善延遲、成本與隱私。"],
            "refs": [2],
        },
    ]
    featured = [
        {
            "ref": 1,
            "headline": "380 兆 token 描出 AI 經濟的輪廓",
            "meta": "Nicola Borri, Yukun Liu, Aleh Tsyvinski · arXiv:2606.30583",
            "abstract": {
                "problem": "市場缺少能量化企業實際 AI 暴險的高頻指標。",
                "method": "以 OpenRouter 使用量建立 AI Factor，再對照股價與職能資料。",
                "result": "樣本期內週 token 用量從 114 億增至 15.6 兆。",
                "limitation": "資料來自單一平台，且不等同全球 AI 使用全貌。",
            },
            "intro_story": "這張圖不是模型參數競賽，而是付費用戶真正消耗的 token：從 2024 年起呈現持續、跨模型的指數成長。",
            "method_paragraphs": [],
            "results_paragraphs": [],
            "figures": [{
                "path": (ASSET_DIR / "paper-ai-premium-figure-1.png").resolve().as_uri(),
                "caption": "圖 1：每週 token 總用量（Borri, Liu & Tsyvinski, AI Premium, arXiv:2606.30583, CC BY 4.0）。",
            }],
            "terms": [{"term": "AI Factor", "blurb": "從 token、金額與用戶成長建立的 AI 消費因子。"}],
            "flaws": [{"title": "平台偏差", "note": "OpenRouter 用戶結構可能與企業內部使用不同。"}],
            "vision": "AI 採用的下一階段，將由實際使用深度而非口號定價。",
        },
        {
            "ref": 2,
            "headline": "把模糊判斷編譯成可攜式小模型",
            "meta": "Wentao Zhang, Liliana Hotsko, Woojeong Kim et al. · arXiv:2607.02512",
            "abstract": {
                "problem": "模糊函數難以寫成規則，卻不該每次都呼叫大模型。",
                "method": "神經編譯器把自然語言規格轉成可本機執行的權重程式。",
                "result": "0.6B 解譯器接近 32B 直接提示，推論記憶體約為 1/50。",
                "limitation": "需先針對函數規格編譯，不適合高度動態的單次任務。",
            },
            "intro_story": "把「這封信是否緊急」寫成規則很難，但也不需每次把內容送到雲端。這篇論文把一次性編譯與長期執行分開。",
            "method_paragraphs": [],
            "results_paragraphs": [],
            "figures": [{
                "path": (ASSET_DIR / "paper-program-as-weights-figure-1.png").resolve().as_uri(),
                "caption": "圖 1：Program-as-Weights 的雲端編譯與本機執行流程（Zhang et al., arXiv:2607.02512, CC BY 4.0）。",
            }],
            "terms": [],
            "flaws": [{"title": "規格依賴", "note": "需求改變時必須重新編譯權重程式。"}],
            "vision": "未來的應用不只呼叫模型，也會讓模型生成可部署的小型工具。",
        },
    ]
    for paper in featured:
        if len(paper.get("figures", [])) != 1:
            raise ValueError(f"featured paper {paper.get('ref')} must have exactly one figure")
        figure_path = Path(unquote(urlparse(paper["figures"][0]["path"]).path))
        if not figure_path.is_file():
            raise FileNotFoundError(figure_path)
    html = template.render(
        masthead="LINK2NEWS",
        issue_no="DEMO-01",
        issue_date="2026 年 07 月 06-12 日",
        issue_title="AI 經濟與離線智慧",
        n_papers=2,
        n_repos=3,
        n_news=5,
        focus=focus,
        featured=featured,
        roundup={
            "headline": "從使用量到本機執行",
            "paragraphs": ["AI 的價值正從模型排行榜，移向真實使用深度、成本與可部署性。"],
        },
        general_terms=[{"term": "任務路由", "blurb": "依風險與成本把工作交給不同模型。"}],
        references=[
            {"ref": 1, "authors_str": "Borri, Liu, Tsyvinski", "title": "AI Premium",
             "url": "https://arxiv.org/abs/2606.30583", "citations": None, "pdf": ""},
            {"ref": 2, "authors_str": "Zhang et al.", "title": "Program-as-Weights",
             "url": "https://arxiv.org/abs/2607.02512", "citations": None, "pdf": ""},
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
