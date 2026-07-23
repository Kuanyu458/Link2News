from __future__ import annotations

import json
import unittest

from demo.providers import GeminiProvider, ProviderUnavailableError


def valid_digest():
    return {
        "title": "標題",
        "dek": "副標",
        "highlights": [{"headline": "重點", "summary": "摘要", "source_ids": [1]}],
        "sources": [{"id": 1, "title": "來源", "summary": "來源摘要"}],
        "podcast": [
            {"speaker": "HOST" if index % 2 == 0 else "GUEST", "text": "測" * 100}
            for index in range(8)
        ],
    }


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self.payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class DemoProviderTests(unittest.TestCase):
    def test_structured_text_is_validated_in_one_call(self):
        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(valid_digest())}]}}]}
        session = FakeSession([FakeResponse(payload=payload)])
        result = GeminiProvider(api_key="test", session=session).generate_digest("prompt")
        self.assertEqual(result["title"], "標題")
        self.assertEqual(len(session.calls), 1)

    def test_429_fails_closed_without_paid_fallback(self):
        session = FakeSession([FakeResponse(status_code=429, headers={"Retry-After": "30"})])
        provider = GeminiProvider(api_key="test", session=session)
        with self.assertRaisesRegex(ProviderUnavailableError, "免費"):
            provider.generate_digest("prompt")
        self.assertEqual(len(session.calls), 1)

    def test_invalid_source_reference_is_rejected(self):
        data = valid_digest()
        data["highlights"][0]["source_ids"] = [2]
        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(data)}]}}]}
        with self.assertRaisesRegex(ProviderUnavailableError, "不存在"):
            GeminiProvider(
                api_key="test", session=FakeSession([FakeResponse(payload=payload)])
            ).generate_digest("prompt")

    def test_source_not_present_in_this_job_is_rejected(self):
        data = valid_digest()
        data["sources"][0]["id"] = 2
        data["highlights"][0]["source_ids"] = [2]
        payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(data)}]}}]}
        with self.assertRaisesRegex(ProviderUnavailableError, "假來源"):
            GeminiProvider(
                api_key="test", session=FakeSession([FakeResponse(payload=payload)])
            ).generate_digest("prompt", allowed_source_ids={1, 3})
