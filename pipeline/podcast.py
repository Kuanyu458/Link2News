"""Podcast v3：訪談式節目腳本 + 可切換 TTS 後端（edge / openvoice）。

節目形式：主持人（犀利、幽默、替聽眾問蠢問題）訪談來賓研究員（深入解說）。
TTS：
  - edge（預設）：edge-tts 雙聲道，零設定
  - openvoice：OpenVoice V2 聲音轉換——用「你自己或已授權的聲音樣本」讓對話
    有真人質感。需先跑 scripts/setup_openvoice.sh 建置，並在 config 指定樣本路徑。
    後端失敗時自動退回 edge。
"""
import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from llm import ask_json

log = logging.getLogger("weekly.podcast")


def gen_script(papers_report: str, wk: str, target_minutes: int) -> list[dict]:
    """生成訪談式對話腳本 [{speaker: 'HOST'|'GUEST', text: ...}]。"""
    prompt = (
        f"根據以下本週文獻摘要報告，寫一集約 {target_minutes} 分鐘"
        f"（約 {target_minutes * 260} 字）的繁體中文 podcast **訪談節目**腳本。\n\n"
        "節目形式：一位主持人訪談一位來賓（本週論文的「導讀研究員」）。\n"
        "主持人風格（參考台灣知名訪談型 podcast 的節奏）：\n"
        "- 開場自我介紹節目名稱《週知電台》，一兩句冷幽默把本週主題帶出來\n"
        "- 替聽眾問「聽起來很蠢但大家都想問」的問題，敢打斷、敢追問\n"
        "- 用生活化類比接住來賓的技術解釋（「所以它其實就是在幫 AI 寫小抄？」）\n"
        "- 偶爾吐槽（對論文的誇張宣稱、對業界現象），但吐槽完會回到重點\n"
        "- 段落之間用輕鬆的轉場（「好，下一篇更誇張」）\n"
        "來賓風格：真的懂技術的研究員，被問倒時會誠實說「這篇沒講清楚」，"
        "引用論文中的具體數字，並主動指出缺陷與限制。\n\n"
        "內容要求：\n"
        "- 每篇論文都要談到：動機、方法直覺、關鍵數字、缺陷\n"
        "- 對話自然口語（有「欸」「對啊」「等等」等回應詞），不要輪流念稿\n"
        "- 英文專有名詞保留英文，數字用中文語感寫出（「百分之四十七」）\n"
        "- 結尾：主持人請來賓給聽眾一句本週 takeaway，然後幽默收尾\n"
        '輸出 JSON 陣列：[{"speaker": "HOST", "text": "..."}, {"speaker": "GUEST", "text": "..."}]\n\n'
        f"=== 本週文獻摘要報告 ===\n{papers_report[:50000]}"
    )
    script = ask_json(prompt, max_tokens=20000)
    # 相容舊格式（A/B）
    for seg in script:
        if seg.get("speaker") in ("A", "主持人"):
            seg["speaker"] = "HOST"
        elif seg.get("speaker") in ("B", "來賓"):
            seg["speaker"] = "GUEST"
    log.info("podcast script: %d turns, ~%d chars", len(script),
             sum(len(s["text"]) for s in script))
    return script


# ---------- TTS 後端：edge ----------

async def _edge_synthesize(script: list[dict], voices: dict, tmp_dir: Path,
                           progress=None) -> list[Path]:
    import edge_tts
    files = []
    for i, seg in enumerate(script):
        voice = voices["host"] if seg["speaker"] == "HOST" else voices["guest"]
        out = tmp_dir / f"seg_{i:04d}.mp3"
        for attempt in range(5):
            try:
                await edge_tts.Communicate(seg["text"], voice).save(str(out))
                break
            except Exception as e:
                log.warning("tts segment %d attempt %d failed: %s", i, attempt + 1, e)
                await asyncio.sleep(min(3 * 2 ** attempt, 30))
        else:
            log.error("tts segment %d dropped after 5 attempts: %s", i, seg["text"][:40])
        if out.exists() and out.stat().st_size > 0:
            files.append(out)
        if progress:
            progress(f"產生 Podcast：語音合成 {i + 1}/{len(script)}")
    return files


