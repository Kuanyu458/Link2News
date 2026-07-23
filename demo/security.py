"""Bounded, SSRF-resistant public URL fetching for the demo."""
from __future__ import annotations

import gzip
import ipaddress
import logging
import re
import socket
import time
import urllib.robotparser
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests

from pipeline.fetch import canonicalize, normalize_url


log = logging.getLogger("link2news.demo.fetch")
USER_AGENT = "Link2News-Demo/0.1 (+https://github.com/Kuanyu458/Link2News)"
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_CONTENT_TYPES = {
    "text/html",
    "text/plain",
    "application/xhtml+xml",
    "application/pdf",
}
MAX_REDIRECTS = 3
HTML_LIMIT = 2 * 1024 * 1024
PDF_LIMIT = 15 * 1024 * 1024
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 20
FETCH_BUDGET_SECONDS = 60


class UnsafeUrlError(ValueError):
    """Raised when a URL could reach a non-public network target."""


class FetchRejectedError(RuntimeError):
    """Raised when a public resource violates demo fetch limits."""


@dataclass(frozen=True)
class FetchedDocument:
    requested_url: str
    final_url: str
    content_type: str
    body: bytes


def parse_url_input(text: str, maximum: int = 5) -> list[str]:
    """Parse, normalize and deduplicate one to five public URLs."""
    candidates = [
        token.rstrip(".,;!?…。，")
        for token in re.findall(r"https?://[^\s\"'<>）)\]】」]+", text or "")
    ]
    seen: set[str] = set()
    urls: list[str] = []
    for candidate in candidates:
        normalized = canonicalize(normalize_url(candidate))
        validate_public_url(normalized)
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    if not urls:
        raise ValueError("請輸入至少 1 個 http/https 公開網址。")
    if len(urls) > maximum:
        raise ValueError(f"公開 Demo 每次最多接受 {maximum} 個不同網址。")
    return urls


def validate_public_url(url: str) -> tuple[str, tuple[str, ...]]:
    """Validate syntax and resolve every address to a globally routable IP."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeUrlError("只接受 http 或 https 網址。")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("不接受含帳號或密碼的網址。")
    if not parsed.hostname:
        raise UnsafeUrlError("網址缺少主機名稱。")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise UnsafeUrlError("不接受本機或內網網址。")
    try:
        infos = socket.getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeUrlError("網址主機無法解析。") from exc
    addresses = tuple(sorted({item[4][0].split("%", 1)[0] for item in infos}))
    if not addresses:
        raise UnsafeUrlError("網址主機沒有可用 IP。")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeUrlError("網址解析到非公開網路位址。")
    return hostname, addresses


class SafeFetcher:
    """Fetch public documents with redirect, peer-IP, size and robots limits."""

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain,application/pdf;q=0.8",
            "Accept-Encoding": "gzip, identity",
        })

    def fetch(
        self,
        url: str,
        check_robots: bool = True,
        deadline: float | None = None,
    ) -> FetchedDocument:
        started = time.monotonic()
        deadline = deadline or (started + FETCH_BUDGET_SECONDS)
        current = url
        if check_robots and not self._robots_allowed(url, started, deadline):
            raise FetchRejectedError("網站 robots.txt 不允許此 Demo 擷取。")
        for redirect_count in range(MAX_REDIRECTS + 1):
            if time.monotonic() > deadline:
                raise FetchRejectedError("網址擷取超過 60 秒限制。")
            _hostname, resolved = validate_public_url(current)
            response = self.session.get(
                current,
                allow_redirects=False,
                stream=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
            try:
                self._validate_peer(response, resolved)
                if response.status_code in {301, 302, 303, 307, 308}:
                    if redirect_count >= MAX_REDIRECTS:
                        raise FetchRejectedError("網址重新導向超過 3 次。")
                    location = response.headers.get("location", "")
                    if not location:
                        raise FetchRejectedError("重新導向缺少 Location。")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise FetchRejectedError(
                        f"不支援的內容類型：{content_type or 'unknown'}")
                limit = PDF_LIMIT if content_type == "application/pdf" else HTML_LIMIT
                length = int(response.headers.get("content-length") or 0)
                if length > limit:
                    raise FetchRejectedError("來源檔案超過 Demo 大小限制。")
                body = self._read_bounded(
                    response.iter_content(64 * 1024), limit, deadline)
                if response.headers.get("content-encoding", "").lower() == "gzip":
                    body = gzip.decompress(body)
                    if len(body) > limit:
                        raise FetchRejectedError("解壓縮後內容超過 Demo 大小限制。")
                return FetchedDocument(url, current, content_type, body)
            finally:
                response.close()
        raise FetchRejectedError("無法完成網址擷取。")

    def _robots_allowed(self, url: str, started: float, deadline: float) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            document = self.fetch(robots_url, check_robots=False, deadline=deadline)
            if document.content_type not in {"text/plain", "text/html"}:
                return True
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(document.body.decode("utf-8", errors="replace").splitlines())
            return parser.can_fetch(USER_AGENT, url)
        except (requests.RequestException, FetchRejectedError, UnsafeUrlError, ValueError):
            # A missing or malformed robots file does not imply a prohibition.
            return time.monotonic() <= deadline

    @staticmethod
    def _read_bounded(chunks: Iterable[bytes], limit: int, deadline: float) -> bytes:
        data = bytearray()
        for chunk in chunks:
            if time.monotonic() > deadline:
                raise FetchRejectedError("網址擷取超過 60 秒限制。")
            if not chunk:
                continue
            data.extend(chunk)
            if len(data) > limit:
                raise FetchRejectedError("來源內容超過 Demo 大小限制。")
        return bytes(data)

    @staticmethod
    def _validate_peer(response: requests.Response, resolved: tuple[str, ...]) -> None:
        """Verify the actual TCP peer remains one of the pre-resolved public IPs."""
        candidates = [
            getattr(getattr(response.raw, "_connection", None), "sock", None),
            getattr(getattr(response.raw, "connection", None), "sock", None),
        ]
        sock = next((candidate for candidate in candidates if candidate is not None), None)
        if sock is None:
            # Some test transports do not expose a socket. Real urllib3 responses do.
            if response.raw.__class__.__module__.startswith(("urllib3", "requests")):
                raise UnsafeUrlError("無法驗證實際連線位址。")
            return
        peer = sock.getpeername()[0].split("%", 1)[0]
        ip = ipaddress.ip_address(peer)
        if not ip.is_global or peer not in resolved:
            raise UnsafeUrlError("實際連線位址與安全 DNS 檢查不一致。")
