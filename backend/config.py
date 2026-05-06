"""
환경 변수 로더 — pydantic-settings 기반.
모든 모듈은 settings 인스턴스를 import해서 사용합니다.

    from backend.config import settings
    key = settings.gemini_api_key
"""
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",          # .env에 정의 안 된 키 무시
    )

    # ── LLM (필수) ────────────────────────────────────────────────────────────
    gemini_api_key: str = Field(..., description="Google AI Studio API 키")
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini 모델 ID (무료 티어 안정 모델)",
    )

    # ── 텔레그램 (선택) ───────────────────────────────────────────────────────
    telegram_bot_token: str = Field(default="", description="텔레그램 봇 토큰")
    telegram_chat_id: str = Field(default="", description="텔레그램 채팅 ID")

    # ── DART (선택) ───────────────────────────────────────────────────────────
    dart_api_key: str = Field(default="", description="OpenDartReader API 키")

    # ── 스토리지 ──────────────────────────────────────────────────────────────
    data_cache_dir: Path = Field(
        default=Path("./data/cache"),
        description="Parquet 캐시 저장 경로",
    )
    db_path: Path = Field(
        default=Path("./data/runs.db"),
        description="SQLite 실행 결과 DB 경로",
    )

    # ── 서버 ──────────────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    debug: bool = Field(default=False)

    def ensure_dirs(self) -> None:
        """필요한 디렉터리를 자동 생성합니다."""
        self.data_cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def dart_enabled(self) -> bool:
        return bool(self.dart_api_key)


# 모듈 단위 싱글턴 (테스트 시 monkeypatch로 교체 가능)
settings = Settings()
