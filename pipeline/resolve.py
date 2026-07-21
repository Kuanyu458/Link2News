"""連結解析層：展開社群貼文，從內文與留言挖出真正的論文/GitHub 連結。

輸入 fetch.py 的連結清單，輸出解析後的資源清單：
  [{url, kind: paper|github|news, via: 原始分享連結, context: 貼文文字}]
無法解析的社群貼文歸入 unresolved。
"""
import html
import json
import logging
import re
from urllib.parse import urlparse, parse_qs, unquote

import requests

from common import URL_RE
from fetch import classify, normalize_url, canonicalize, ARXIV_RE

log = logging.getLogger("weekly.resolve")

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) weekly-report-bot/1.0"}
SHORTENERS = ("t.co", "bit.ly", "lin.ee", "reurl.cc", "tinyurl.com", "goo.gl",
              "buff.ly", "ow.ly", "is.gd", "dlvr.it", "lnkd.in", "youtu.be")


# 「?u=<目標網址>」型的轉址包裝頁（FB/Threads/IG/Google 等）：直接解參數，不必載入頁面
REDIRECTOR_HOSTS = {
    "l.facebook.com": "u", "lm.facebook.com": "u", "l.instagram.com": "u",
    "l.threads.com": "u", "l.threads.net": "u", "l.messenger.com": "u",
    "out.reddit.com": "url", "www.google.com": "q", "google.com": "q",
    "href.li": None, "t.umblr.com": "z",
}


def unwrap_redirector(url: str) -> str:
    p = urlparse(url)
    host = (p.netloc or "").lower()
    if host not in REDIRECTOR_HOSTS:
        return url
    if host in ("www.google.com", "google.com") and not p.path.startswith("/url"):
        return url
    param = REDIRECTOR_HOSTS[host]
    if param is None:  # href.li 形式：目標直接接在 ? 後
        target = unquote(p.query)
    else:
        target = (parse_qs(p.query).get(param) or [""])[0]
    if target.startswith("http"):
        return normalize_url(target)
    return url


def expand_short_url(url: str) -> str:
    url = unwrap_redirector(url)
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    if host not in SHORTENERS:
        return url
    try:
        r = requests.head(url, allow_redirects=True, timeout=15, headers=UA)
        return normalize_url(r.url)
    except requests.RequestException:
        try:
            r = requests.get(url, allow_redirects=True, timeout=15, headers=UA, stream=True)
            return normalize_url(r.url)
        except requests.RequestException:
            return url


def _urls_in(text: str) -> list[str]:
    return [normalize_url(u) for u in URL_RE.findall(text or "")]


# ---------- 各平台貼文展開：回傳 (貼文+留言文字, 其中的外部連結) ----------

def _expand_reddit(url: str, comment_limit: int = 5) -> tuple[str, list[str]]:
    api = url.split("?")[0].rstrip("/") + ".json"
    r = requests.get(api, headers=UA, params={"sort": "top", "limit": comment_limit}, timeout=20)
    r.raise_for_status()
    data = r.json()
    texts, links = [], []
    post = data[0]["data"]["children"][0]["data"]
    texts.append(post.get("title", ""))
    texts.append(post.get("selftext", ""))
    if post.get("url_overridden_by_dest"):
        links.append(post["url_overridden_by_dest"])
    # 只讀熱門前 N 則頂層留言；不展開回覆樹，避免留言連結灌入週報。
    if len(data) > 1:
        comments = [c.get("data", {}) for c in data[1]["data"]["children"]
                    if c.get("kind") == "t1"]
        comments.sort(key=lambda c: c.get("score") or 0, reverse=True)
        texts.extend(c.get("body", "") for c in comments[:comment_limit] if c.get("body"))
    body = "\n".join(t for t in texts if t)
    return body, links + _urls_in(body)


def _expand_hn(url: str, comment_limit: int = 5) -> tuple[str, list[str]]:
    m = re.search(r"id=(\d+)", url)
    if not m:
        return "", []
    r = requests.get(f"https://hn.algolia.com/api/v1/items/{m.group(1)}", timeout=20)
    r.raise_for_status()
    item = r.json()
    texts, links = [item.get("title") or ""], []
    if item.get("url"):
        links.append(item["url"])
    # Algolia children 維持 HN 顯示排名；只讀前 N 則頂層留言，不展開回覆。
    for comment in (item.get("children") or [])[:comment_limit]:
        if comment.get("text"):
            texts.append(html.unescape(re.sub(r"<[^>]+>", " ", comment["text"])))
    body = "\n".join(texts)
    return body, links + _urls_in(body)


