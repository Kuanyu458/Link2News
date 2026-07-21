"""四份報告生成：名詞說明、文獻摘要、GitHub 導入發想（news 併入摘要報告附錄）。"""
import logging
from pathlib import Path

from llm import ask

log = logging.getLogger("weekly.generate")


def gen_terms_report(selected_terms: list[dict], wk: str) -> str:
    if not selected_terms:
        return f"# 尚未理解的名詞說明報告（{wk}）\n\n本週沒有勾選任何術語。\n"
    term_list = "\n".join(f"- {t['term']}（出現脈絡：{t.get('context','')}）"
                          for t in selected_terms)
    prompt = (
        f"為以下術語各寫一則解說，讀者是有軟體/AI 背景但不熟悉這些特定概念的工程師。"
        f"每則包含：\n"
        f"1. **定義**（2-3 句，精確）\n"
        f"2. **白話類比**（1-2 句，幫助直覺理解）\n"
        f"3. **在本週素材中的角色**（根據附的出現脈絡）\n"
        f"不確定的內容請先搜尋查證。用繁體中文，Markdown 格式，"
        f"每個術語一個 `## 術語名` 小節。\n\n{term_list}"
    )
    body = ask(prompt, allow_web_search=True, max_tokens=16000)
    return f"# 尚未理解的名詞說明報告（{wk}）\n\n{body}\n"


def gen_papers_report(papers: list[dict], news: list[dict], wk: str, progress=None) -> str:
    if not papers and not news:
        return f"# 本週學術文獻摘要報告（{wk}）\n\n本週沒有收集到學術文獻。\n"
    sections = []
    paper_total = len(papers)
    for index, p in enumerate(papers):
        material = (f"標題：{p['title']}\n作者：{', '.join(p['authors'])}\n"
                    f"連結：{p['url']}\n摘要：{p['abstract']}\n\n全文（截斷）：\n{p['full_text'][:40000]}")
        prompt = (
            "為這篇論文寫結構化摘要，繁體中文 Markdown（專有名詞保留英文），格式：\n"
            "### 標題（原文標題）\n"
            "**作者/連結**（一行）\n"
            "**研究問題**：這篇論文想解決什麼（2-3 句）\n"
            "**方法**：核心做法（3-5 句）\n"
            "**關鍵結果**：務必含具體數字（3-5 點）\n"
            "**限制**：作者承認或你觀察到的（1-3 點）\n"
            "**與我的專案的潛在關聯**：可能的應用方向（2-3 句）\n\n" + material
        )
        try:
            sections.append(ask(prompt, max_tokens=4000))
        except Exception as e:
            log.error("summary failed for %s: %s", p["url"], e)
            sections.append(f"### {p['title']}\n（摘要生成失敗：{e}）\n{p['url']}")
        if progress:
            progress(index + 1, paper_total, "paper", p.get("title") or p["url"])
    report = f"# 本週學術文獻摘要報告（{wk}）\n\n" + "\n\n---\n\n".join(sections)
    if news:
        report += "\n\n---\n\n## 附錄：本週其他文章\n\n"
        news_total = len(news)
        for index, n in enumerate(news):
            digest = ""
            if n.get("full_text"):
                try:
                    digest = ask(f"用 2-3 句繁體中文摘要這篇文章的要點：\n\n{n['full_text'][:10000]}",
                                 max_tokens=500)
                except Exception:
                    digest = ""
            report += f"- **{n.get('title') or n['url']}**（{n['url']}）\n  {digest}\n"
            if progress:
                progress(index + 1, news_total, "news", n.get("title") or n["url"])
    return report + "\n"


def gen_github_report(repos: list[dict], wk: str, project_context: str = "") -> str:
    if not repos:
        return f"# 本週 GitHub 專案導入發想（{wk}）\n\n本週沒有收集到 GitHub 專案。\n"
    sections = []
    for r in repos:
        material = (f"Repo：{r['owner']}/{r['repo']}（⭐ {r['stars']}，主要語言 {r['language']}）\n"
                    f"描述：{r['description']}\nTopics：{', '.join(r['topics'])}\n"
                    f"README（截斷）：\n{r['readme'][:12000]}")
        ctx = f"\n\n我目前的專案背景：{project_context}" if project_context else ""
        prompt = (
            "分析這個 GitHub 專案，繁體中文 Markdown，格式：\n"
            f"### {r['owner']}/{r['repo']}\n"
            "**這是什麼**：一句話定位 + 2-3 句說明\n"
            "**技術亮點**：架構或實作上值得學的點（2-4 點）\n"
            "**導入發想**：具體說明可以怎麼用在我的專案或工作流中，"
            "給出至少一個可執行的整合構想（3-5 句）\n" + ctx + "\n\n" + material
        )
        try:
            sections.append(ask(prompt, max_tokens=3000))
        except Exception as e:
            log.error("github analysis failed for %s: %s", r["url"], e)
            sections.append(f"### {r['owner']}/{r['repo']}\n（分析失敗：{e}）\n{r['url']}")
    return (f"# 本週 GitHub 專案導入發想（{wk}）\n\n"
            + "\n\n---\n\n".join(sections) + "\n")


def write_reports(out_dir: Path, reports: dict[str, str]) -> None:
    for name, content in reports.items():
        (out_dir / name).write_text(content, encoding="utf-8")
        log.info("wrote %s", out_dir / name)
