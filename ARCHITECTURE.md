# AlphaForge KR — Architecture Reference

> **읽는 대상**: Claude Code, Gemini Antigravity, 그리고 미래의 개발자.
> 이 문서를 먼저 읽으면 코드베이스 전체를 5분 내에 파악할 수 있습니다.

---

## 1. 시스템 개요

한국 증시(KOSPI/KOSDAQ) 종목 스크리닝을 **노드 기반 DAG 워크플로우**로 구성하고,
결과를 HTML 리포트 / 텔레그램으로 자동 배포하는 퀀트 도구입니다.

```
[데이터 수집] → [DAG 실행 엔진] → [노드 체인 실행] → [HTML 리포트 / 텔레그램]
     ↑                                                         ↓
  pykrx / DART / KRX                               Jinja2 렌더링 / python-telegram-bot
```

---

## 2. 폴더 구조

```
jason_octopus/
│
├── ARCHITECTURE.md        ← 이 파일 (구조 청사진)
├── README.md              ← 설치 및 실행 가이드
├── requirements.txt       ← Python 의존성 (pip + venv)
├── .env.example           ← 시크릿 템플릿 (실제 키는 .env에)
├── .gitignore
│
├── backend/               ← FastAPI 서버 (API 엔드포인트)
│   ├── main.py            ← FastAPI app 진입점
│   ├── routes/            ← 라우터 (run, node, report)
│   └── models.py          ← Pydantic 요청/응답 모델
│
├── engine/                ← DAG 실행 코어 (시스템의 심장)
│   ├── dag.py             ← DAG 클래스: 토폴로지 정렬, 캐시, 부분 재실행
│   ├── node_base.py       ← BaseNode 추상 클래스
│   └── cache.py           ← 실행 결과 캐시 (SQLite 기반)
│
├── nodes/                 ← 개별 노드 구현 (각 파일 = 노드 1개)
│   ├── universe.py        ← 국장 종목 (KOSPI+KOSDAQ 전체)
│   ├── vcp.py             ← VCP 패턴 찾기
│   ├── box_breakout.py    ← 박스권 돌파 찾기
│   ├── ma_alignment.py    ← 이평선 정배열 (5>20>60)
│   ├── foreign_flow.py    ← 외국인 누적 순매수
│   ├── institution_flow.py← 기관 누적 순매수
│   ├── and_filter.py      ← AND 필터 (교집합)
│   ├── or_filter.py       ← OR 필터 (합집합)
│   ├── score_filter.py    ← 점수 임계값 필터
│   ├── top_n.py           ← 상위 N개 선택
│   ├── ai_analysis.py     ← Gemini Flash LLM 분석
│   └── news_search.py     ← Perplexity 뉴스 검색 (선택)
│
├── llm/
│   └── gemini.py          ← Gemini Flash API 어댑터 (모든 LLM 호출은 여기서)
│
├── data/
│   ├── krx.py             ← KRX 시세·수급 수집 (pykrx 래퍼)
│   ├── dart.py            ← DART 공시·재무 수집
│   ├── cache.py           ← Parquet 기반 로컬 캐시
│   └── holidays.py        ← KRX 휴장일 캘린더
│
├── backtest/
│   └── engine.py          ← 백테스트 엔진 (생존편향·look-ahead 제거)
│
├── scheduler/
│   └── jobs.py            ← APScheduler 기반 KRX 장 마감 후 자동 실행
│
├── notify/
│   └── telegram.py        ← 텔레그램 봇 전송
│
├── frontend/
│   ├── styles.css         ← Claude 톤 CSS 변수 및 공통 스타일
│   ├── template_base.html ← 모든 리포트가 상속하는 기본 템플릿 (Jinja2)
│   └── templates/         ← 리포트별 Jinja2 템플릿
│       ├── report_screen.html   ← 스크리닝 결과 리포트
│       └── report_backtest.html ← 백테스트 결과 리포트
│
└── tests/
    ├── test_dag.py
    ├── test_data.py
    └── golden/            ← 특정일 기준 골든셋 (회귀 테스트)
        └── 2023-10-01.json
```

---

## 3. 데이터 흐름 (핵심 규칙)

