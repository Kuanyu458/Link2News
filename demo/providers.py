"""Gemini Free Tier text and TTS providers used by the public demo."""
from __future__ import annotations

import base64
import json
import os
import time
import wave
from pathlib import Path
from typing import Any

import requests


GEMINI_ROOT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TEXT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"


class ProviderUnavailableError(RuntimeError):
    """Provider is missing, exhausted or temporarily unavailable."""


class GeminiProvider:
    def __init__(
        self,
        api_key: str | None = None,
        text_model: str | None = None,
        tts_model: str | None = None,
        session: requests.Session | None = None,
    ):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.text_model = text_model or os.environ.get(
            "DEMO_TEXT_MODEL", DEFAULT_TEXT_MODEL)
        self.tts_model = tts_model or os.environ.get(
            "DEMO_TTS_MODEL", DEFAULT_TTS_MODEL)
        self.session = session or requests.Session()

    def generate_digest(
        self,
        prompt: str,
        allowed_source_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        self._require_key()
        schema = digest_json_schema()
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 5000,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        result = self._post(
            f"{GEMINI_ROOT}/models/{self.text_model}:generateContent",
            payload,
            params={"key": self.api_key},
        )
        try:
            parts = result["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
            data = json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderUnavailableError("Gemini 回傳格式無法解析。") from exc
        return validate_digest(data, allowed_source_ids)

    def synthesize(self, transcript: list[dict[str, str]], destination: Path) -> Path:
        self._require_key()
        lines = "\n".join(
            f"{turn['speaker']}: {turn['text']}" for turn in transcript)
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{
                    "text": (
                        "以自然、清楚的台灣繁體中文播客節奏朗讀以下對話。"
                        "不要加入音樂、音效或腳本外內容：\n" + lines
                    )
                }],
            }],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "multiSpeakerVoiceConfig": {
                        "speakerVoiceConfigs": [
                            {
                                "speaker": "HOST",
                                "voiceConfig": {
                                    "prebuiltVoiceConfig": {"voiceName": "Puck"}
                                },
                            },
                            {
                                "speaker": "GUEST",
                                "voiceConfig": {
                                    "prebuiltVoiceConfig": {"voiceName": "Kore"}
                                },
                            },
                        ]
                    }
                },
            },
        }
        result = self._post(
            f"{GEMINI_ROOT}/models/{self.tts_model}:generateContent",
            payload,
            params={"key": self.api_key},
        )
        encoded = _find_audio_data(result)
        try:
            pcm = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ProviderUnavailableError("Gemini TTS 音訊無法解碼。") from exc
        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(pcm)
        return destination

    def _require_key(self) -> None:
        if not self.api_key:
            raise ProviderUnavailableError(
                "Demo 尚未設定 GEMINI_API_KEY，服務暫時不可用。")

    def _post(
        self,
        url: str,
        payload: dict[str, Any],
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            response = self.session.post(
                url,
                params=params,
                headers={"Content-Type": "application/json", **(headers or {})},
                json=payload,
                timeout=(10, 180),
            )
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "")
                suffix = f"（約 {retry_after} 秒後可再試）" if retry_after else ""
                raise ProviderUnavailableError(
                    f"今日免費 Gemini 配額已用完或暫時受限{suffix}。")
            if response.status_code >= 500 and attempt == 0:
                time.sleep(1)
                continue
            if response.status_code >= 400:
                raise ProviderUnavailableError(
                    f"Gemini 服務拒絕請求（HTTP {response.status_code}）。")
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderUnavailableError("Gemini 回傳非 JSON 內容。") from exc
        raise ProviderUnavailableError("Gemini 服務暫時不可用。")


def digest_json_schema() -> dict[str, Any]:
    source_ids = {
        "type": "array",
        "items": {"type": "integer", "minimum": 1, "maximum": 5},
        "minItems": 1,
        "maxItems": 5,
    }
    return {
        "type": "object",
        "required": ["title", "dek", "highlights", "sources", "podcast"],
        "properties": {
            "title": {"type": "string", "maxLength": 60},
            "dek": {"type": "string", "maxLength": 140},
            "highlights": {
                "type": "array",
                "minItems": 1,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": ["headline", "summary", "source_ids"],
                    "properties": {
                        "headline": {"type": "string", "maxLength": 50},
                        "summary": {"type": "string", "maxLength": 220},
                        "source_ids": source_ids,
                    },
                },
            },
            "sources": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "required": ["id", "title", "summary"],
                    "properties": {
                        "id": {"type": "integer", "minimum": 1, "maximum": 5},
                        "title": {"type": "string", "maxLength": 90},
                        "summary": {"type": "string", "maxLength": 220},
                    },
                },
            },
            "podcast": {
                "type": "array",
                "minItems": 6,
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "required": ["speaker", "text"],
                    "properties": {
                        "speaker": {"type": "string", "enum": ["HOST", "GUEST"]},
                        "text": {"type": "string", "maxLength": 260},
                    },
                },
            },
        },
    }


def validate_digest(
    data: Any,
    allowed_source_ids: set[int] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ProviderUnavailableError("Gemini 摘要不是物件格式。")
    for key in ("title", "dek", "highlights", "sources", "podcast"):
        if key not in data:
            raise ProviderUnavailableError(f"Gemini 摘要缺少欄位：{key}")
    source_ids = {
        item.get("id") for item in data["sources"]
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }
    if not source_ids or not source_ids.issubset(set(range(1, 6))):
        raise ProviderUnavailableError("Gemini 回傳無效來源編號。")
    if allowed_source_ids is not None and not source_ids.issubset(allowed_source_ids):
        raise ProviderUnavailableError("Gemini 回傳了本次未提供的假來源。")
    for item in data["highlights"]:
        refs = set(item.get("source_ids") or []) if isinstance(item, dict) else set()
        if not refs or not refs.issubset(source_ids):
            raise ProviderUnavailableError("重點摘要引用了不存在的來源。")
    transcript_chars = 0
    for turn in data["podcast"]:
        if not isinstance(turn, dict) or turn.get("speaker") not in {"HOST", "GUEST"}:
            raise ProviderUnavailableError("Podcast 腳本 speaker 無效。")
        text = str(turn.get("text") or "").strip()
        if not text:
            raise ProviderUnavailableError("Podcast 腳本包含空白段落。")
        turn["text"] = text[:260]
        transcript_chars += len(text)
    if not 720 <= transcript_chars <= 840:
        raise ProviderUnavailableError(
            f"Podcast 腳本長度不符 3 分鐘目標（{transcript_chars} 字）。")
    data["title"] = str(data["title"])[:60]
    data["dek"] = str(data["dek"])[:140]
    return data


def _find_audio_data(result: dict[str, Any]) -> str:
    output = result.get("output_audio") or {}
    if output.get("data"):
        return output["data"]
    for candidate in result.get("candidates") or []:
        for part in candidate.get("content", {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if inline.get("data"):
                return inline["data"]
    raise ProviderUnavailableError("Gemini TTS 沒有回傳音訊。")
