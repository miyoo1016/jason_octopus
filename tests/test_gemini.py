"""
llm/gemini.py 단위 테스트 — google.genai (google-genai) SDK 기반.
실제 API 호출 없이 mock으로 검증합니다.

실행:
    pytest tests/test_gemini.py -v
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from llm.gemini import (
    GeminiUsage,
    _parse_json_response,
    gemini_chat,
    total_cost,
)


# ── _parse_json_response ──────────────────────────────────────────────────────

class TestParseJsonResponse:
    def test_plain_json(self):
        result = _parse_json_response('{"score": 85, "grade": "A"}')
        assert result == {"score": 85, "grade": "A"}

    def test_json_with_markdown_block(self):
        result = _parse_json_response('```json\n{"score": 70}\n```')
        assert result == {"score": 70}

    def test_json_with_backtick_only(self):
        result = _parse_json_response('```\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_json_response("이건 JSON이 아닙니다.")


# ── GeminiUsage ───────────────────────────────────────────────────────────────

class TestGeminiUsage:
    def test_total_tokens(self):
        assert GeminiUsage(input_tokens=100, output_tokens=50).total_tokens == 150

    def test_cost_calculation(self):
        # 1M 입력 토큰 → $0.075
        assert abs(GeminiUsage(input_tokens=1_000_000).cost_usd - 0.075) < 1e-9

    def test_zero_cost(self):
        assert GeminiUsage().cost_usd == 0.0

    def test_as_dict_keys(self):
        d = GeminiUsage(input_tokens=10, output_tokens=5, latency_ms=123.4).as_dict()
        assert "cost_usd" in d
        assert "latency_ms" in d
        assert d["total_tokens"] == 15


def test_total_cost():
    usages = [
        GeminiUsage(input_tokens=1000, output_tokens=200),
        GeminiUsage(input_tokens=500,  output_tokens=100),
    ]
    assert total_cost(usages) > 0


# ── gemini_chat mock ──────────────────────────────────────────────────────────

def _mock_response(text: str, input_tokens: int = 50, output_tokens: int = 30) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.usage_metadata.prompt_token_count     = input_tokens
    resp.usage_metadata.candidates_token_count = output_tokens
    return resp


class TestGeminiChat:
    @patch("llm.gemini._make_client")
    def test_text_response(self, mock_make_client):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_response("안녕하세요.")
        mock_make_client.return_value = mock_client

        result, usage = gemini_chat(user_prompt="테스트", as_json=False)

        assert result == "안녕하세요."
        assert usage.input_tokens  == 50
        assert usage.output_tokens == 30
        assert usage.attempts == 1

    @patch("llm.gemini._make_client")
    def test_json_response(self, mock_make_client):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_response('{"score": 90, "grade": "A"}')
        mock_make_client.return_value = mock_client

        result, usage = gemini_chat(user_prompt="분석", as_json=True)

        assert isinstance(result, dict)
        assert result["score"] == 90

    @patch("llm.gemini._make_client")
    def test_json_with_markdown_code_block(self, mock_make_client):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_response('```json\n{"grade": "B"}\n```')
        mock_make_client.return_value = mock_client

        result, _ = gemini_chat(user_prompt="분석", as_json=True)
        assert isinstance(result, dict)
        assert result["grade"] == "B"

    @patch("llm.gemini._make_client")
    def test_json_parse_failure_returns_raw_text(self, mock_make_client):
        """JSON 파싱 3회 실패 시 원문 텍스트 반환 (예외 없음)."""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_response("JSON 아님")
        mock_make_client.return_value = mock_client

        result, usage = gemini_chat(user_prompt="분석", as_json=True)

        assert isinstance(result, str)
        assert usage.attempts == 3

    @patch("llm.gemini._make_client")
    def test_api_error_raises_after_max_attempts(self, mock_make_client):
        """API 오류가 3회 연속 발생하면 예외를 다시 던집니다."""
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = ConnectionError("네트워크 오류")
        mock_make_client.return_value = mock_client

        with pytest.raises(ConnectionError):
            gemini_chat(user_prompt="분석")
