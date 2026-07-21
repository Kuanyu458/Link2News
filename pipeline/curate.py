"""文獻策展：引用數查詢 → 選 4 篇重點文獻 → 統一引用編號 → 文獻庫歸檔。

引用編號 [1]..[n] 三處一致：報紙內文引用、文末參考文獻、文獻庫 PDF 檔名。
"""
import datetime as dt
import logging
import math
import re
import shutil
from pathlib import Path

import requests

from common import PROJECT_ROOT, save_json
from fetch import ARXIV_RE, DOI_RE
from llm import ask_json

log = logging.getLogger("weekly.curate")

S2_API = "https://api.semanticscholar.org/graph/v1/paper"


def fetch_citation_count(url: str, secrets: dict | None = None) -> int | None:
    """Semantic Scholar 查引用數；查不到回 None。免費層常見 429，退避重試。"""
    import time
    m = ARXIV_RE.search(url)
    if m:
        pid = f"arXiv:{m.group(2)}"
    elif (dm := DOI_RE.search(url)):
        pid = f"DOI:{dm.group(2).rstrip('/.')}"
    else:
        return None
    headers = {}
    if secrets and secrets.get("S2_API_KEY"):
        headers["x-api-key"] = secrets["S2_API_KEY"]
    for attempt in range(4):
        try:
            r = requests.get(f"{S2_API}/{pid}", params={"fields": "citationCount"},
                             headers=headers, timeout=20)
            if r.status_code == 200:
                return r.json().get("citationCount", 0)
            if r.status_code == 404:
                return None  # 太新，S2 還沒收錄
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except requests.RequestException as e:
            log.warning("S2 lookup failed for %s: %s", pid, e)
            return None
    log.warning("S2 rate-limited for %s after retries", pid)
    return None


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def fetch_citation_openalex(title: str) -> int | None:
    """備援：OpenAlex 標題搜尋（免費、限流寬鬆）。核對標題避免抓錯論文。"""
    if not title:
        return None
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={"filter": f"title.search:{title[:200]}",
                    "select": "cited_by_count,title",
                    "per-page": 5, "sort": "cited_by_count:desc"},
            timeout=20)
        if r.status_code != 200:
            return None
        want = _norm_title(title)
        hits = [w["cited_by_count"] for w in r.json().get("results", [])
                if _norm_title(w.get("title") or "") == want]
        return max(hits) if hits else None
    except requests.RequestException as e:
        log.warning("OpenAlex lookup failed: %s", e)
        return None


def select_featured(papers: list[dict], cfg: dict) -> list[int]:
    """回傳重點文獻的索引（最多 4 篇），由 LLM 綜合引用數與讀者相關性排序。"""
    if len(papers) <= 4:
        return list(range(len(papers)))
    listing = "\n".join(
        f"{i}. {p['title']}（引用數：{p.get('citations', '未知')}）\n   摘要：{p['abstract'][:300]}"
        for i, p in enumerate(papers))
    prompt = (
        f"讀者背景：{cfg.get('project_context', 'AI 工程師')}。\n"
        "從以下本週論文中選出 4 篇「最值得深讀」的重點文獻，綜合考量：\n"
        "1. 引用次數（高引用代表領域公認重要）\n"
        "2. 與讀者工作的相關性\n"
        "3. 彼此互補（避免四篇都是同一主題）\n"
        '輸出 JSON：{"featured": [索引數字×4], "reason": "一句話選文理由"}\n\n' + listing
    )
    try:
        result = ask_json(prompt, max_tokens=500)
        idx = [i for i in result["featured"] if isinstance(i, int) and 0 <= i < len(papers)][:4]
        if idx:
            log.info("featured selection: %s (%s)", idx, result.get("reason", ""))
            return idx
    except Exception as e:
        log.warning("featured selection failed (%s); falling back to citation order", e)
    ranked = sorted(range(len(papers)),
                    key=lambda i: papers[i].get("citations") or 0, reverse=True)
    return ranked[:4]


def _week_folder_name(date: dt.date | None = None) -> str:
    """年月+該月第幾週，如 202607w2。"""
    d = date or dt.date.today()
    return f"{d.year}{d.month:02d}w{math.ceil(d.day / 7)}"


def _safe_filename(title: str, max_len: int = 80) -> str:
    name = re.sub(r'[/\\:?*"<>|\n\r]', " ", title)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len].strip()


def curate(ingested: dict, cfg: dict, out_dir: Path, secrets: dict | None = None) -> dict:
    """主入口。為所有論文加上 citations 數、featured 標記、引用編號 ref，
    複製 PDF 到文獻庫，回傳並保存 citations.json。"""
    import time
    papers = ingested["papers"]

    # 1. 引用數：S2 優先，限流/查無則 OpenAlex 標題搜尋備援（間隔 1.5s）
    for p in papers:
        p["citations"] = fetch_citation_count(p["url"], secrets)
        if p["citations"] is None:
            p["citations"] = fetch_citation_openalex(p["title"])
        log.info("citations %s: %s", p["title"][:50], p["citations"])
        time.sleep(1.5)

    # 2. 選重點文獻
    featured_idx = select_featured(papers, cfg)

    # 3. 編號：featured 先取 [1]-[4]，其餘接續
    order = featured_idx + [i for i in range(len(papers)) if i not in featured_idx]
    for ref, i in enumerate(order, 1):
        papers[i]["ref"] = ref
        papers[i]["featured"] = i in featured_idx

    # 4. 文獻庫歸檔（先清掉本週舊編號檔，避免重跑後新舊編號混雜）
    lib_dir = PROJECT_ROOT / "文獻庫" / _week_folder_name()
    lib_dir.mkdir(parents=True, exist_ok=True)
    for old in lib_dir.glob("[[]*[]] *.pdf"):
        old.unlink()
    archived = []
    for p in papers:
        if not p.get("pdf_path") or not Path(p["pdf_path"]).exists():
            continue
        dest = lib_dir / f"[{p['ref']}] {_safe_filename(p['title'] or 'untitled')}.pdf"
        shutil.copy2(p["pdf_path"], dest)
        archived.append(dest.name)
    log.info("archived %d PDFs to %s", len(archived), lib_dir)

    citations = {
        "library_dir": str(lib_dir),
        "references": [
            {"ref": p["ref"], "title": p["title"], "authors": p.get("authors", []),
             "url": p["url"], "citations": p.get("citations"),
             "featured": p["featured"],
             "pdf": f"[{p['ref']}] {_safe_filename(p['title'] or 'untitled')}.pdf"
                    if p.get("pdf_path") else None}
            for p in sorted(papers, key=lambda x: x["ref"])
        ],
    }
    save_json(out_dir / "citations.json", citations)
    return citations
