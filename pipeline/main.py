"""週報 pipeline 入口 v2 —— 週日 09:00 單次執行，全程無人工互動。

流程：收集連結 → 解析 → 抓內容 → 文獻策展（引用數/選文/編號/文獻庫歸檔）
     → 自動選 5 個術語 → 生成報告 → GitHub star → podcast → 報紙 PDF → LINE 通知

用法：
  python main.py                    # 完整流程（預設 --all）
  python main.py --collect          # 只跑收集（除錯用，同 --stage 1）
  python main.py --generate         # 只跑生成（除錯用，同 --stage 2）
  python main.py --all --dry-run    # 測試連結集，不推 LINE、不 star
  python main.py --generate --week 2026-W28   # 指定週補跑
  python main.py --rerender --week 2026-W28   # 只用既有資料重排 HTML/PDF
"""
import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

# Keep sibling imports working both when invoked as a file and through the
# installed `weekly-report` console entry point.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from .common import (load_config, load_secrets, setup_logging, week_key,
                         output_dir, save_json, load_json, line_push,
                         line_push_messages)
    from .adapters import (
        Artifact,
        CollectRequest,
        DeliveryEvent,
        deduplicate_external_ids,
        load_delivery_adapter,
        load_source_adapter,
    )
except ImportError:  # direct ``python pipeline/main.py`` compatibility
    from common import (load_config, load_secrets, setup_logging, week_key,
                        output_dir, save_json, load_json, line_push,
                        line_push_messages)
    from adapters import (
        Artifact,
        CollectRequest,
        DeliveryEvent,
        deduplicate_external_ids,
        load_delivery_adapter,
        load_source_adapter,
    )

log = logging.getLogger("weekly.main")

TEST_LINKS = [
    {"url": "https://arxiv.org/abs/1706.03762", "text": "經典必讀", "ts": 0},
    {"url": "https://github.com/karpathy/nanoGPT", "text": "小而美的 GPT 訓練庫", "ts": 0},
    {"url": "https://news.ycombinator.com/item?id=39535800", "text": "HN 討論串測試", "ts": 0},
]


def _push_job_status(cfg, secrets, job_id, status, phase, error="",
                     progress_done=None, progress_total=None,
                     delivery_status=None, artifacts=None):
    """回報進度到 collector；回報失敗不應中斷主流程。"""
    if not job_id:
        return
    import requests
    try:
        base = cfg["collector"]["base_url"].rstrip("/")
        payload = {"id": job_id, "status": status, "phase": phase, "error": error}
        if progress_done is not None:
            payload["progress_done"] = progress_done
        if progress_total is not None:
            payload["progress_total"] = progress_total
        if delivery_status is not None:
            payload["delivery_status"] = delivery_status
        if artifacts is not None:
            payload["artifacts"] = artifacts
        requests.post(
            f"{base}/job-status",
            json=payload,
            headers={"X-Api-Secret": secrets.get("COLLECTOR_API_SECRET", "")},
            timeout=20,
        ).raise_for_status()
    except Exception as exc:
        log.warning("job status push failed (non-fatal): %s", exc)


