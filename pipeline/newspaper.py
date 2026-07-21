"""研究週刊 PDF：A4 直向、橫書雙欄，HTML 在手機自動切換單欄。

模型以主編身分產出版面 JSON → Jinja2 響應式版型 → Playwright A4 PDF。
引用編號來自 curate.py 的 citations.json，與文獻庫 PDF 檔名一致。
"""
import datetime as dt
import html
import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from common import PROJECT_ROOT
from llm import ask_json

log = logging.getLogger("weekly.newspaper")


def _issue_metadata(wk: str) -> tuple[str, str]:
    """把 2026-W28 轉為穩定的期號與該週日期範圍。"""
    try:
        year_text, week_text = wk.split("-W", 1)
        year, week = int(year_text), int(week_text)
        monday = dt.date.fromisocalendar(year, week, 1)
        sunday = dt.date.fromisocalendar(year, week, 7)
        if monday.month == sunday.month:
            date_text = f"{year} 年 {monday.month:02d} 月 {monday.day:02d}-{sunday.day:02d} 日"
        else:
            date_text = (f"{year} 年 {monday.month:02d} 月 {monday.day:02d} 日-"
                         f"{sunday.month:02d} 月 {sunday.day:02d} 日")
        return f"{year}-{week:02d}", date_text
    except (TypeError, ValueError):
        return wk, dt.date.today().strftime("%Y 年 %m 月 %d 日")


def _figures_by_ref(ingested: dict) -> tuple[list[dict], str]:
    """整理所有論文圖表，回傳 (figures 清單, 給主編看的文字清單)。"""
    figures = []
    for p in ingested["papers"]:
        for f in p.get("figures", []):
            figures.append({"id": len(figures), "ref": p.get("ref"),
                            "path": f["path"], "caption": f["caption"]})
    listing = "\n".join(
        f"- figure_id={f['id']}（屬於文獻 [{f['ref']}]）：{f['caption'][:110]}"
        for f in figures) or "（本週無可用圖表）"
    return figures, listing