### 노드 입출력 계약

모든 노드는 동일한 인터페이스를 따릅니다:

```python
# 입력: pandas DataFrame, 컬럼 명세 따름
# 출력: pandas DataFrame, 동일 컬럼 명세 (필터 시 행 감소, 점수 추가 시 컬럼 증가)

class BaseNode:
    INPUT_SCHEMA:  dict[str, str]   # {"code": "str", "name": "str", ...}
    OUTPUT_SCHEMA: dict[str, str]   # 출력 컬럼 명세
    PARAMS_SCHEMA: dict             # Pydantic 파라미터 스키마

    def run(self, df: pd.DataFrame, params: dict, as_of_date: str) -> pd.DataFrame:
        ...
```

### 표준 DataFrame 컬럼

모든 노드가 공유하는 기본 컬럼 (이 컬럼은 절대 제거 금지):

| 컬럼명 | 타입 | 설명 |
|--------|------|------|
| `code` | str | 종목코드 (6자리, 예: "005930") |
| `name` | str | 종목명 |
| `market` | str | "KOSPI" 또는 "KOSDAQ" |
| `close` | float | 종가 |
| `volume` | int | 거래량 |

---

## 4. DAG 엔진 핵심 설계

```
Node A → Node B → Node C (선형)
              ↘ Node D     (분기)
Node E ↗             (합류 → AND 노드)
```

- **캐시 키**: `hash(node_id + params + upstream_signature + as_of_date)` → 동일 조건 재실행 방지
- **부분 재실행**: 특정 노드 params 변경 시 해당 노드 + 하위 노드만 재실행
- **타입 검증**: 엣지 연결 시 upstream OUTPUT_SCHEMA ⊇ downstream INPUT_SCHEMA 확인
- **실패 격리**: 노드 1개 실패 시 전체 DAG 중단, 실패 노드에 에러 표시

---

## 5. LLM 어댑터 (Gemini Flash)

```python
# 모든 LLM 호출은 반드시 llm/gemini.py를 통해서만 수행합니다.
# 직접 google.generativeai 호출 금지.

from llm.gemini import gemini_chat

result = gemini_chat(
    system_prompt="...",
    user_prompt="...",
    max_tokens=1024,
    as_json=True,    # JSON 모드 강제 시
)
```

- 모델: `gemini-2.5-flash-preview-05-20` (무료 티어 최신)
- 재시도: 최대 3회, 1초 간격
- JSON 강제: 응답 파싱 실패 시 자동 재시도

---

## 6. 확장 규칙 (Antigravity / 협업 AI용)

**노드 추가 시**: `nodes/` 아래 파일 1개 생성 → `BaseNode` 상속 → `nodes/__init__.py`에 등록.

**새 API 엔드포인트 추가 시**: `backend/routes/` 아래 파일 1개 → `main.py`에 include.

**CSS 수정 시**: `frontend/styles.css`의 CSS 변수(`:root`)만 변경. 직접 색상 코드 하드코딩 금지.

**의존성 추가 시**: `requirements.txt`에 버전 고정(예: `pandas==2.2.2`).

---

## 7. 환경 변수 목록

| 변수 | 설명 | 필수 |
|------|------|------|
| `GEMINI_API_KEY` | Google AI Studio 키 | ✅ |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 | 선택 |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID | 선택 |
| `DART_API_KEY` | OpenDartReader 키 | 선택 |
| `DATA_CACHE_DIR` | Parquet 캐시 저장 경로 | 기본: `./data/cache` |
| `DB_PATH` | SQLite 실행 결과 DB | 기본: `./data/runs.db` |

---

## 8. 금지 사항 (DO NOT)

- `backend/`에 비즈니스 로직 작성 금지 → `engine/` 또는 `nodes/`로
- `nodes/`에서 직접 API 호출 금지 → `data/` 또는 `llm/` 모듈을 통해서
- `frontend/styles.css` 외에 인라인 스타일로 색상 코드 직접 입력 금지
- `.env` 파일 git 커밋 금지 (`.gitignore`에 포함됨)
- 전역 변수·싱글턴 패턴 사용 금지 (테스트 격리 불가)
