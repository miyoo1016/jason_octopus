"""
Claude API 어댑터 (Step 3).
anthropic 패키지가 필요합니다: pip install anthropic
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Claude Haiku 4.5 요금 (USD / 1M tokens)
_PRICE_INPUT  = 0.80
_PRICE_OUTPUT = 4.00


def claude_analyze_stocks(
    stocks: list[dict[str, Any]],
    api_key: str,
    system_prompt: str | None = None,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 400,
) -> tuple[dict[str, dict], float]:
    """
    Claude API로 종목별 AI 분석.

    Returns:
        (comments, cost_usd)
        - comments: {code: {"score": 75, "grade": "B", "comment": "...", "key_reasons": [...]}}
        - cost_usd: 총 API 비용
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic 패키지가 설치되어 있지 않습니다. pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key, timeout=20.0)

    if system_prompt is None:
        system_prompt = (
            "당신은 한국 주식 시장 전문 애널리스트입니다. "
            "제공된 종목 정보를 바탕으로 간결하고 정확한 투자 참고 분석을 제공합니다. "
            "투자 권유가 아닌 참고 정보임을 명심하세요."
        )

    comments: dict[str, dict] = {}
    total_cost = 0.0

    for stock in stocks:
        code = stock.get("code", "")
        factors = {k: v for k, v in stock.items() if k not in ("code", "name", "market")}
        user_prompt = (
            f"종목: {stock.get('name', '─')}({code}), 시장: {stock.get('market', '─')}\n"
            f"종가: {stock.get('close', '─')}원\n"
            f"팩터: {json.dumps(factors, ensure_ascii=False, default=str)}\n\n"
            "다음 JSON 형식으로만 응답하세요 (마크다운·설명 없이 JSON만):\n"
            '{"score": 0~100, "grade": "A|B|C", '
            '"comment": "2~3문장 매매 관점 분석", "key_reasons": ["이유1", "이유2"]}'
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text if response.content else ""
            usage = response.usage
            in_tok  = getattr(usage, "input_tokens", 0)
            out_tok = getattr(usage, "output_tokens", 0)
            total_cost += (in_tok / 1_000_000 * _PRICE_INPUT) + (out_tok / 1_000_000 * _PRICE_OUTPUT)

            text = raw.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                inner = [l for l in lines[1:] if l.strip() != "```"]
                text = "\n".join(inner).strip()

            parsed = json.loads(text)
            comments[code] = parsed if isinstance(parsed, dict) else {"comment": str(parsed), "grade": "─"}

        except Exception as exc:
            logger.warning("Claude 분석 실패 %s: %s", code, exc)
            comments[code] = {"comment": "분석 실패", "grade": "─"}

    return comments, round(total_cost, 6)
