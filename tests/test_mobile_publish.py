import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import main as pipeline_main  # noqa: E402


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, artifact):
        self.artifact = artifact

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "artifact": self.artifact}


class MobilePublishTests(unittest.TestCase):
    def test_upload_sends_integrity_and_duration_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "podcast.mp3"
            path.write_bytes(b"podcast-audio")
            captured = {}

            def fake_put(url, data, headers, timeout):
                captured["url"] = url
                captured["body"] = data.read()
                captured["headers"] = headers
                captured["timeout"] = timeout
                return FakeResponse({
                    "kind": "podcast", "url": "https://example.test/media",
                    "durationMs": 1234, "expiresAt": 9999999999999,
                })

            cfg = {"collector": {"base_url": "https://collector.test"}}
            secrets = {"COLLECTOR_API_SECRET": "secret"}
            with patch("requests.put", side_effect=fake_put):
                result = pipeline_main._upload_artifact(
                    cfg, secrets, "2026-W28", "podcast", path, 1234)

            self.assertEqual(result["durationMs"], 1234)
            self.assertEqual(captured["body"], b"podcast-audio")
            self.assertEqual(captured["headers"]["Content-Type"], "audio/mpeg")
            self.assertEqual(captured["headers"]["X-Duration-Ms"], "1234")
            self.assertEqual(
                captured["headers"]["X-Content-SHA256"],
                hashlib.sha256(b"podcast-audio").hexdigest())

    def test_completion_contains_pdf_button_and_inline_audio(self):
        artifacts = {
            "report": {
                "url": "https://collector.test/report", "expiresAt": 9999999999999,
            },
            "podcast": {
                "url": "https://collector.test/podcast", "durationMs": 4567,
                "expiresAt": 9999999999999,
            },
        }
        messages = pipeline_main._completion_messages(
            "2026-W28", {"focus": [{"headline": "本週焦點"}]}, artifacts)
        self.assertEqual(messages[0]["type"], "flex")
        buttons = messages[0]["contents"]["footer"]["contents"]
        self.assertEqual(buttons[0]["action"]["label"], "閱讀 PDF")
        self.assertEqual(buttons[0]["action"]["uri"], artifacts["report"]["url"])
        self.assertEqual(messages[1], {
            "type": "audio", "originalContentUrl": artifacts["podcast"]["url"],
            "duration": 4567,
        })

    def test_missing_duration_keeps_download_without_inline_audio(self):
        artifacts = {
            "report": {"url": "https://collector.test/report", "expiresAt": 1},
            "podcast": {
                "url": "https://collector.test/podcast", "durationMs": 0,
                "expiresAt": 1,
            },
        }
        messages = pipeline_main._completion_messages("2026-W28", {}, artifacts)
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            messages[0]["contents"]["footer"]["contents"][1]["action"]["label"],
            "下載 Podcast")


if __name__ == "__main__":
    unittest.main()
