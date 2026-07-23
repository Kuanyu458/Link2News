"""從 collector 拉取本週連結並做 URL 分類。"""
import logging
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

try:
    from .common import collector_get, week_start_ms
except ImportError:  # direct ``python pipeline/main.py`` compatibility
    from common import collector_get, week_start_ms

log = logging.getLogger("weekly.fetch")

# 同時支援新式 (2402.17764) 與舊式 (cond-mat/0309395) arXiv ID
ARXIV_RE = re.compile(
    r"arxiv\.org/(abs|pdf|html)/((?:[a-z-]+(?:\.[A-Z]{2})?/\d{7})|(?:\d{4}\.\d{4,5}))(v\d+)?", re.I)
DOI_RE = re.compile(r"(doi\.org/|dx\.doi\.org/)(10\.\d{4,9}/\S+)", re.I)
GITHUB_RE = re.compile(r"github\.com/([\w.\-]+)/([\w.\-]+)", re.I)

PAPER_HOST_HINTS = (
    "arxiv.org", "biorxiv.org", "medrxiv.org", "openreview.net", "aclanthology.org",
    "nature.com", "science.org", "sciencedirect.com", "springer.com", "ieee.org",
    "acm.org", "semanticscholar.org", "pubmed.ncbi.nlm.nih.gov", "cell.com",
    "pnas.org", "jmlr.org", "proceedings.mlr.press", "huggingface.co/papers",
)

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                   "utm_content", "fbclid", "gclid", "igshid", "s", "t", "si"}


def normalize_url(url: str) -> str:
    """去除追蹤參數與尾端雜訊。"""
    url = url.rstrip(".,;!?…。，")
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if k not in TRACKING_PARAMS]
    return urlunparse(p._replace(query=urlencode(q), fragment=""))


# github.com 下不是「使用者/組織」的保留路徑（產品頁、行銷頁）
GITHUB_NON_OWNER = {
    "topics", "trending", "collections", "features", "orgs", "security",
    "enterprise", "solutions", "about", "pricing", "resources", "marketplace",
    "sponsors", "customer-stories", "blog", "events", "contact", "premium-support",
    "team", "mobile", "git-guides", "readme", "education", "apps", "settings",
    "login", "join", "signup", "explore", "new", "notifications", "codespaces",
}


def canonicalize(url: str) -> str:
    """同一資源的不同形式收斂為單一 URL：
    arXiv abs/pdf/html、HF papers → arXiv abs；GitHub repo 深層路徑 → repo 首頁。"""
    m = ARXIV_RE.search(url)
    if m:
        return f"https://arxiv.org/abs/{m.group(2)}"
    m = re.search(r"huggingface\.co/papers/(\d{4}\.\d{4,5})", url, re.I)
    if m:
        return f"https://arxiv.org/abs/{m.group(1)}"
    m = GITHUB_RE.search(url)
    if m and (urlparse(url).netloc or "").lower().removeprefix("www.") == "github.com":
        owner, repo = m.group(1), m.group(2).removesuffix(".git")
        if owner.lower() not in GITHUB_NON_OWNER:
            return f"https://github.com/{owner}/{repo}"
    return url


def classify(url: str, social_domains: list[str]) -> str:
    """回傳 'paper' | 'github' | 'social' | 'news'。"""
    host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    if ARXIV_RE.search(url) or DOI_RE.search(url):
        return "paper"
    if any(h in url.lower() for h in PAPER_HOST_HINTS):
        return "paper"
    m = GITHUB_RE.search(url)
    if m and host == "github.com":
        # github.com/owner/repo 之外的（產品頁、行銷頁）當一般連結
        if m.group(1).lower() not in GITHUB_NON_OWNER:
            return "github"
    if any(host == d.removeprefix("www.") or host.endswith("." + d.removeprefix("www."))
           for d in social_domains):
        return "social"
    return "news"


def fetch_week_links(cfg: dict, secrets: dict) -> list[dict]:
    """拉取本週連結，正規化 + 去重，回傳 [{url, text, ts}]。"""
    rows = collector_get(
        cfg, secrets, "/api/v1/links",
        {
            "since": week_start_ms(),
            "source_id": cfg.get("line", {}).get("push_to", ""),
        },
    )
    seen, items = set(), []
    for row in rows:
        url = normalize_url(row["url"])
        if url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "text": row.get("message_text") or "", "ts": row.get("line_timestamp")})
    log.info("fetched %d unique links this week", len(items))
    return items
