"""Gradio UI and health endpoint for the isolated public preview."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

import gradio as gr
from fastapi import FastAPI

from demo.service import (
    DemoRejectedError,
    DemoService,
    cleanup_expired_jobs,
    health_payload,
)


service = DemoService()
site_key = os.environ.get("TURNSTILE_SITE_KEY", "")
APP_CSS = """
.hero {max-width:820px;margin:auto;text-align:center}
.notice {padding:12px;border:1px solid #d9a441;border-radius:8px;background:#fff8df}
#turnstile-token {display:none!important}
"""


def _turnstile_markup() -> str:
    if not site_key:
        return (
            "<div class='notice'>Demo 尚未設定 Turnstile site key；"
            "正式環境會停用生成。</div>"
        )
    return f"""
<div id="link2news-turnstile" class="cf-turnstile"
 data-sitekey="{site_key}" data-action="turnstile-spin-v1"
 data-callback="link2newsTurnstileDone"></div>
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
<script>
window.link2newsTurnstileDone = function(token) {{
  const root = document.querySelector('#turnstile-token');
  const input = root && root.querySelector('textarea,input');
  if (input) {{
    input.value = token;
    input.dispatchEvent(new Event('input', {{bubbles:true}}));
    input.dispatchEvent(new Event('change', {{bubbles:true}}));
  }}
}};
</script>"""


def generate(
    urls: str,
    consent: bool,
    turnstile_token: str,
    request: gr.Request,
    progress: gr.Progress = gr.Progress(),
):
    client_ip = request.client.host if request and request.client else "unknown"
    try:
        result = service.run(
            urls,
            consent,
            turnstile_token,
            client_ip,
            progress=lambda fraction, message: progress(fraction, desc=message),
        )
        status = {
            "job_id": result.job_id,
            "podcast_seconds": round(result.duration_seconds, 1),
            "sources": result.source_statuses,
            "retention": "產物將在 60 分鐘後刪除",
        }
        return (
            result.html,
            json.dumps(status, ensure_ascii=False, indent=2),
            str(result.mp3_path),
            str(result.pdf_path),
            str(result.mp3_path),
        )
    except DemoRejectedError as exc:
        raise gr.Error(str(exc)) from exc


with gr.Blocks(title="Link2News 公開 Demo") as blocks:
    gr.Markdown(
        """
<div class="hero">
<h1>Link2News 公開試用</h1>
<p>貼入 1–5 個不需登入的公開網址，取得一頁週報與約三分鐘雙人 Podcast。</p>
<p><strong>免費 Space 閒置後會休眠，冷啟動可能需要等待。</strong></p>
</div>
"""
    )
    urls = gr.Textbox(
        label="公開網址",
        lines=6,
        placeholder="每行一個網址，最多 5 個；只接受 http/https 公開頁面",
    )
    consent = gr.Checkbox(
        label=(
            "我確認只貼公開網址，並了解摘錄會送至 Gemini 免費層；"
            "免費層內容可能被 Google 用於改善產品。"
        )
    )
    gr.HTML(_turnstile_markup())
    turnstile_token = gr.Textbox(elem_id="turnstile-token", container=False)
    submit = gr.Button("生成一頁週報與 Podcast", variant="primary")
    report = gr.HTML(label="一頁週報預覽")
    status = gr.Code(label="來源與工作狀態", language="json")
    audio = gr.Audio(label="Podcast 播放", type="filepath")
    with gr.Row():
        pdf = gr.File(label="下載 A4 PDF")
        mp3 = gr.File(label="下載 MP3")
    submit.click(
        generate,
        inputs=[urls, consent, turnstile_token],
        outputs=[report, status, audio, pdf, mp3],
        concurrency_limit=1,
        api_visibility="private",
    )

blocks.queue(default_concurrency_limit=1, max_size=5)


@asynccontextmanager
async def lifespan(_app):
    async def cleanup_loop():
        while True:
            await asyncio.sleep(60)
            cleanup_expired_jobs()

    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="Link2News Demo",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


@app.get("/healthz")
def healthz():
    return health_payload()


app = gr.mount_gradio_app(app, blocks, path="/", css=APP_CSS)
