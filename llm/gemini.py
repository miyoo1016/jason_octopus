"""
Gemini Flash API 어댑터 — 모든 LLM 호출은 이 모듈을 통해서만 수행합니다.

사용법:
    from llm.gemini import gemini_chat, GeminiUsage

    result, usage = gemini_chat(
        system_prompt="한국 주식 분석가입니다.",
        user_prompt="한솔홈데코(025750)를 분석해주세요.",
        max_tokens=512,
        as_json=True,        # JSON 객체 반환 강제
    )
    print(result)   # dict (as_json=True) 또는 str
    print(usage.cost_usd)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types as genai_types

from backend.config import settings

logger = logging.getLogger(__name__)

# ── Gemini Flash 요금 (USD / 1M tokens, 128k context 이하 기준) ───────────────
# https://ai.google.dev/pricing
_PRICE_INPUT_PER_1M  = 0.075
_PRICE_OUTPUT_PER_1M = 0.300

# ── 재시도 설정 ───────────────────────────────────────────────────────────────
_MAX_ATTEMPTS = 3
_RETRY_WAIT_S = 1.0


@dataclass
class GeminiUsage:
    """단일 LLM 호출의 토큰 사용량 및 비용 추적."""
    input_tokens:  int   = 0
    output_tokens: int   = 0
    latency_ms:    float = 0.0
    model:         str   = ""
    attempts:      int   = 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens  / 1_000_000 * _PRICE_INPUT_PER_1M
            + self.output_tokens / 1_000_000 * _PRICE_OUTPUT_PER_1M
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens":  self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens":  self.total_tokens,
            "cost_usd":      round(self.cost_usd, 6),
            "latency_ms":    round(self.latency_ms, 1),
            "model":         self.model,
            "attempts":      self.attempts,
        }


def _make_client() -> genai.Client:
    """Gemini 클라이언트 생성. 테스트 시 monkeypatch 대상."""
    return genai.Client(api_key=settings.gemini_api_key)


def _parse_json_response(text: str) -> dict | list:
    """모델 응답에서 JSON을 추출합니다 (3단계 Fallback).

    1. 직접 파싱
    2. 마크다운 코드 블록 제거 후 파싱
    3. 정규식으로 JSON 배열/객체 추출 후 파싱
    """
    import re

    text = text.strip()

    # 1. 직접 시도
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. 마크다운 코드 블록 제거
    if "```" in text:
        lines = text.splitlines()
        inner: list[str] = []
        in_block = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_block = not in_block
                continue
            if in_block or not in_block:
                inner.append(line)
        candidate = "\n".join(inner).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 3. 정규식으로 JSON 배열/객체 추출
    arr_match = re.search(r'\[[\s\S]*\]', text)
    if arr_match:
        try:
            return json.loads(arr_match.group())
        except json.JSONDecodeError:
            pass
    obj_match = re.search(r'\{[\s\S]*\}', text)
    if obj_match:
        try:
            return json.loads(obj_match.group())
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError(f"JSON 추출 실패 (len={len(text)})", text, 0)


def gemini_chat(
    user_prompt: str,
    system_prompt: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.2,
    as_json: bool = False,
) -> tuple[str | dict | list, GeminiUsage]:
    """
    Gemini Flash에 단일 메시지를 전송하고 결과를 반환합니다.

    Args:
        user_prompt:   사용자 입력 (종목 정보, 분석 요청 등)
        system_prompt: 시스템 역할 지시 (선택)
        max_tokens:    최대 출력 토큰 수
        temperature:   생성 다양성 (0=결정적, 1=창의적)
        as_json:       True이면 JSON으로 파싱해서 반환, 실패 시 재시도

    Returns:
        (result, usage) 튜플
        - result: as_json=True이면 dict/list, 아니면 str
        - usage: GeminiUsage (토큰·비용·지연 정보)
    """
    client = _make_client()
    usage  = GeminiUsage(model=settings.gemini_model)

    # 시스템 프롬프트 + JSON 지시 결합
    full_system = system_prompt
    full_user   = user_prompt
    if as_json:
        full_user += (
            "\n\n반드시 유효한 JSON만 출력하세요. "
            "마크다운 코드 블록(```)이나 설명 텍스트 없이 JSON 객체만 출력합니다."
        )

    config = genai_types.GenerateContentConfig(
        system_instruction=full_system or None,
        max_output_tokens=max_tokens,
        temperature=temperature,
    )

    t0 = time.perf_counter()

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        usage.attempts = attempt
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=full_user,
                config=config,
            )

            # 토큰 사용량 기록
            meta = getattr(response, "usage_metadata", None)
            if meta:
                usage.input_tokens  = getattr(meta, "prompt_token_count", 0) or 0
                usage.output_tokens = getattr(meta, "candidates_token_count", 0) or 0

            raw_text = response.text
            usage.latency_ms = (time.perf_counter() - t0) * 1000

            if not as_json:
                logger.debug(
                    "Gemini 완료 | %d tok | $%.6f | %.0fms",
                    usage.total_tokens, usage.cost_usd, usage.latency_ms,
                )
                return raw_text, usage

            # JSON 파싱 시도
            try:
                parsed = _parse_json_response(raw_text)
                logger.debug(
                    "Gemini JSON 완료 | %d tok | $%.6f | %.0fms",
                    usage.total_tokens, usage.cost_usd, usage.latency_ms,
                )
                return parsed, usage
            except (json.JSONDecodeError, ValueError) as exc:
                if attempt < _MAX_ATTEMPTS:
                    logger.warning("JSON 파싱 실패 (시도 %d/%d): %s", attempt, _MAX_ATTEMPTS, exc)
                    time.sleep(_RETRY_WAIT_S)
                    continue
                # 마지막 시도 실패 → 원문 반환
                logger.error("JSON 파싱 최종 실패. 원문 반환.")
                return raw_text, usage

        except Exception as exc:
            if attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "Gemini API 오류 (시도 %d/%d): %s — %.1f초 후 재시도",
                    attempt, _MAX_ATTEMPTS, exc, _RETRY_WAIT_S,
                )
                time.sleep(_RETRY_WAIT_S)
                continue
            logger.error("Gemini API 최종 실패: %s", exc)
            raise

    raise RuntimeError("Gemini 호출 예외 종료")


def gemini_analyze_stocks(
    stocks: list[dict[str, Any]],
    system_prompt: str | None = None,
    max_tokens_per_stock: int = 256,
) -> tuple[list[dict[str, Any]], list[GeminiUsage]]:
    """
    종목 리스트를 순서대로 AI 분석합니다.

    Args:
        stocks:               code, name 등 팩터 정보가 담긴 딕셔너리 리스트
        system_prompt:        공통 시스템 프롬프트 (None이면 기본값)
        max_tokens_per_stock: 종목당 최대 출력 토큰

    Returns:
        (results, usages) — AI 분석 필드가 추가된 딕셔너리 리스트, 호출별 GeminiUsage 리스트
    """
    if system_prompt is None:
        system_prompt = (
            "당신은 한국 주식 시장 전문 애널리스트입니다. "
            "제공된 종목 정보를 바탕으로 간결하고 정확한 투자 참고 분석을 제공합니다. "
            "투자 권유가 아닌 참고 정보임을 명심하세요."
        )

    results: list[dict[str, Any]] = []
    usages:  list[GeminiUsage]    = []

    for stock in stocks:
        user_prompt = (
            f"종목코드: {stock.get('code', '—')}\n"
            f"종목명: {stock.get('name', '—')}\n"
            f"시장: {stock.get('market', '—')}\n"
            f"종가: {stock.get('close', '—')}원\n"
            f"팩터 정보: {json.dumps(stock, ensure_ascii=False, default=str)}\n\n"
            "다음 JSON 형식으로 분석해주세요:\n"
            '{"score": 0~100점, "grade": "A|B|C", '
            '"comment": "2~3문장 분석", "key_reasons": ["이유1", "이유2"]}'
        )

        try:
            result, usage = gemini_chat(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens_per_stock,
                as_json=True,
            )
            stock_out = {**stock, **(result if isinstance(result, dict) else {"comment": str(result)})}
        except Exception as exc:
            logger.error("종목 %s 분석 실패: %s", stock.get("code"), exc)
            stock_out = {**stock, "comment": "분석 실패", "grade": "—"}
            usage     = GeminiUsage()

        results.append(stock_out)
        usages.append(usage)
        logger.info("AI 완료 | %s %s | $%.6f", stock.get("code"), stock.get("name"), usage.cost_usd)

    return results, usages


def total_cost(usages: list[GeminiUsage]) -> float:
    """GeminiUsage 리스트의 총 비용(USD)을 반환합니다."""
    return round(sum(u.cost_usd for u in usages), 6)


_BATCH_SIZE = 10  # 배치당 종목 수 (20/day 한도 → 30종목 = 3호출)


def gemini_analyze_stocks_with_key(
    stocks: list[dict[str, Any]],
    api_key: str,
    system_prompt: str | None = None,
    max_tokens_per_stock: int = 200,
) -> tuple[dict[str, dict], float]:
    """
    사용자 제공 API 키로 종목 배치 AI 분석.

    gemini-2.5-flash 무료 티어는 하루 20회이므로,
    10종목씩 배치 프롬프트로 묶어 API 호출 횟수를 최소화합니다.
    (30종목 = 3회 호출)

    Returns:
        (comments, cost_usd)
        - comments: {code: {"score": 75, "grade": "B", "comment": "...", "key_reasons": [...]}}
        - cost_usd: 총 API 비용
    """
    client = genai.Client(api_key=api_key)
    model_id = "gemini-2.5-flash"

    if system_prompt is None:
        system_prompt = (
            "당신은 한국 주식 시장 전문 애널리스트입니다. "
            "제공된 종목 정보를 바탕으로 간결하고 정확한 투자 참고 분석을 제공합니다. "
            "투자 권유가 아닌 참고 정보임을 명심하세요."
        )

    comments: dict[str, dict] = {}
    total_cost_usd = 0.0
    quota_exhausted = False

    # 배치 단위로 분할
    batches = [stocks[i:i + _BATCH_SIZE] for i in range(0, len(stocks), _BATCH_SIZE)]

    for batch_idx, batch in enumerate(batches):
        if quota_exhausted:
            # 할당량 소진 시 남은 배치는 건너뜀
            for stock in batch:
                comments[stock.get("code", "")] = {"comment": "일일 한도 초과 (내일 재시도)", "grade": "─"}
            continue

        # 배치 프롬프트 조립
        stock_lines = []
        batch_codes = []
        for stock in batch:
            code = stock.get("code", "")
            batch_codes.append(code)
            factors = {k: v for k, v in stock.items() if k not in ("code", "name", "market")}
            stock_lines.append(
                f"- {stock.get('name', '─')}({code}), {stock.get('market', '─')}, "
                f"종가 {stock.get('close', '─')}원, "
                f"팩터: {json.dumps(factors, ensure_ascii=False, default=str)}"
            )

        user_prompt = (
            f"다음 {len(batch)}개 종목을 각각 분석하세요:\n\n"
            + "\n".join(stock_lines)
            + "\n\n"
            "반드시 JSON 배열로 응답하세요. 각 항목은 다음 형식입니다:\n"
            '[{"code": "종목코드", "score": 0~100, "grade": "A|B|C", '
            '"comment": "1~2문장 매매 관점 분석", "key_reasons": ["이유1", "이유2"]}, ...]'
        )

        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            system_instruction=system_prompt,
            max_output_tokens=max_tokens_per_stock * len(batch),
            temperature=0.2,
        )

        # 재시도: 503(서버 과부하)만. 429(일일 한도)는 재시도 무의미
        last_exc = None
        resp = None
        for attempt in range(1, 4):
            try:
                resp = client.models.generate_content(
                    model=model_id,
                    contents=user_prompt,
                    config=config,
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc)

                # 429 일일 한도 → 재시도 하지 않고 즉시 포기
                if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                    logger.warning(
                        "Gemini 일일 한도 초과 (배치 %d/%d): %s",
                        batch_idx + 1, len(batches), msg[:120],
                    )
                    quota_exhausted = True
                    break

                # 503/500/502/504 → 일시 오류, 재시도
                retryable = any(s in msg for s in ("503", "500", "502", "504", "UNAVAILABLE"))
                if not retryable or attempt == 3:
                    break
                wait_s = 2 ** attempt  # 2s → 4s
                logger.warning(
                    "Gemini 일시 오류 (배치 %d, 시도 %d/3) → %ds 대기: %s",
                    batch_idx + 1, attempt, wait_s, msg[:120],
                )
                time.sleep(wait_s)

        # 실패 시 배치 전체 기본값 설정
        if last_exc is not None or resp is None:
            fail_msg = "일일 한도 초과 (내일 재시도)" if quota_exhausted else "분석 실패 (서버 오류)"
            for code in batch_codes:
                comments[code] = {"comment": fail_msg, "grade": "─"}
            continue

        # 응답 파싱
        try:
            raw = resp.text
            meta = getattr(resp, "usage_metadata", None)
            if meta:
                in_tok = getattr(meta, "prompt_token_count", 0) or 0
                out_tok = getattr(meta, "candidates_token_count", 0) or 0
                total_cost_usd += (in_tok / 1_000_000 * _PRICE_INPUT_PER_1M) + (out_tok / 1_000_000 * _PRICE_OUTPUT_PER_1M)

            parsed = _parse_json_response(raw)

            # 배열 응답 → 개별 종목 매핑
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "code" in item:
                        comments[item["code"]] = item
            elif isinstance(parsed, dict):
                # 단일 종목 응답 (배치 1개일 때)
                if "code" in parsed:
                    comments[parsed["code"]] = parsed
                elif len(batch_codes) == 1:
                    comments[batch_codes[0]] = parsed

            # 응답에 빠진 종목은 기본값
            for code in batch_codes:
                if code not in comments:
                    comments[code] = {"comment": "AI 응답 누락", "grade": "─"}

            logger.info(
                "Gemini 배치 %d/%d 완료 (%d종목)",
                batch_idx + 1, len(batches), len(batch),
            )

        except Exception as exc:
            logger.warning("Gemini 배치 %d 파싱 실패: %s", batch_idx + 1, exc)
            for code in batch_codes:
                comments[code] = {"comment": "응답 파싱 실패", "grade": "─"}

    return comments, round(total_cost_usd, 6)
