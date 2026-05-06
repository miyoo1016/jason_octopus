# AlphaForge KR (Quantitative DAG Engine)

AlphaForge KR은 한국 주식 시장(KOSPI/KOSDAQ)을 대상으로 설계된 고성능 **노드 기반(DAG) 퀀트 파이프라인 스크리닝 엔진**입니다. 수천 개의 종목 데이터를 초고속으로 필터링하고, AI(Gemini Flash) 분석을 통합하며, 생존 편향이 제거된 정교한 백테스트 결과를 제공합니다.

## 🚀 주요 기능 (Features)

1. **DAG 기반 위상 정렬 및 캐싱 (Core Engine)**
   - Kahn의 위상 정렬 알고리즘을 사용해 각 노드의 의존성을 해석하여 순차적/병렬적으로 실행합니다.
   - 각 노드의 입력 파라미터, 상위 노드의 상태 해시(SHA256)를 기반으로 **결정론적 캐시(Deterministic Cache)**를 생성하여 멱등성을 보장하고 재실행 속도를 극대화합니다.
2. **모듈화된 퀀트 노드 12종**
   - VCP 패턴, 박스권 돌파, 이평선 정배열(5>20>60), 외국인/기관 수급 분석 등 다양한 시그널 모듈을 조립하여 복잡한 전략을 단숨에 구축합니다.
3. **AI 통합 분석 (Google Gemini 3.1 Pro / Flash)**
   - 필터링된 유망 종목의 시계열 및 팩터 데이터를 바탕으로, LLM이 투자 매력도와 상세 코멘트를 작성합니다.
4. **엄격한 백테스트 엔진**
   - 미래 참조(Look-ahead Bias) 방지를 위해 시그널 발생 다음 날(T+1) 시가에 진입하도록 설계.
   - 한국 시장 거래세(0.18%) 및 수수료/슬리피지 비용 기본 내장. 손절/익절(SL/TP) 트래킹 기능 포함.
5. **Claude 톤 디자인의 리포트 렌더러**
   - 미려하고 차분한 Claude AI 톤앤매너로 구성된 HTML 스크리닝/백테스트 리포트 제공 (FastAPI 기반).
6. **APScheduler 자동화 및 Telegram 알림**
   - 휴장일을 피해 평일 지정 시간에 실행되며 결과물은 텔레그램 메신저로 자동 전송됩니다.

## ⚙️ 기술 스택 (Tech Stack)
- **Core**: Python 3.12, Pandas, Pykrx
- **Engine**: NetworkX (Topological logic), Hashlib (Cache), Pydantic
- **Web & UI**: FastAPI, Jinja2, Vanilla CSS
- **Testing**: Pytest (100% Core coverage)

## 📦 설치 및 실행 방법

### 1. 환경 설정
```bash
git clone https://github.com/your-username/jason_octopus.git
cd jason_octopus

# 가상환경 생성 및 활성화
python3 -m venv venv
source venv/bin/activate  # Mac/Linux

# 의존성 설치
pip install -r requirements.txt
```

### 2. API 키 입력
`.env.example` 파일을 복사하여 `.env`를 생성하고 API 키를 입력하세요.
```bash
cp .env.example .env
```
```env
# .env 내용
GEMINI_API_KEY="AI 분석을 위한 구글 제미나이 API 키"
TELEGRAM_BOT_TOKEN="텔레그램 봇 토큰 (선택)"
TELEGRAM_CHAT_ID="텔레그램 채팅 ID (선택)"
```

### 3. 데모 스크립트 실행
터미널에서 명령어 한 줄로 엔진 작동 과정을 체험할 수 있습니다.
```bash
python run_demo.py
```

### 4. 웹 UI (FastAPI) 구동
Claude 톤 디자인의 리포트 렌더러를 보려면 서버를 실행하세요.
```bash
uvicorn backend.main:app --reload
```
브라우저에서 `http://127.0.0.1:8000/report/screen` 접속.

## 🧪 테스트 코드 구동
엔진의 무결성과 골든셋 회귀 테스트를 검증합니다.
```bash
pytest tests/ -v
```

## 🤝 기여 (Contributing)
이 엔진은 추상화된 `BaseNode` 인터페이스를 제공합니다. 누구나 `nodes/` 디렉터리에 새로운 로직의 `.py` 파일을 생성하고 `INPUT_ARITY`, `OUTPUT_COLUMNS`, 그리고 `run()` 메서드만 구현하면 새로운 퀀트 지표를 무한히 확장할 수 있습니다.

---
**AlphaForge AI Research** - Built with Agentic AI Collaboration.