def _editor_payload(reports: dict, ingested: dict, citations: dict, cfg: dict) -> dict:
    figures, figure_list = _figures_by_ref(ingested)
    featured_refs = [r["ref"] for r in citations["references"] if r["featured"]]
    ref_list = "\n".join(
        f"[{r['ref']}]{'★重點' if r['featured'] else ''} {r['title']}"
        f"（作者：{', '.join(r['authors'][:3])}；引用數：{r['citations'] if r['citations'] is not None else '未知'}）"
        for r in citations["references"])

    prompt = f"""你是一份台灣傳統紙本報紙風格研究週刊的主編，撰稿風格是資深科技產業線記者
（如經濟日報科技版、數位時代的產業報導）：新聞導言破題、消息來源式敘述、
產業影響分析、文字精煉有現場感。
讀者背景：{cfg.get('project_context', 'AI 工程師兼 AI 醫材工程師')}。
版面為 A4 直向、由左至右橫書的學術論文式雙欄排版；HTML 在手機上會改為單欄。
請以適合橫向閱讀的短標題、短段落與清楚的小節層級編排本期內容，輸出 JSON。

用語規範（重要）：
- 除了無法翻譯的專有名詞（模型名、系統名、演算法名）外，一律使用繁體中文，
  避免中英夾雜：寫「模型」不寫 model、寫「基準測試」不寫 benchmark、
  寫「微調」不寫 fine-tune、寫「代理」或「智慧代理」不寫 agent（首次出現可括注英文）
- 數值與研究結果保留阿拉伯數字以利掃讀，避免不必要的長英文句
- 標題全中文、簡短有力（主標 10-18 字內）

## 第 1 部分 focus：本週焦點（正好 3 個主題）
綜觀本週全部素材（論文、GitHub 專案、新聞），歸納出讀者「身為 AI 工程師、AI 醫材工程師最需要關注」的 3 個主題。每個主題：
- kicker：8 字內的眼題（如「AGENT 記憶革命」）
- headline：有故事性、引人入勝的標題（不是論文標題直譯）
- paragraphs：2-3 段（每段 80-120 字）敘事開場→技術意涵→對讀者工作的影響
- refs：這個主題導引到的文獻編號陣列（必須用下方文獻編號）

## 第 2 部分 featured：重點學術文獻（正好 4 篇，用標記★重點的文獻，依編號序）
每篇採「輕學術期刊」結構，目標是讓讀者 3-5 分鐘抓到問題/結論/缺陷：
- ref：文獻編號
- headline：雜誌化中文標題（副標可含原文縮寫）
- meta：一行「作者 et al. · 引用數 N · 原文連結」
- abstract：摘要框，四行直給——
  - problem：一句話點出痛點（「這篇想解決 X，但現有方法在 Y 情況表現不佳」）
  - method：1-2 句核心做法（只保留關鍵點）
  - result：一句最重要的數字（「AUC 從 0.82 到 0.85」式）
  - limitation：一句直說缺陷（「但在 Z 情況仍不穩」式）
- intro_story：故事開場引言（60-90 字），用具體情境切入，不用抽象定義
- method_paragraphs：正好 2 段（每段 90-130 字）「我們怎麼做」——用「為什麼這樣設計→做了什麼→
  遇到什麼難點怎麼解」的故事線寫，語氣用「他們嘗試…他們發現…」而非「本研究提出」
- results_paragraphs：正好 1 段（100-150 字）「數字告訴我們什麼」——給數字並用現實情境解讀
  （「這 3% 在臨床上可能意味著每年多識別數百名高風險患者」式），
  明說什麼情況好、什麼情況沒改善
- flaws：缺陷清單 2-3 條，各 {{"title": "六字內短標", "note": "一句說明原因與實務影響"}}
- vision：一句願景式結尾（「從技術上看只是一個模型；從臨床上看可能是新篩檢策略的起點」式）
- figure_ids：從下方圖表清單選正好 1 張最能說明該文獻的（只能選屬於該文獻的）

## 第 3 部分 roundup：其他學術文獻總覽（1 篇短文）
- headline：總覽標題
- paragraphs：2-3 段（共 250-350 字）快速帶過未入選重點的文獻與本週學術動向，每提到一篇附 [編號]

## 第 4 部分 terms：本週關鍵術語（正好 5 個）
用名詞報告的 5 個術語，各壓縮成 50-80 字科普：意義＋由來/方法一句話。
每個術語標注 ref：若該術語主要出自某篇重點文獻，填該文獻編號
（會排版在該篇文章旁的「名詞解釋」框）；與特定文獻無關的填 null
（會排入「新詞櫥窗」專欄）。

規格：內文引用一律用〔n〕編號；記者筆法——導言破題、數字具體、
缺陷直說不粉飾；正文不用條列式（flaws 清單除外）。
另輸出 issue_title：本期 10 字內的一句話主題（會印在每頁報眉）。

輸出 JSON schema：
{{"issue_title": str,
 "focus": [{{"kicker": str, "headline": str, "paragraphs": [str], "refs": [int]}} ×3],
 "featured": [{{"ref": int, "headline": str, "meta": str,
                "abstract": {{"problem": str, "method": str, "result": str, "limitation": str}},
                "intro_story": str, "method_paragraphs": [str], "results_paragraphs": [str],
                "flaws": [{{"title": str, "note": str}}], "vision": str,
                "figure_ids": [int]}} ×4],
 "roundup": {{"headline": str, "paragraphs": [str]}},
 "terms": [{{"term": str, "blurb": str, "ref": int|null}} ×5]}}

=== 文獻編號表（★重點=第 2 部分的 4 篇：{featured_refs}）===
{ref_list}

=== 可用圖表清單 ===
{figure_list}

=== 名詞說明報告 ===
{reports['terms'][:12000]}

=== 文獻摘要報告 ===
{reports['papers'][:40000]}

=== GitHub 導入發想 ===
{reports['github'][:12000]}
"""
    layout = ask_json(prompt, max_tokens=16000)

    fig_index = {f["id"]: f for f in figures}

    def as_render_fig(f):
        return {"path": Path(f["path"]).resolve().as_uri(), "caption": f["caption"]}

    for pp in layout.get("featured", []):
        pp["figures"] = []
        for fid in (pp.pop("figure_ids", None) or []):
            try:
                fid = int(fid)
            except (TypeError, ValueError):
                continue
            f = fig_index.get(fid)
            if f and f["ref"] == pp.get("ref"):
                pp["figures"].append(as_render_fig(f))
        if not pp["figures"]:
            # 保底：主編沒選到有效圖時，用該文獻的第一張圖（通常是 Figure 1 總覽圖）
            pp["figures"] = [as_render_fig(f) for f in figures
                             if f["ref"] == pp.get("ref")][:1]

    # 術語分流：屬於某篇重點文獻的 → 排入該篇旁的「名詞解釋」框；其餘 → 新詞櫥窗
    featured_by_ref = {pp.get("ref"): pp for pp in layout.get("featured", [])}
    general_terms = []
    for t in layout.get("terms", []):
        ref = t.get("ref")
        try:
            ref = int(ref) if ref is not None else None
        except (TypeError, ValueError):
            ref = None
        if ref in featured_by_ref:
            featured_by_ref[ref].setdefault("terms", []).append(t)
        else:
            general_terms.append(t)
    layout["general_terms"] = general_terms
    return layout


