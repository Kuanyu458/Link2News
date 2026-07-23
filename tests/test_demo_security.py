from __future__ import annotations

import socket
import time
import unittest
from unittest.mock import patch

from demo.security import (
    FetchRejectedError,
    SafeFetcher,
    UnsafeUrlError,
    parse_url_input,
    validate_public_url,
)


def public_dns(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


class DemoSecurityTests(unittest.TestCase):
    @patch("demo.security.socket.getaddrinfo", side_effect=public_dns)
    def test_accepts_and_deduplicates_one_to_five_public_urls(self, _dns):
        urls = parse_url_input("https://example.com/a\nhttps://example.com/a\nhttps://example.org")
        self.assertEqual(len(urls), 2)

    def test_rejects_localhost_and_credentials(self):
        with self.assertRaises(UnsafeUrlError):
            validate_public_url("http://localhost/private")
        with self.assertRaises(UnsafeUrlError):
            validate_public_url("https://user:pass@example.com/")

    @patch(
        "demo.security.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))],
    )
    def test_rejects_cloud_metadata_address(self, _dns):
        with self.assertRaises(UnsafeUrlError):
            validate_public_url("http://metadata.invalid/latest")

    @patch("demo.security.socket.getaddrinfo", side_effect=public_dns)
    def test_rejects_more_than_five_urls(self, _dns):
        text = "\n".join(f"https://example.com/{index}" for index in range(6))
        with self.assertRaisesRegex(ValueError, "最多"):
            parse_url_input(text)

    def test_redirect_is_revalidated_and_private_target_is_rejected(self):
        response = _Response(302, {"location": "http://127.0.0.1/private"})
        fetcher = SafeFetcher(_Session([response]))
        with patch(
            "demo.security.validate_public_url",
            side_effect=[
                ("example.com", ("93.184.216.34",)),
                UnsafeUrlError("網址解析到非公開網路位址。"),
            ],
        ):
            with self.assertRaises(UnsafeUrlError):
                fetcher.fetch("https://example.com", check_robots=False)

    def test_bounded_reader_rejects_oversized_response(self):
        with self.assertRaises(FetchRejectedError):
            SafeFetcher._read_bounded([b"12", b"34"], 3, time.monotonic() + 1)

    def test_peer_ip_must_match_preflight_dns(self):
        response = _Response(200, {}, peer="93.184.216.35")
        with self.assertRaisesRegex(UnsafeUrlError, "不一致"):
            SafeFetcher._validate_peer(response, ("93.184.216.34",))


class _Socket:
    def __init__(self, peer):
        self.peer = peer

    def getpeername(self):
        return self.peer, 443


class _Connection:
    def __init__(self, peer):
        self.sock = _Socket(peer)


class _Raw:
    def __init__(self, peer):
        self._connection = _Connection(peer)


class _Response:
    def __init__(self, status_code, headers, peer="93.184.216.34"):
        self.status_code = status_code
        self.headers = headers
        self.raw = _Raw(peer)

    def close(self):
        pass


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.headers = {}

    def get(self, *_args, **_kwargs):
        return self.responses.pop(0)
