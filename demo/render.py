"""Render the isolated one-page report and normalize podcast audio."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pypdfium2
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

from demo.content import SourceRecord


TEMPLATE_DIR = Path(__file__).with_name("templates")
TARGET_SECONDS = 180.0
MIN_SECONDS = 170.0
MAX_SECONDS = 190.0


def render_html(data: dict[str, Any], records: list[SourceRecord], destination: Path) -> Path:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(("html", "xml")),
    )
    source_urls = {record.id: record.url for record in records}
    template = environment.get_template("onepage.html")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        template.render(report=data, sources=records, source_urls=source_urls),
        encoding="utf-8",
    )
    return destination


def render_pdf(html_path: Path, destination: Path) -> Path:
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH") or None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path=executable,
            args=["--no-sandbox"] if executable else None,
        )
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.emulate_media(media="print")
            page.pdf(
                path=str(destination),
                format="A4",
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
        finally:
            browser.close()
    pdf = pypdfium2.PdfDocument(str(destination))
    try:
        if len(pdf) != 1:
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"Demo PDF 必須正好 1 頁，目前為 {len(pdf)} 頁。")
    finally:
        pdf.close()
    return destination


def encode_podcast(wav_path: Path, destination: Path) -> tuple[Path, float]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("Demo 容器缺少 ffmpeg 或 ffprobe。")
    source_duration = probe_duration(wav_path, ffprobe)
    if source_duration <= 0:
        raise RuntimeError("TTS 音訊長度無效。")
    tempo = source_duration / TARGET_SECONDS
    if not 0.85 <= tempo <= 1.15:
        raise RuntimeError(
            f"TTS 原始長度 {source_duration:.1f} 秒，超過允許的 ±15% 校正範圍。")
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(wav_path), "-filter:a", f"atempo={tempo:.6f}",
            "-codec:a", "libmp3lame", "-b:a", "96k", str(destination),
        ],
        check=True,
        timeout=90,
    )
    duration = probe_duration(destination, ffprobe)
    if not MIN_SECONDS <= duration <= MAX_SECONDS:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Podcast 長度 {duration:.1f} 秒，不在 170–190 秒驗收範圍。")
    return destination, duration


def probe_duration(path: Path, ffprobe: str | None = None) -> float:
    executable = ffprobe or shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("找不到 ffprobe。")
    completed = subprocess.run(
        [
            executable, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return float(completed.stdout.strip())
