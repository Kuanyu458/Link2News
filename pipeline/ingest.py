"""內容抓取：論文（arXiv/DOI）、GitHub repo、新聞全文，以及論文圖表抽取。"""
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from fetch import ARXIV_RE, GITHUB_RE, DOI_RE

log = logging.getLogger("weekly.ingest")
UA = {"User-Agent": "weekly-report-bot/1.0 (personal research digest)"}
ATOM = "{http://www.w3.org/2005/Atom}"


# ---------- 論文 ----------

def ingest_paper(resource: dict, assets_dir: Path) -> dict:
    """回傳 {title, authors, abstract, full_text, pdf_path, figures: [png paths], url}"""
    url = resource["url"]
    out = {"url": url, "kind": "paper", "via": resource.get("via", ""),
           "title": "", "authors": [], "abstract": "", "full_text": "",
           "pdf_path": None, "figures": []}

    m = ARXIV_RE.search(url)
    if m:
        arxiv_id = m.group(2) + (m.group(3) or "")
        meta = _arxiv_metadata(arxiv_id)
        out.update(meta)
        pdf_path = assets_dir / f"arxiv_{arxiv_id.replace('/', '_')}.pdf"
        if _download(f"https://arxiv.org/pdf/{arxiv_id}", pdf_path):
            out["pdf_path"] = str(pdf_path)
    elif (dm := DOI_RE.search(url)):
        out.update(_doi_metadata(dm.group(2).rstrip("/.")))
    if not out["title"]:
        out.update(_page_metadata(url))

    if out["pdf_path"]:
        out["full_text"] = extract_pdf_text(Path(out["pdf_path"]))
        out["figures"] = extract_pdf_figures(Path(out["pdf_path"]), assets_dir)
    return out


def _arxiv_metadata(arxiv_id: str) -> dict:
    try:
        r = requests.get("https://export.arxiv.org/api/query",
                         params={"id_list": arxiv_id}, timeout=30, headers=UA)
        entry = ET.fromstring(r.text).find(f"{ATOM}entry")
        if entry is None:
            return {}
        return {
            "title": re.sub(r"\s+", " ", entry.findtext(f"{ATOM}title", "")).strip(),
            "abstract": re.sub(r"\s+", " ", entry.findtext(f"{ATOM}summary", "")).strip(),
            "authors": [a.findtext(f"{ATOM}name", "") for a in entry.findall(f"{ATOM}author")][:8],
        }
    except Exception as e:
        log.warning("arxiv metadata failed for %s: %s", arxiv_id, e)
        return {}


def _doi_metadata(doi: str) -> dict:
    try:
        r = requests.get(f"https://api.crossref.org/works/{doi}", timeout=30, headers=UA)
        msg = r.json()["message"]
        return {
            "title": (msg.get("title") or [""])[0],
            "abstract": re.sub(r"<[^>]+>", "", msg.get("abstract", "")),
            "authors": [f"{a.get('given','')} {a.get('family','')}".strip()
                        for a in msg.get("author", [])][:8],
        }
    except Exception as e:
        log.warning("crossref failed for %s: %s", doi, e)
        return {}


def _page_metadata(url: str) -> dict:
    """非 arXiv/DOI 的論文頁：抓頁面標題與內文摘要。"""
    import trafilatura
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return {}
    meta = trafilatura.extract_metadata(downloaded)
    body = trafilatura.extract(downloaded) or ""
    return {"title": (meta.title if meta else "") or "", "abstract": body[:1500],
            "full_text": body}


def _download(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, timeout=120, headers=UA)
        r.raise_for_status()
        if not r.content[:5] == b"%PDF-":
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return True
    except requests.RequestException as e:
        log.warning("pdf download failed %s: %s", url, e)
        return False


