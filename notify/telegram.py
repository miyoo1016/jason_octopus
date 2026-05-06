"""
텔레그램 알림 모듈.
"""
import logging
from typing import Any
import pandas as pd
from telegram import Bot
from telegram.constants import ParseMode

from backend.config import settings

logger = logging.getLogger(__name__)

async def send_telegram_message(text: str) -> bool:
    """
    설정된 텔레그램 봇 토큰과 채팅 ID로 메시지를 비동기 발송합니다.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("텔레그램 설정이 없어 메시지를 발송하지 않습니다.")
        return False
        
    try:
        bot = Bot(token=settings.telegram_bot_token)
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN
        )
        logger.info("텔레그램 메시지 발송 완료")
        return True
    except Exception as exc:
        logger.error(f"텔레그램 발송 실패: {exc}")
        return False

def format_results_to_markdown(df: pd.DataFrame, title: str = "AlphaForge 결과") -> str:
    """
    분석 완료된 DataFrame을 마크다운 표 형식으로 변환합니다.
    """
    if df.empty:
        return f"📊 *{title}*\n\n조건을 만족하는 종목이 없습니다."
        
    lines = [f"📊 *{title}*", ""]
    lines.append("순위 | 종목명(코드) | 매수 매력도 | 주요 근거")
    lines.append("---|---|---|---")
    
    for i, (_, row) in enumerate(df.iterrows(), 1):
        name = row.get("name", "")
        code = row.get("code", "")
        grade = row.get("grade", "-")
        comment = row.get("comment", "-")
        
        # 마크다운 특수문자 이스케이프
        comment = str(comment).replace("|", ",").replace("\n", " ")
        
        lines.append(f"{i} | **{name}** ({code}) | {grade} | {comment}")
        
    return "\n".join(lines)