def _expand_twitter(url: str, comment_limit: int = 5) -> tuple[str, list[str]]:
    """X/Twitter：用 fxtwitter 公開 API 抓貼文內容。"""
    m = re.search(r"(?:twitter|x)\.com/(\w+)/status/(\d+)", url)
    if not m:
        return "", []
    r = requests.get(f"https://api.fxtwitter.com/{m.group(1)}/status/{m.group(2)}",
                     headers=UA, timeout=20)
    r.raise_for_status()
    tweet = r.json().get("tweet", {})
    body = tweet.get("text", "")
    links = _urls_in(body)
    # quote tweet 內的連結也算
    if q := tweet.get("quote"):
        body += "\n" + q.get("text", "")
        links += _urls_in(q.get("text", ""))
    return body, links


def _expand_generic(url: str, comment_limit: int = 5) -> tuple[str, list[str]]:
    """無結構化留言 API 的社群只抓貼文正文，不掃描整串留言。"""
    import trafilatura
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return "", []
    body = trafilatura.extract(downloaded, include_links=True, include_comments=False) or ""
    links = _urls_in(body)
    return body, links


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


# 整輪解析共用一個瀏覽器實例（每次重啟要 1-2 秒，連結多時差距很大）
_pw = None
_browser = None


def _get_browser():
    global _pw, _browser
    if _browser is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(headless=True)
    return _browser


def close_browser():
    global _pw, _browser
    try:
        if _browser:
            _browser.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _browser = _pw = None


def _expand_playwright(url: str, comment_limit: int = 5) -> tuple[str, list[str]]:
    """登入牆平台備援：headless 瀏覽器抓可見文字與 href。

    站內連結（同網域）一律丟棄——登入牆平台的站內 href 是導覽列/相關貼文雜訊，
    貼文真正引用的外部連結會經由 l.threads.com 這類轉址（不同網域）出現。
    """
    try:
        page = _get_browser().new_page(user_agent=UA["User-Agent"])
        try:
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            # 登入牆平台無可靠公開留言排名時，只取第一個 article（貼文主體）。
            if page.locator("article").count():
                scope = page.locator("article").first
                body = scope.inner_text()
                hrefs = scope.eval_on_selector_all(
                    "a[href^='http']", "els => els.map(e => e.href)")
            else:
                # 沒有可辨識的貼文容器時只取頁面摘要，不以整頁文字冒充熱門留言。
                meta = page.locator('meta[property="og:description"]')
                body = (meta.first.get_attribute("content") if meta.count() else "") or page.title()
                hrefs = []
        finally:
            page.close()
        page_host = _host(url)
        hrefs = [h for h in hrefs if _host(unwrap_redirector(h)) != page_host]
        return body[:20000], [normalize_url(h) for h in hrefs]
    except Exception as e:
        log.warning("playwright expand failed for %s: %s", url, e)
        return "", []


def _search_arxiv_by_title(title: str) -> str | None:
    """標題備援搜尋：arXiv API 以標題查原文連結。"""
    try:
        r = requests.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f'ti:"{title}"', "max_results": 1},
            timeout=20,
        )
        m = re.search(r"<id>(https?://arxiv\.org/abs/[^<]+)</id>", r.text)
        return m.group(1) if m else None
    except requests.RequestException:
        return None


def _guess_paper_title(post_text: str, timeout: int = 45) -> str | None:
    """讓 Claude 從貼文文字辨識被提到的論文標題（無連結時的備援）。"""
    if len(post_text.strip()) < 30:
        return None
    from llm import ask
    prompt = (
        "以下是一則社群貼文。若其中明確提到某篇學術論文的標題，輸出該英文標題本身"
        "（不加引號、不加任何說明）；若沒有提到論文，只輸出 NONE。\n\n" + post_text[:3000]
    )
    try:
        ans = (ask(prompt, max_tokens=200, timeout=timeout) or "").strip()
    except Exception as e:
        log.warning("title guess LLM call failed: %s", e)  # 備援步驟，失敗不致命
        return None
    if not ans or ans.upper().startswith("NONE") or len(ans) > 250:
        return None
    return ans


