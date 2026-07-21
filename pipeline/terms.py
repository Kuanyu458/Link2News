"""術語流程 v2：抽取 → 比對詞庫 → 自動選出前 5 個最廣泛使用的術語。

v1 的 LINE 勾選流程已移除；known_terms.json 只記錄「已解釋過」的術語，
未被選中的術語之後幾週仍有機會入選。
"""
import logging

from common import PROJECT_ROOT, load_json, save_json
from llm import ask_json

log = logging.getLogger("weekly.terms")
KNOWN_TERMS_PATH = PROJECT_ROOT / "data" / "known_terms.json"


def extract_terms(ingested: dict) -> list[dict]:
    """從本週所有內容抽出專業術語（排除詞庫已解釋過的）。"""
    known = {t["term"].lower() for t in load_json(KNOWN_TERMS_PATH, [])}
    corpus = []
    for p in ingested["papers"]:
        corpus.append(f"[論文] {p['title']}\n{p['abstract']}\n{p['full_text'][:8000]}")
    for r in ingested["repos"]:
        corpus.append(f"[GitHub] {r['owner']}/{r['repo']}: {r['description']}\n{r['readme'][:5000]}")
    for n in ingested["news"]:
        corpus.append(f"[新聞] {n['title']}\n{n['full_text'][:5000]}")
    if not corpus:
        return []

    prompt = (
        "以下是本週閱讀素材。抽出其中出現的專業術語（技術名詞、模型/演算法名、"
        "領域概念、縮寫），每個術語附一句它在素材中的出現脈絡。規則：\n"
        "- 只收具體的專業概念，不收一般詞彙\n"
        "- 同義/縮寫合併為一項（如 RAG 與 Retrieval-Augmented Generation）\n"
        "- 最多 40 項，依重要性排序\n"
        '輸出 JSON 陣列：[{"term": "...", "context": "..."}]\n\n'
        + "\n\n---\n\n".join(corpus)[:80000]
    )
    terms = ask_json(prompt, max_tokens=8000)
    fresh = [t for t in terms if t["term"].lower() not in known]
    log.info("extracted %d terms (%d new)", len(terms), len(fresh))
    return fresh


def auto_select_terms(candidates: list[dict], cfg: dict, top_n: int = 5) -> list[dict]:
    """自動選出前 top_n 個「最廣泛使用、讀者遲早會再遇到」的術語。"""
    if len(candidates) <= top_n:
        return candidates
    listing = "\n".join(f"{i}. {t['term']}（脈絡：{t.get('context', '')[:80]}）"
                        for i, t in enumerate(candidates))
    prompt = (
        f"讀者背景：{cfg.get('project_context', 'AI 工程師')}。\n"
        f"從以下候選術語中選出 {top_n} 個「在 AI/ML 領域最廣泛被使用」的術語。判斷標準：\n"
        "- 優先選業界通用、跨論文出現、讀者之後一定會再遇到的概念\n"
        "- 不選單一論文自創的系統名或 benchmark 名（除非已成領域共通語彙）\n"
        "- 不選讀者背景大概率已熟知的入門概念\n"
        f'輸出 JSON：{{"selected": [索引數字×{top_n}]}}\n\n' + listing
    )
    try:
        result = ask_json(prompt, max_tokens=300)
        idx = [i for i in result["selected"]
               if isinstance(i, int) and 0 <= i < len(candidates)][:top_n]
        if idx:
            picked = [candidates[i] for i in idx]
            log.info("auto-selected terms: %s", [t["term"] for t in picked])
            return picked
    except Exception as e:
        log.warning("term auto-select failed (%s); using first %d", e, top_n)
    return candidates[:top_n]


def record_explained(terms: list[dict]) -> None:
    """把已解釋過的術語記入詞庫，之後不再重複介紹。"""
    known = load_json(KNOWN_TERMS_PATH, [])
    existing = {t["term"].lower() for t in known}
    for t in terms:
        if t["term"].lower() not in existing:
            known.append({"term": t["term"], "explained": True})
    save_json(KNOWN_TERMS_PATH, known)