def extract_pdf_text(pdf_path: Path, max_chars: int = 60000) -> str:
    import pypdfium2 as pdfium
    try:
        doc = pdfium.PdfDocument(pdf_path)
        pages = []
        try:
            for page in doc:
                textpage = page.get_textpage()
                try:
                    pages.append(textpage.get_text_range(0, textpage.count_chars()))
                finally:
                    textpage.close()
                    page.close()
        finally:
            doc.close()
        return "\n".join(pages)[:max_chars]
    except Exception as e:
        log.warning("pdf text extract failed %s: %s", pdf_path, e)
        return ""


def _pdf_text_lines(textpage) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Return text lines and PDF-coordinate bounds from a PDFium text page."""
    lines = []
    chars = []
    boxes = []

    def flush():
        if not chars or not boxes:
            chars.clear()
            boxes.clear()
            return
        text = "".join(chars).strip()
        if text:
            left = min(box[0] for box in boxes)
            bottom = min(box[1] for box in boxes)
            right = max(box[2] for box in boxes)
            top = max(box[3] for box in boxes)
            lines.append((text, (left, bottom, right, top)))
        chars.clear()
        boxes.clear()

    for index in range(textpage.count_chars()):
        char = textpage.get_text_range(index, 1)
        if char in {"\r", "\n"}:
            flush()
            continue
        chars.append(char)
        try:
            boxes.append(textpage.get_charbox(index))
        except Exception:
            pass
    flush()
    return lines


def extract_pdf_figures(pdf_path: Path, assets_dir: Path, max_figures: int = 6) -> list[dict]:
    """抽取論文中的圖與表。

    策略：找每頁中含 'Figure N'/'Table N' caption 的區塊，將 caption 上方（圖）
    或下方（表）的區域以高解析度截圖存 PNG——比抽 embedded image 更能完整
    保留向量圖與表格。回傳 [{path, caption}]。
    """
    import pypdfium2 as pdfium
    figures = []
    try:
        doc = pdfium.PdfDocument(pdf_path)
        try:
            for pno, page in enumerate(doc):
                if len(figures) >= max_figures:
                    page.close()
                    break
                textpage = page.get_textpage()
                try:
                    lines = _pdf_text_lines(textpage)
                finally:
                    textpage.close()
                width, height = page.get_size()
                for text, bounds in lines:
                    m = re.match(r"^(Figure|Fig\.?|Table)\s+(\d+)[.:]", text)
                    if not m:
                        continue
                    x0, y0, x1, y1 = bounds
                    is_table = m.group(1).lower().startswith("tab")
                    if is_table:
                        # PDF coordinates start at the bottom-left. Tables are
                        # usually below their captions.
                        region = (20, max(20, y0 - 260), width - 20, y0)
                    else:
                        region = (20, y1, width - 20, min(height - 20, y1 + 320))
                    left, bottom, right, top = region
                    if top - bottom < 60 or right - left < 120:
                        continue
                    bitmap = page.render(
                        scale=180 / 72,
                        crop=(left, bottom, width - right, height - top),
                    )
                    if bitmap.width < 120 or bitmap.height < 60:
                        bitmap.close()
                        continue
                    fname = f"{pdf_path.stem}_p{pno+1}_{m.group(1).lower()[:3]}{m.group(2)}.png"
                    fpath = assets_dir / fname
                    image = bitmap.to_pil()
                    image.save(fpath, format="PNG")
                    bitmap.close()
                    caption = re.sub(r"\s+", " ", text)[:300]
                    figures.append({"path": str(fpath), "caption": caption})
                    if len(figures) >= max_figures:
                        break
                page.close()
            if max_figures > 0 and not figures and len(doc) > 0:
                # 保底：有些 PDF 的 caption 無法從文字層辨識。用首頁縮圖
                # 代替空白專欄，並明確標示為論文預覽而非 Figure。
                page = doc[0]
                try:
                    bitmap = page.render(scale=150 / 72)
                    try:
                        fname = f"{pdf_path.stem}_p1_preview.png"
                        fpath = assets_dir / fname
                        bitmap.to_pil().save(fpath, format="PNG")
                        figures.append({
                            "path": str(fpath),
                            "caption": f"論文首頁預覽：{pdf_path.stem}",
                        })
                    finally:
                        bitmap.close()
                finally:
                    page.close()
        finally:
            doc.close()
    except Exception as e:
        log.warning("figure extract failed %s: %s", pdf_path, e)
    return figures


# ---------- GitHub ----------

def ingest_github(resource: dict, secrets: dict) -> dict:
    url = resource["url"]
    m = GITHUB_RE.search(url)
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    headers = dict(UA)
    if tok := secrets.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {tok}"
    out = {"url": f"https://github.com/{owner}/{repo}", "kind": "github",
           "via": resource.get("via", ""), "owner": owner, "repo": repo,
           "description": "", "stars": 0, "language": "", "topics": [], "readme": ""}
    try:
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=30)
        if r.status_code == 200:
            j = r.json()
            out.update({"description": j.get("description") or "",
                        "stars": j.get("stargazers_count", 0),
                        "language": j.get("language") or "",
                        "topics": j.get("topics", [])})
        r = requests.get(f"https://api.github.com/repos/{owner}/{repo}/readme",
                         headers={**headers, "Accept": "application/vnd.github.raw+json"}, timeout=30)
        if r.status_code == 200:
            out["readme"] = r.text[:15000]
    except requests.RequestException as e:
        log.warning("github ingest failed %s/%s: %s", owner, repo, e)
    return out


# ---------- 新聞 ----------

def ingest_news(resource: dict) -> dict:
    import trafilatura
    url = resource["url"]
    out = {"url": url, "kind": "news", "via": resource.get("via", ""),
           "title": "", "full_text": resource.get("context", "")}
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        meta = trafilatura.extract_metadata(downloaded)
        out["title"] = (meta.title if meta else "") or ""
        out["full_text"] = (trafilatura.extract(downloaded) or out["full_text"])[:20000]
    return out


def ingest_all(resolved: dict, assets_dir: Path, secrets: dict, progress=None) -> dict:
    """依 kind 分派抓取。回傳 {papers, repos, news, unresolved}"""
    assets_dir.mkdir(parents=True, exist_ok=True)
    papers, repos, news = [], [], []
    seen_papers, seen_repos = set(), set()
    resources = resolved["resources"]
    total = len(resources)
    for index, res in enumerate(resources):
        if progress:
            progress(index, total, res, False)
        try:
            if res["kind"] == "paper":
                if res["url"] in seen_papers:
                    continue
                seen_papers.add(res["url"])
                p = ingest_paper(res, assets_dir)
                if "withdrawn" in (p["title"] + p["abstract"][:200]).lower():
                    resolved["unresolved"].append({"url": res["url"], "via": res.get("via", ""),
                                                   "reason": "paper withdrawn"})
                elif not p["title"] and not p["full_text"]:
                    resolved["unresolved"].append({"url": res["url"], "via": res.get("via", ""),
                                                   "reason": "no metadata/content"})
                else:
                    papers.append(p)
            elif res["kind"] == "github":
                g = ingest_github(res, secrets)
                key = (g["owner"].lower(), g["repo"].lower())
                if key in seen_repos:
                    continue
                seen_repos.add(key)
                if not g["description"] and not g["readme"]:
                    resolved["unresolved"].append({"url": g["url"], "via": res.get("via", ""),
                                                   "reason": "repo not found or empty"})
                else:
                    repos.append(g)
            else:
                news.append(ingest_news(res))
        except Exception as e:
            log.error("ingest failed for %s: %s", res["url"], e)
            resolved["unresolved"].append({"url": res["url"], "via": res.get("via", ""),
                                           "reason": f"ingest error: {e}"})
        finally:
            if progress:
                progress(index + 1, total, res, True)
    log.info("ingested %d papers, %d repos, %d news", len(papers), len(repos), len(news))
    return {"papers": papers, "repos": repos, "news": news,
            "unresolved": resolved["unresolved"]}