def render_newspaper(reports: dict, ingested: dict, citations: dict, cfg: dict,
                     out_dir: Path, wk: str, podcast_path: Path | None,
                     layout_override: dict | None = None) -> Path:
    """產生 HTML/PDF；layout_override 可用來重排已完成的舊版版面資料。"""
    layout = layout_override if layout_override is not None else _editor_payload(
        reports, ingested, citations, cfg)
    (out_dir / "layout.json").write_text(
        json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")

    refs = []
    for r in citations["references"]:
        authors = r.get("authors") or []
        refs.append({**r, "authors_str": (", ".join(authors[:3])
                                          + (" et al." if len(authors) > 3 else "")) or "—"})

    # 術語框空間有限：科普文強制截長
    for t in layout.get("terms", []):
        blurb = (t.get("blurb") or "").strip()
        t["blurb"] = blurb[:110] + ("…" if len(blurb) > 110 else "")

    env = Environment(loader=FileSystemLoader(PROJECT_ROOT / "templates"), autoescape=True)
    tpl = env.get_template("newspaper.html")
    issue_no, issue_date = _issue_metadata(wk)
    html = tpl.render(
        masthead=cfg["report"].get("title", "週知快報"),
        issue_no=issue_no,
        issue_date=issue_date,
        issue_title=layout.get("issue_title", ""),
        n_papers=len(ingested["papers"]), n_repos=len(ingested["repos"]),
        n_news=len(ingested["news"]),
        focus=layout.get("focus", []),
        featured=layout.get("featured", []),
        roundup=layout.get("roundup", {"headline": "", "paragraphs": []}),
        general_terms=layout.get("general_terms", layout.get("terms", [])),
        references=refs,
        library_dir=citations.get("library_dir", ""),
        podcast_minutes=cfg["podcast"].get("target_minutes", 15),
    )
    html_path = out_dir / "newspaper.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path = out_dir / f"weekly_{wk}.pdf"
    _print_pdf(html_path, pdf_path, cfg["report"].get("title", "週知快報"),
               layout.get("issue_title", ""),
               issue_date)
    log.info("newspaper PDF: %s", pdf_path)
    return pdf_path


def _print_pdf(html_path: Path, pdf_path: Path, masthead: str,
               issue_title: str = "", issue_date: str = "") -> None:
    from playwright.sync_api import sync_playwright
    # 每頁報眉（Chromium 列印頁首，落在 @page 上邊距內）。
    masthead = html.escape(masthead)
    issue_title = html.escape(issue_title)
    issue_date = html.escape(issue_date)
    header = (
        '<div style="width:100%;font-size:7pt;color:#555;'
        'font-family:\'PingFang TC\',sans-serif;display:flex;'
        'justify-content:space-between;align-items:flex-end;padding:0 9mm 1mm 9mm;'
        'border-bottom:1px solid #777;margin:0;">'
        f'<span style="color:#111;font-weight:700;letter-spacing:.25em;">{masthead}</span>'
        f'<span>{issue_title}</span>'
        f'<span>{issue_date}</span></div>'
    )
    footer = (
        '<div style="width:100%;font-size:7pt;color:#555;'
        'font-family:\'PingFang TC\',sans-serif;text-align:center;">'
        '第 <span class="pageNumber"></span> 版</div>'
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri())
        page.wait_for_timeout(800)
        page.pdf(path=str(pdf_path), format="A4", print_background=True,
                 landscape=False, prefer_css_page_size=True, display_header_footer=True,
                 header_template=header, footer_template=footer)
        browser.close()