PLATFORM_EXPANDERS = [
    (re.compile(r"(twitter|x)\.com/\w+/status/", re.I), _expand_twitter),
    (re.compile(r"reddit\.com/r/.+/comments/", re.I), _expand_reddit),
    (re.compile(r"news\.ycombinator\.com/item", re.I), _expand_hn),
]

LOGIN_WALLED = re.compile(r"(threads\.(net|com)|facebook\.com|instagram\.com|linkedin\.com)", re.I)


def expand_social(url: str, comment_limit: int = 5) -> tuple[str, list[str]]:
    for pattern, fn in PLATFORM_EXPANDERS:
        if pattern.search(url):
            try:
                return fn(url, comment_limit)
            except Exception as e:
                log.warning("%s expand failed (%s), falling back", url, e)
                break
    if LOGIN_WALLED.search(url):
        return _expand_playwright(url, comment_limit)
    body, links = _expand_generic(url, comment_limit)
    if not body and not links:
        return _expand_playwright(url, comment_limit)
    return body, links


def resolve_links(items: list[dict], cfg: dict, progress=None) -> dict:
    """主入口。回傳 {"resources": [...], "unresolved": [...]}"""
    social_domains = cfg["resolve"]["social_domains"]
    max_depth = cfg["resolve"].get("max_depth", 2)
    resources, unresolved, seen = [], [], set()

    def add_resource(url, kind, via, context=""):
        if url in seen:
            return
        seen.add(url)
        resources.append({"url": url, "kind": kind, "via": via, "context": context[:2000]})

    def process(url: str, via: str, context: str, depth: int):
        url = canonicalize(expand_short_url(url))
        if url in seen:
            return
        kind = classify(url, social_domains)
        if kind in ("paper", "github", "news"):
            add_resource(url, kind, via, context)
            return
        # social：展開貼文
        if depth >= max_depth:
            unresolved.append({"url": url, "via": via, "reason": "max depth"})
            return
        seen.add(url)
        body, links = expand_social(
            url, comment_limit=int(cfg["resolve"].get("social_comment_limit", 5)))
        found = 0
        # 貼文主連結排在最前；留言中的順帶連結多，每則貼文最多收 3 個資源
        per_post_cap = cfg["resolve"].get("per_post_cap", 3)
        for link in links:
            if found >= per_post_cap:
                break
            link = canonicalize(expand_short_url(link))
            if link == url or link in seen:
                continue
            k = classify(link, social_domains)
            if k in ("paper", "github"):
                add_resource(link, k, url, body)
                found += 1
            elif k == "social" and depth + 1 < max_depth and _host(link) != _host(url):
                # 只遞迴「跨平台」的社群連結；同站連結是導覽/相關貼文雜訊
                process(link, url, body, depth + 1)
        # 貼文沒挖到論文/GitHub 連結 → 標題備援搜尋
        if found == 0 and body:
            title = _guess_paper_title(
                body, timeout=cfg["resolve"].get("title_guess_timeout_seconds", 45))
            if title and (hit := _search_arxiv_by_title(title)):
                add_resource(hit, "paper", url, body)
                found += 1
        if found == 0:
            if body:
                # 有內文但沒有學術資源：當一般新聞素材保留
                add_resource(url, "news", via, body)
            else:
                unresolved.append({"url": url, "via": via, "reason": "could not expand"})

    try:
        total = len(items)
        for index, item in enumerate(items, 1):
            log.info("resolving link %d/%d: %s", index, total, item["url"])
            if progress:
                progress(index - 1, total, item["url"], False)
            process(item["url"], item["url"], item.get("text", ""), 0)
            log.info("resolved link %d/%d", index, total)
            if progress:
                progress(index, total, item["url"], True)
    finally:
        close_browser()

    log.info("resolved: %d resources, %d unresolved", len(resources), len(unresolved))
    return {"resources": resources, "unresolved": unresolved}
