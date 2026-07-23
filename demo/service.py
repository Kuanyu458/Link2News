"""Orchestrate one bounded, isolated public demo job."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from demo.content import SourceRecord, build_prompt, extract_source
from demo.providers import GeminiProvider, ProviderUnavailableError
from demo.render import encode_podcast, render_html, render_pdf
from demo.security import FetchRejectedError, SafeFetcher, UnsafeUrlError, parse_url_input


log = logging.getLogger("link2news.demo")
WORK_ROOT = Path(os.environ.get("DEMO_WORK_ROOT", "/tmp/link2news-demo"))
ARTIFACT_TTL_SECONDS = 3600
PER_IP_DAILY_LIMIT = 2
GLOBAL_DAILY_LIMIT = 20


class DemoRejectedError(RuntimeError):
    """A user-visible fail-closed rejection."""


@dataclass(frozen=True)
class DemoResult:
    job_id: str
    html: str
    html_path: Path
    pdf_path: Path
    mp3_path: Path
    source_statuses: list[dict[str, str]]
    duration_seconds: float


class QuotaStore:
    def __init__(self, path: Path | None = None, salt: str | None = None):
        self.path = path or WORK_ROOT / "quota.sqlite3"
        self.salt = salt or os.environ.get("DEMO_RATE_SALT") or secrets.token_hex(32)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as database:
            database.execute(
                "CREATE TABLE IF NOT EXISTS usage "
                "(day TEXT NOT NULL, client_hash TEXT NOT NULL, count INTEGER NOT NULL, "
                "PRIMARY KEY(day, client_hash))"
            )
            database.execute(
                "CREATE TABLE IF NOT EXISTS turnstile_replay "
                "(token_hash TEXT PRIMARY KEY, created_at INTEGER NOT NULL)"
            )

    def consume(self, client_ip: str) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        client_hash = self._hash(client_ip)
        with self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT count FROM usage WHERE day=? AND client_hash=?",
                (today, client_hash),
            ).fetchone()
            own_count = int(row[0]) if row else 0
            total = int(database.execute(
                "SELECT COALESCE(SUM(count),0) FROM usage WHERE day=?", (today,)
            ).fetchone()[0])
            if own_count >= PER_IP_DAILY_LIMIT:
                raise DemoRejectedError("此來源今日 2 次免費試用額度已用完。")
            if total >= GLOBAL_DAILY_LIMIT:
                raise DemoRejectedError("今日全站 20 次免費試用額度已用完。")
            database.execute(
                "INSERT INTO usage(day,client_hash,count) VALUES(?,?,1) "
                "ON CONFLICT(day,client_hash) DO UPDATE SET count=count+1",
                (today, client_hash),
            )

    def consume_turnstile_token(self, token: str) -> None:
        token_hash = self._hash(token)
        now = int(time.time())
        with self._connect() as database:
            database.execute(
                "DELETE FROM turnstile_replay WHERE created_at < ?", (now - 600,))
            try:
                database.execute(
                    "INSERT INTO turnstile_replay(token_hash,created_at) VALUES(?,?)",
                    (token_hash, now),
                )
            except sqlite3.IntegrityError as exc:
                raise DemoRejectedError("驗證 token 已使用，請重新驗證。") from exc

    @contextmanager
    def _connect(self):
        database = sqlite3.connect(self.path, timeout=10)
        try:
            yield database
            database.commit()
        finally:
            database.close()

    def _hash(self, value: str) -> str:
        return hashlib.sha256(f"{self.salt}:{value}".encode()).hexdigest()


class TurnstileVerifier:
    def __init__(self, endpoint: str | None = None, session: requests.Session | None = None):
        self.endpoint = endpoint or os.environ.get("TURNSTILE_VERIFY_URL", "")
        self.session = session or requests.Session()

    def verify(self, token: str, remote_ip: str) -> None:
        if os.environ.get("DEMO_ALLOW_TEST_TURNSTILE") == "1" and token == "test-pass":
            return
        if not self.endpoint:
            raise DemoRejectedError("Turnstile 驗證服務尚未設定，Demo 暫停。")
        if not token:
            raise DemoRejectedError("請先完成人機驗證。")
        try:
            response = self.session.post(
                self.endpoint,
                json={"token": token, "remoteip": remote_ip},
                timeout=(5, 10),
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise DemoRejectedError("人機驗證服務暫時不可用。") from exc
        if not result.get("success"):
            raise DemoRejectedError("人機驗證無效或已過期，請重新驗證。")


class DemoService:
    def __init__(
        self,
        fetcher: SafeFetcher | None = None,
        provider: GeminiProvider | None = None,
        quota: QuotaStore | None = None,
        verifier: TurnstileVerifier | None = None,
    ):
        self.fetcher = fetcher or SafeFetcher()
        self.provider = provider or GeminiProvider()
        self.quota = quota or QuotaStore()
        self.verifier = verifier or TurnstileVerifier()

    def run(
        self,
        url_text: str,
        consent: bool,
        turnstile_token: str,
        client_ip: str,
        progress: Callable[[float, str], None] | None = None,
    ) -> DemoResult:
        update = progress or (lambda _fraction, _message: None)
        if not consent:
            raise DemoRejectedError(
                "送出前必須確認只貼公開網址，並同意摘錄送至 Gemini 免費層。")
        cleanup_expired_jobs()
        update(0.05, "驗證公開網址")
        urls = parse_url_input(url_text)
        self.verifier.verify(turnstile_token, client_ip)
        self.quota.consume_turnstile_token(turnstile_token)
        self.quota.consume(client_ip)

        job_id = str(uuid.uuid4())
        job_dir = WORK_ROOT / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        statuses: list[dict[str, str]] = []
        records: list[SourceRecord] = []
        started = time.monotonic()
        fetch_deadline = started + 60
        try:
            for index, url in enumerate(urls, start=1):
                update(0.1 + 0.35 * index / len(urls), f"擷取來源 {index}/{len(urls)}")
                domain = (urlparse(url).hostname or "").lower()
                try:
                    record = extract_source(
                        index, self.fetcher.fetch(url, deadline=fetch_deadline))
                    records.append(record)
                    statuses.append({
                        "source": str(index), "domain": domain,
                        "status": "success", "detail": "已擷取",
                    })
                except (FetchRejectedError, UnsafeUrlError, requests.RequestException, ValueError) as exc:
                    statuses.append({
                        "source": str(index), "domain": domain,
                        "status": "failed", "detail": _safe_error(exc),
                    })
            if not records:
                raise DemoRejectedError("所有來源都無法安全擷取，因此沒有產生空白報告。")

            update(0.52, "產生結構化摘要與 Podcast 腳本")
            report = self.provider.generate_digest(
                build_prompt(records),
                allowed_source_ids={record.id for record in records},
            )
            update(0.70, "排版一頁週報")
            html_path = render_html(report, records, job_dir / "report.html")
            pdf_path = render_pdf(html_path, job_dir / "report.pdf")
            update(0.82, "合成雙人 Podcast")
            wav_path = self.provider.synthesize(report["podcast"], job_dir / "podcast.wav")
            mp3_path, duration = encode_podcast(wav_path, job_dir / "podcast.mp3")
            wav_path.unlink(missing_ok=True)
            update(1.0, "完成")
            log.info(
                "job=%s domains=%s stage=completed elapsed_ms=%d",
                job_id,
                ",".join(item["domain"] for item in statuses),
                int((time.monotonic() - started) * 1000),
            )
            return DemoResult(
                job_id=job_id,
                html=html_path.read_text(encoding="utf-8"),
                html_path=html_path,
                pdf_path=pdf_path,
                mp3_path=mp3_path,
                source_statuses=statuses,
                duration_seconds=duration,
            )
        except Exception as exc:
            log.warning(
                "job=%s domains=%s stage=failed error=%s elapsed_ms=%d",
                job_id,
                ",".join(item["domain"] for item in statuses),
                type(exc).__name__,
                int((time.monotonic() - started) * 1000),
            )
            shutil.rmtree(job_dir, ignore_errors=True)
            if isinstance(exc, (DemoRejectedError, ProviderUnavailableError)):
                raise
            raise DemoRejectedError("Demo 產生失敗，未保留不完整產物。") from exc


def cleanup_expired_jobs(now: float | None = None) -> int:
    jobs = WORK_ROOT / "jobs"
    if not jobs.exists():
        return 0
    cutoff = (now or time.time()) - ARTIFACT_TTL_SECONDS
    deleted = 0
    for candidate in jobs.iterdir():
        if candidate.is_dir() and candidate.stat().st_mtime < cutoff:
            shutil.rmtree(candidate, ignore_errors=True)
            deleted += 1
    return deleted


def health_payload(queue_status: str = "ready") -> dict[str, object]:
    return {
        "version": os.environ.get("LINK2NEWS_DEMO_VERSION", "dev"),
        "text_provider_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "tts_provider_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "turnstile_configured": bool(os.environ.get("TURNSTILE_VERIFY_URL")),
        "queue": queue_status,
    }


def _safe_error(exc: Exception) -> str:
    text = str(exc).strip()
    return text[:160] if text else type(exc).__name__