class JobProgress:
    """可更新的工作進度，並定期重送目前狀態避免長模型呼叫被判定停滯。"""

    def __init__(self, cfg, secrets, job_id, phase, done=None, total=None,
                 heartbeat_seconds=120):
        self.cfg, self.secrets, self.job_id = cfg, secrets, job_id
        self.phase, self.done, self.total = phase, done, total
        self.heartbeat_seconds = heartbeat_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _snapshot(self):
        with self._lock:
            return self.phase, self.done, self.total

    def push(self):
        phase, done, total = self._snapshot()
        _push_job_status(self.cfg, self.secrets, self.job_id, "running", phase,
                         progress_done=done, progress_total=total)

    def update(self, phase, done=None, total=None):
        with self._lock:
            self.phase = phase
            if done is not None:
                self.done = done
            if total is not None:
                self.total = total
        self.push()

    def __enter__(self):
        self.push()
        if self.job_id:
            def heartbeat():
                while not self._stop.wait(self.heartbeat_seconds):
                    self.push()
            self._thread = threading.Thread(target=heartbeat, daemon=True,
                                            name="weekly-progress-heartbeat")
            self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _podcast_duration_ms(path: Path) -> int:
    """讀取 MP3 長度；失敗時回 0，完成卡仍保留下載按鈕。"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return max(0, round(float(result.stdout.strip()) * 1000))
    except Exception as exc:
        log.warning("cannot read podcast duration; inline audio disabled: %s", exc)
        return 0


def _upload_artifact(cfg, secrets, wk: str, kind: str, path: Path,
                     duration_ms: int = 0) -> dict:
    """把單一產物串流至 collector/R2，含完整性驗證與有限重試。"""
    import requests
    content_type = "application/pdf" if kind == "report" else "audio/mpeg"
    size = path.stat().st_size
    sha256 = _file_sha256(path)
    url = f"{cfg['collector']['base_url'].rstrip('/')}/artifacts/{wk}/{kind}"
    headers = {
        "X-Api-Secret": secrets.get("COLLECTOR_API_SECRET", ""),
        "Content-Type": content_type,
        "Content-Length": str(size),
        "X-Content-SHA256": sha256,
    }
    if duration_ms > 0:
        headers["X-Duration-Ms"] = str(duration_ms)
    last_error = None
    for attempt in range(1, 4):
        try:
            with path.open("rb") as source:
                response = requests.put(url, data=source, headers=headers, timeout=(30, 300))
            response.raise_for_status()
            payload = response.json()
            artifact = payload.get("artifact") or {}
            if not artifact.get("url"):
                raise RuntimeError("collector response missing artifact URL")
            log.info("artifact published: %s (%d bytes)", kind, size)
            return artifact
        except Exception as exc:
            last_error = exc
            log.warning("artifact upload %s attempt %d/3 failed: %s", kind, attempt, exc)
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{kind} upload failed after 3 attempts: {last_error}")


def _completion_messages(wk: str, layout: dict, artifacts: dict) -> list[dict]:
    report = artifacts["report"]
    podcast = artifacts.get("podcast")
    headlines = [str(item.get("headline", "")).strip()
                 for item in layout.get("focus", []) if item.get("headline")]
    expiry_ms = min(a.get("expiresAt", 0) for a in artifacts.values())
    expiry = time.strftime("%Y/%m/%d", time.localtime(expiry_ms / 1000)) if expiry_ms else "90 天後"
    body_contents = [
        {"type": "text", "text": f"{wk} 週報完成", "weight": "bold", "size": "xl"},
        {"type": "text", "text": "手機版已就緒", "color": "#2E7D32", "size": "sm",
         "margin": "sm"},
    ]
    if headlines:
        body_contents.append({
            "type": "text", "text": "\n".join(f"• {title}" for title in headlines[:3]),
            "wrap": True, "size": "sm", "margin": "lg", "color": "#444444",
        })
    body_contents.append({
        "type": "text", "text": f"私密連結有效至 {expiry}", "size": "xs",
        "margin": "lg", "color": "#888888",
    })
    buttons = [{
        "type": "button", "style": "primary", "color": "#1F6F5C",
        "action": {"type": "uri", "label": "閱讀 PDF", "uri": report["url"]},
    }]
    if podcast:
        buttons.append({
            "type": "button", "style": "secondary", "margin": "sm",
            "action": {"type": "uri", "label": "下載 Podcast", "uri": podcast["url"]},
        })
    messages = [{
        "type": "flex", "altText": f"{wk} 週報已完成，可閱讀 PDF 與收聽 Podcast",
        "contents": {
            "type": "bubble",
            "body": {"type": "box", "layout": "vertical", "contents": body_contents},
            "footer": {"type": "box", "layout": "vertical", "spacing": "sm",
                       "contents": buttons},
        },
    }]
    if podcast and int(podcast.get("durationMs") or 0) > 0:
        messages.append({
            "type": "audio", "originalContentUrl": podcast["url"],
            "duration": int(podcast["durationMs"]),
        })
    return messages


def publish_artifacts(cfg, secrets, wk: str, pdf_path: Path,
                      podcast_path: Path | None, layout: dict,
                      summary_text: str = "") -> dict:
    """發布 PDF/Podcast，並推送可在手機直接閱讀與播放的 LINE 訊息。"""
    if not pdf_path.exists():
        raise RuntimeError(f"PDF 不存在：{pdf_path}")
    artifacts = {"report": _upload_artifact(cfg, secrets, wk, "report", pdf_path)}
    if podcast_path and podcast_path.exists():
        duration_ms = _podcast_duration_ms(podcast_path)
        artifacts["podcast"] = _upload_artifact(
            cfg, secrets, wk, "podcast", podcast_path, duration_ms)

    messages = _completion_messages(wk, layout, artifacts)
    if summary_text:
        messages.append({"type": "text", "text": summary_text[:4900]})
    if not line_push_messages(secrets, cfg["line"]["push_to"], messages):
        fallback = (f"✅ 本週週報完成（{wk}）\n"
                    f"📄 閱讀 PDF：{artifacts['report']['url']}")
        if artifacts.get("podcast"):
            fallback += f"\n🎧 收聽 Podcast：{artifacts['podcast']['url']}"
        if not line_push(secrets, cfg["line"]["push_to"], fallback):
            log.error("both rich and fallback LINE completion notifications failed")
    return artifacts


def publish_existing(cfg, secrets, wk: str, job_id: str = "") -> dict:
    """補送指定週既有產物，不執行 LLM、TTS 或排版。"""
    out = output_dir(cfg, wk)
    pdf_path = out / f"weekly_{wk}.pdf"
    podcast_path = out / "podcast.mp3"
    layout = load_json(out / "layout.json", {})
    ingested = load_json(out / "ingested.json", {})
    _push_job_status(cfg, secrets, job_id, "running", "重新發布手機閱讀檔案")
    artifacts = publish_artifacts(
        cfg, secrets, wk, pdf_path,
        podcast_path if podcast_path.exists() else None, layout,
        f"📦 已補送 {wk} 手機閱讀檔案。",
    )
    _push_snapshot(
        cfg, secrets, wk, layout, ingested.get("unresolved", []),
        int(ingested.get("source_link_count") or 0), artifacts, "ready")
    _push_job_status(
        cfg, secrets, job_id, "completed", f"{wk} 手機閱讀檔案已補送",
        delivery_status="ready", artifacts=artifacts)
    return artifacts


def rerender_existing(cfg: dict, wk: str) -> Path:
    """只重排既有週報，不呼叫 LLM、TTS、LINE、Collector 或 GitHub。"""
    from newspaper import render_newspaper

    out = output_dir(cfg, wk)
    required_json = {
        "ingested": out / "ingested.json",
        "citations": out / "citations.json",
        "layout": out / "layout.json",
    }
    missing = [path.name for path in required_json.values() if not path.exists()]
    if missing:
        raise RuntimeError(f"{wk} 無法重排，缺少：{', '.join(missing)}")

    reports = {
        "terms": (out / "1_名詞說明報告.md").read_text(encoding="utf-8")
        if (out / "1_名詞說明報告.md").exists() else "",
        "papers": (out / "2_文獻摘要報告.md").read_text(encoding="utf-8")
        if (out / "2_文獻摘要報告.md").exists() else "",
        "github": (out / "4_GitHub導入發想.md").read_text(encoding="utf-8")
        if (out / "4_GitHub導入發想.md").exists() else "",
    }
    ingested = load_json(required_json["ingested"], {})
    citations = load_json(required_json["citations"], {})
    layout = load_json(required_json["layout"], {})
    podcast_path = out / "podcast.mp3"
    pdf_path = render_newspaper(
        reports, ingested, citations, cfg, out, wk,
        podcast_path if podcast_path.exists() else None,
        layout_override=layout,
    )
    log.info("rerender complete without publishing: %s", pdf_path)
    return pdf_path


def collect(cfg, secrets, wk, dry_run=False, job_id="", source_adapter=None) -> dict | None:
    """收集：拉連結 → 解析 → 抓內容。回傳 ingested（無連結時回 None）。"""
    from fetch import fetch_week_links
    from resolve import resolve_links
    from ingest import ingest_all

    out = output_dir(cfg, wk)
    with JobProgress(cfg, secrets, job_id, "收集本週 LINE 連結") as progress:
        if dry_run:
            links = TEST_LINKS
        elif source_adapter is None:
            links = fetch_week_links(cfg, secrets)
        else:
            links = [
                item.as_pipeline_item()
                for item in deduplicate_external_ids(
                    source_adapter.collect(CollectRequest(week=wk, mode="all")))
            ]
        if not links:
            log.info("no links this week")
            if not dry_run:
                line_push(secrets, cfg["line"]["push_to"], f"📭 本週（{wk}）聊天室沒有收集到任何連結。")
            return None

        source_total = len(links)

        def report_resolve(done, total, url, completed):
            host = (urlparse(url).netloc or url)[:60]
            if completed:
                phase = f"解析連結：已完成 {done}/{total}"
            else:
                phase = f"解析連結：已完成 {done}/{total}，正在處理 {done + 1}/{total} ({host})"
            progress.update(phase, done, total)

        progress.update(f"解析連結：已完成 0/{source_total}", 0, source_total)
        resolved = resolve_links(links, cfg, progress=report_resolve)

        def report_ingest(done, total, resource, completed):
            kind = {"paper": "文獻", "github": "GitHub", "news": "文章"}.get(
                resource.get("kind"), "素材")
            action = "已完成" if completed else "正在擷取"
            progress.update(
                f"內容擷取：{action} {done}/{total}（{kind}）｜連結解析 {source_total}/{source_total}",
                source_total, source_total)

        progress.update(
            f"下載與擷取內容：0/{len(resolved['resources'])}｜連結解析 {source_total}/{source_total}",
            source_total, source_total)
        ingested = ingest_all(resolved, out / "assets", secrets, progress=report_ingest)
        ingested["source_link_count"] = source_total
        save_json(out / "ingested.json", ingested)
        return ingested


def generate(cfg, secrets, wk, dry_run=False, job_id="", reuse_previous=False,
             delivery_adapter=None, delivery_name="line"):
    """生成：策展 → 選詞 → 報告 → star → podcast → 報紙 → 通知。"""
    from curate import curate
    from terms import extract_terms, auto_select_terms, record_explained
    from generate import gen_terms_report, gen_papers_report, gen_github_report, write_reports
    from github_star import star_repos
    from podcast import make_podcast
    from newspaper import render_newspaper

    out = output_dir(cfg, wk)
    ingested = load_json(out / "ingested.json")
    if ingested is None:
        raise RuntimeError(f"找不到 {out}/ingested.json — 請先跑 --collect")

    source_total = int(ingested.get("source_link_count") or
                       (len(ingested.get("papers", [])) + len(ingested.get("repos", [])) +
                        len(ingested.get("news", [])) + len(ingested.get("unresolved", []))))
    initial_phase = "載入上次週報素材" if reuse_previous else "文獻策展與引用整理"
    with JobProgress(cfg, secrets, job_id, initial_phase,
                     source_total, source_total) as progress:
        # 重新生成沿用上次引用編號與文獻庫，不重抓引用數或搬動原始檔。
        citations = load_json(out / "citations.json") if reuse_previous else None
        if citations is None:
            citations = curate(ingested, cfg, out, secrets)
            save_json(out / "ingested.json", ingested)  # 回存 ref/featured/citations 欄位

        # 自動選 5 個最廣泛使用的新術語
        progress.update("選擇本週關鍵術語")
        candidates = load_json(out / "terms_candidates.json") if reuse_previous else None
        if candidates is None:
            candidates = extract_terms(ingested)
            save_json(out / "terms_candidates.json", candidates)
        selected = auto_select_terms(candidates, cfg)

        progress.update("報告撰寫 1/3：名詞說明")
        terms_report = gen_terms_report(selected, wk)

        def report_paper(done, total, kind, title):
            label = "文獻摘要" if kind == "paper" else "文章摘要"
            progress.update(f"報告撰寫 2/3：{label} {done}/{total}｜{title[:42]}")

        progress.update(f"報告撰寫 2/3：文獻摘要 0/{len(ingested['papers'])}")
        papers_report = gen_papers_report(ingested["papers"], ingested["news"], wk,
                                          progress=report_paper)
        progress.update("報告撰寫 3/3：GitHub 導入發想")
        github_report = gen_github_report(
            ingested["repos"], wk, project_context=cfg.get("project_context", ""))
        reports = {"terms": terms_report, "papers": papers_report,
                   "github": github_report}
        record_explained(selected)
        write_reports(out, {
            "1_名詞說明報告.md": reports["terms"],
            "2_文獻摘要報告.md": reports["papers"],
            "4_GitHub導入發想.md": reports["github"],
        })

        progress.update("產生 Podcast：建立訪談腳本")
        starred = [] if (dry_run or reuse_previous) else star_repos(ingested["repos"], secrets)
        podcast_path = make_podcast(
            reports["papers"], cfg, out, wk,
            progress=lambda phase: progress.update(phase))
        progress.update("產生 A4 直向雙欄 PDF 與自適應 HTML")
        pdf_path = render_newspaper(reports, ingested, citations, cfg, out, wk, podcast_path)

    layout = load_json(out / "layout.json", {})
    focus_lines = "\n".join(f"  • {f['headline']}" for f in layout.get("focus", []))
    msg = (f"✅ 本週週報完成（{wk}）\n"
           f"🔍 本週焦點：\n{focus_lines}\n"
           f"📄 報紙：{pdf_path.name}\n"
           f"🎧 Podcast：{podcast_path.name if podcast_path else '無'}\n"
           f"⭐ 已 star：{', '.join(starred) if starred else '無'}\n"
           f"📚 文獻庫：{citations.get('library_dir', '')}\n"
           f"📁 {out}")
    unresolved = ingested.get("unresolved", [])
    if unresolved:
        msg += f"\n⚠️ {len(unresolved)} 條連結無法自動解析，請手動處理：\n" + \
               "\n".join(f"- {u['url']}" for u in unresolved[:8])
    result_phase = f"{wk} 週報已重新生成" if reuse_previous else f"{wk} 週報已完成"
    delivery_status = None
    artifacts = None
    if dry_run:
        print("\n" + msg)
    else:
        phase = ("上傳 PDF 與 Podcast 到手機閱讀庫"
                 if delivery_name == "line" else "整理本機輸出檔案")
        _push_job_status(
            cfg, secrets, job_id, "running", phase,
            progress_done=source_total, progress_total=source_total)
        try:
            if delivery_adapter is None:
                delivery_adapter = load_delivery_adapter(
                    delivery_name, cfg, secrets)
            event_artifacts = [
                Artifact.from_path("pdf", pdf_path, "application/pdf"),
            ]
            html_path = out / "newspaper.html"
            if html_path.exists():
                event_artifacts.append(
                    Artifact.from_path("html", html_path, "text/html"))
            if podcast_path and podcast_path.exists():
                event_artifacts.append(Artifact.from_path(
                    "audio", podcast_path, "audio/mpeg",
                    _podcast_duration_ms(podcast_path)))
            receipt = delivery_adapter.publish(DeliveryEvent(
                status="completed",
                phase=result_phase,
                week=wk,
                progress_done=source_total,
                progress_total=source_total,
                summary=msg,
                artifacts=tuple(event_artifacts),
                metadata={"layout": layout, "unresolved": unresolved},
            ))
            if not receipt.ok:
                raise RuntimeError(receipt.error or f"{delivery_name} delivery failed")
            artifacts = dict(receipt.items)
            delivery_status = "ready"
            if delivery_name == "line":
                _push_snapshot(
                    cfg, secrets, wk, layout, unresolved, source_total,
                    artifacts, delivery_status)
            elif receipt.items:
                print(json.dumps({
                    "week": wk,
                    "delivery": delivery_name,
                    "artifacts": receipt.items,
                }, ensure_ascii=False, indent=2))
        except Exception as exc:
            delivery_status = "failed"
            log.error("%s delivery failed:\n%s", delivery_name, traceback.format_exc())
            if delivery_name == "line":
                line_push(
                    secrets, cfg["line"]["push_to"],
                    f"⚠️ {wk} 週報已在 Mac 生成完成，但手機檔案發布失敗。\n"
                    f"原因：{str(exc)[:500]}\n"
                    f"可稍後執行：python pipeline/main.py --publish --week {wk}")
                _push_snapshot(
                    cfg, secrets, wk, layout, unresolved, source_total,
                    {}, delivery_status)
            else:
                print(json.dumps({
                    "week": wk,
                    "delivery": delivery_name,
                    "delivery_status": "failed",
                    "generation_status": "completed",
                    "error": str(exc)[:500],
                    "output": str(out.resolve()),
                }, ensure_ascii=False, indent=2))
            result_phase += "，內容生成完成但交付失敗"
    _push_job_status(
        cfg, secrets, job_id, "completed", result_phase,
        progress_done=source_total, progress_total=source_total,
        delivery_status=delivery_status, artifacts=artifacts)
    log.info("generate complete")


def _push_snapshot(cfg, secrets, wk, layout, unresolved, link_count=0,
                   artifacts=None, delivery_status=None):
    """把本週摘要（術語＋待處理清單）同步到 collector，供 LINE 選單按鈕即時查詢。"""
    import requests
    try:
        base = cfg["collector"]["base_url"].rstrip("/")
        snap = {
            "week": wk,
            "linkCount": link_count,
            "terms": [{"term": t.get("term", ""), "blurb": t.get("blurb", "")}
                      for t in layout.get("terms", [])],
            "unresolved": [u["url"] for u in unresolved][:20],
            "artifacts": artifacts or {},
            "deliveryStatus": delivery_status,
        }
        requests.post(f"{base}/snapshot", json=snap,
                      headers={"X-Api-Secret": secrets.get("COLLECTOR_API_SECRET", "")},
                      timeout=30).raise_for_status()
        log.info("snapshot pushed to collector")
    except Exception as e:
        log.warning("snapshot push failed (non-fatal): %s", e)


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="完整流程（預設）")
    mode.add_argument("--collect", action="store_true", help="只跑收集")
    mode.add_argument("--generate", action="store_true", help="只跑生成")
    mode.add_argument("--regenerate", action="store_true",
                      help="沿用指定週已解析素材，重新生成所有報告與媒體")
    mode.add_argument("--rerender", action="store_true",
                      help="只用指定週既有 layout/素材重排 HTML 與 PDF，不呼叫模型或發布")
    mode.add_argument("--publish", action="store_true",
                      help="只發布指定週既有 PDF/Podcast 到 LINE 手機閱讀連結")
    mode.add_argument("--stage", type=int, choices=[1, 2], help="v1 相容：1=collect 2=generate")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--week", help="指定週期鍵（預設本週），如 2026-W28")
    ap.add_argument("--llm", help="覆寫生成模型（backend:model），如 claude-cli:claude-opus-4-8")
    ap.add_argument("--job-id", default="", help="Collector 生成工作 ID（LINE 進度回報用）")
    ap.add_argument("--input", help="URL 文字檔；使用 - 可從 stdin 讀取")
    ap.add_argument("--output", help="覆寫產物輸出資料夾")
    ap.add_argument("--source-adapter", help="輸入 adapter 名稱")
    ap.add_argument("--delivery-adapter", help="交付 adapter 名稱")
    args = ap.parse_args()

    cfg = load_config()
    secrets = load_secrets()
    if args.output:
        cfg["output_dir"] = str(Path(args.output).expanduser().resolve())

    source_name = args.source_adapter or (
        "file" if args.input else cfg.get("source", {}).get("adapter", "collector"))
    delivery_name = args.delivery_adapter or (
        "local" if args.input else cfg.get("delivery", {}).get("adapter", "line"))
    source_options = {"path": args.input or "-"}
    source_adapter = load_source_adapter(
        source_name, cfg, secrets, source_options)
    delivery_adapter = load_delivery_adapter(
        delivery_name, cfg, secrets)

    # LINE 選單「指定生成模型」的覆寫
    if args.llm:
        backend, _, model = args.llm.partition(":")
        if backend:
            cfg["llm"]["backend"] = backend
        if model:
            key = "codex_model" if backend == "codex-cli" else "claude_model"
            cfg["llm"][key] = model
        import llm as llm_mod
        llm_mod._cfg = cfg  # 讓 llm 模組使用覆寫後的設定
    wk = args.week or week_key()

    if args.dry_run:
        selected = (
            "collect" if args.collect or args.stage == 1 else
            "generate" if args.generate or args.stage == 2 else
            "regenerate" if args.regenerate else
            "rerender" if args.rerender else
            "publish" if args.publish else "all"
        )
        print(json.dumps({
            "dry_run": True,
            "mode": selected,
            "week": wk,
            "llm_backend": cfg.get("llm", {}).get("backend"),
            "collector": cfg.get("collector", {}).get("base_url"),
            "source_adapter": source_name,
            "delivery_adapter": delivery_name,
            "input": args.input,
            "output": cfg.get("output_dir"),
            "external_writes": [],
            "note": "No LINE, D1, R2, GitHub, LLM, TTS, or output write was performed.",
        }, ensure_ascii=False, indent=2))
        return 0

    setup_logging(wk)

    # 背景 runner 會在 claim 雲端工作前先持有這把鎖；手動執行
    # 則仍在此處取鎖。
    lock_file = None
    if os.environ.get("WEEKLY_REPORT_LOCK_HELD") != "1":
        import fcntl
        lock_file = open(Path(__file__).parent.parent / "output" / ".pipeline.lock", "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("另一個 pipeline 正在執行中，本次退出（避免互相覆寫）")
            _push_job_status(cfg, secrets, args.job_id, "failed", "啟動失敗",
                             "另一個 pipeline 正在執行")
            sys.exit(2)

    do_collect = args.collect or args.stage == 1
    do_generate = args.generate or args.stage == 2
    do_regenerate = args.regenerate
    do_rerender = args.rerender
    do_publish = args.publish
    do_all = args.all or not (
        do_collect or do_generate or do_regenerate or do_rerender or do_publish)

    try:
        if do_all:
            ingested = collect(
                cfg, secrets, wk, args.dry_run, args.job_id, source_adapter)
            if ingested is not None:
                generate(
                    cfg, secrets, wk, args.dry_run, args.job_id,
                    delivery_adapter=delivery_adapter, delivery_name=delivery_name)
            else:
                _push_job_status(cfg, secrets, args.job_id, "completed", "本週沒有可生成的連結")
        elif do_collect:
            collect(cfg, secrets, wk, args.dry_run, args.job_id, source_adapter)
        elif do_regenerate:
            generate(
                cfg, secrets, wk, args.dry_run, args.job_id,
                reuse_previous=True, delivery_adapter=delivery_adapter,
                delivery_name=delivery_name)
        elif do_rerender:
            if args.dry_run:
                raise RuntimeError("--rerender 已是離線重排，不需搭配 --dry-run")
            pdf_path = rerender_existing(cfg, wk)
            print(f"重排完成：{pdf_path}")
        elif do_publish:
            if args.dry_run:
                raise RuntimeError("--publish 不支援 --dry-run；請省略 --dry-run")
            publish_existing(cfg, secrets, wk, args.job_id)
        else:
            generate(
                cfg, secrets, wk, args.dry_run, args.job_id,
                delivery_adapter=delivery_adapter, delivery_name=delivery_name)
    except Exception as exc:
        log.error("pipeline failed:\n%s", traceback.format_exc())
        _push_job_status(cfg, secrets, args.job_id, "failed", "生成流程中斷", str(exc))
        if not args.dry_run:
            line_push(secrets, cfg["line"]["push_to"],
                      f"❌ 週報生成失敗（{wk}），請查看 output/logs/{wk}.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