# ---------- TTS 後端：openvoice（透過獨立 conda 環境的 worker 執行） ----------

def _openvoice_synthesize(script: list[dict], cfg: dict, tmp_dir: Path) -> list[Path]:
    """呼叫 openvoice_worker.py（跑在獨立 Python 環境）合成全部片段。

    需求（缺一則丟例外，由上層退回 edge）：
      - cfg.podcast.openvoice.python：OpenVoice 環境的 python 路徑
      - cfg.podcast.openvoice.checkpoints_dir：OpenVoice V2 checkpoints
      - cfg.podcast.openvoice.reference_host / reference_guest：
        聲音參考樣本（**你自己的錄音或已取得授權的聲音**，30 秒以上 wav/mp3）
    """
    ov = cfg["podcast"].get("openvoice", {}) or {}
    python = ov.get("python", "")
    ckpt = ov.get("checkpoints_dir", "")
    ref_host, ref_guest = ov.get("reference_host", ""), ov.get("reference_guest", "")
    for label, path in [("python", python), ("checkpoints_dir", ckpt),
                        ("reference_host", ref_host), ("reference_guest", ref_guest)]:
        if not path or not Path(path).exists():
            raise RuntimeError(f"openvoice 設定缺少或不存在：{label}={path!r}"
                               "（先執行 scripts/setup_openvoice.sh 並填 config）")

    job = {"segments": [{"i": i, "speaker": s["speaker"], "text": s["text"]}
                        for i, s in enumerate(script)],
           "checkpoints_dir": ckpt, "reference_host": ref_host,
           "reference_guest": ref_guest, "out_dir": str(tmp_dir)}
    job_file = tmp_dir / "job.json"
    job_file.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    worker = Path(__file__).parent / "openvoice_worker.py"
    r = subprocess.run([python, str(worker), str(job_file)],
                       capture_output=True, text=True, timeout=7200)
    if r.returncode != 0:
        raise RuntimeError(f"openvoice worker failed: {(r.stderr or r.stdout)[-800:]}")
    files = sorted(tmp_dir.glob("seg_*.wav"))
    if not files:
        raise RuntimeError("openvoice worker produced no audio")
    return files


# ---------- 串接與主入口 ----------

def _concat_audio(files: list[Path], dest: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        listfile = dest.parent / "concat_list.txt"
        listfile.write_text("\n".join(f"file '{f}'" for f in files), encoding="utf-8")
        codec = ["-c:a", "libmp3lame", "-q:a", "3"] if files[0].suffix == ".wav" else ["-c", "copy"]
        subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
                        *codec, str(dest)], check=True, capture_output=True)
        listfile.unlink()
    else:
        with open(dest, "wb") as out:
            for f in files:
                out.write(f.read_bytes())
        log.info("ffmpeg not found; raw concat used")


def make_podcast(papers_report: str, cfg: dict, out_dir: Path, wk: str,
                 progress=None) -> Path | None:
    if "本週沒有收集到學術文獻" in papers_report:
        log.info("no papers this week; skipping podcast")
        return None
    if progress:
        progress("產生 Podcast：建立訪談腳本")
    script = gen_script(papers_report, wk, cfg["podcast"].get("target_minutes", 15))
    (out_dir / "podcast_script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")

    backend = cfg["podcast"].get("tts_backend", "edge")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        files: list[Path] = []
        if backend == "openvoice":
            if progress:
                progress("產生 Podcast：OpenVoice 語音合成")
            try:
                files = _openvoice_synthesize(script, cfg, tmp_dir)
                log.info("openvoice synthesis ok (%d segments)", len(files))
            except Exception as e:
                log.warning("openvoice backend failed (%s); falling back to edge-tts", e)
        if not files:
            voices = cfg["podcast"]["voices"]
            files = asyncio.run(_edge_synthesize(script, voices, tmp_dir, progress))
        if not files:
            log.error("TTS produced no audio")
            return None
        dest = out_dir / "podcast.mp3"
        if progress:
            progress("產生 Podcast：合併音訊")
        _concat_audio(files, dest)
    log.info("podcast written: %s (%.1f MB)", dest, dest.stat().st_size / 1e6)
    return dest
