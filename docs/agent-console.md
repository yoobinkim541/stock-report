# Stock Agent Console

`agent_console`은 기존 `stock-report` 옆에 붙는 로컬 우선 콘솔이다. 외부 GUI를 통째로 합치지 않고, 지금 프로젝트가 이미 만드는 리포트/모의투자/ML 원장/뉴스 캐시를 읽어 대화형 컨텍스트 레이어와 World Memory를 제공한다.

Cloudflare로 접속 중인 기존 Streamlit 대시보드에서는 사이드바의 `AI 콘솔` 페이지로 같은 기능을 쓴다. 이 경우 별도 Flask 포트(`8797`)를 띄우지 않아도 된다.

## 포함한 것

- 대화형 에이전트: 현재 화면(`market`, `portfolio`, `ticker`, `paper`, `lab`)에 맞춰 최근 이벤트, 누적 기억, ML/모의투자 상태를 묶어 답변한다.
- World Memory: SQLite에 시장 이벤트, 리포트, 모델 활동, 수동 메모를 누적한다.
- Shared Memory: FinanceAgentGUI의 `data/shared-memory` 계약을 사용해 사용자 학습 메모리와 외부 시장 브리핑을 `memory_summary.md`로 묶어 프롬프트에 주입한다.
- Context Layer: 기존 `~/reports`, `~/reports/source-cache`, `~/reports/ml-data`를 읽어 모든 화면에서 필요한 근거를 API로 제공한다.
- Portfolio Lab: 실제 자산 연동 없이 전략 가설, 비중, 손실한도, 운용 규칙을 저장한다.
- Strategy Canvas: FinanceAgentGUI의 `portfolio-matrix-dsl` 방식으로 RSI 현금화 규칙을 실행하고, Sortino/Calmar/Ulcer/UPI/Beta까지 표준 평가 지표를 표시한다.
- Local Install Prompt: 노트북에서 같은 콘솔을 설치하도록 붙여넣을 프롬프트를 제공한다.
- Arca proxy ingest: 서버에 이미 열려 있는 SOCKS 터널(`socks5://127.0.0.1:1080`)을 이용해 아카라이브 주식채널 공개 글 조회를 시도하고, 성공한 글만 `source-cache`와 World Memory에 저장한다.

## 의도적으로 뺀 것

- Toss 실제 자산 연동: FinanceAgentGUI의 Toss API는 읽기 전용 데모 성격이고, 개인 인증 정보를 다루므로 v0에서 제외한다.
- 자동 실주문: 이 콘솔은 분석/검증/시나리오 저장 전용이다.
- Binance/Magazine류 부가 탭: 현재 투자 워크플로우와 직접 연결되지 않아 제외한다.

Arca는 Cloudflare challenge를 자동 우회하지 않는다. SOCKS 터널로 일반 공개 페이지를 조회해 보고, challenge가 나오면 실패 상태를 표시한다.

## 실행

### 기존 대시보드 안에서 사용

```bash
bash scripts/run_dashboard.sh
```

대시보드 사이드바에서 `AI 콘솔`을 연다.

### 별도 Flask 콘솔로 사용

```bash
cd /home/ubuntu/projects/stock-report
bash scripts/run_agent_console.sh
```

기본 주소는 `http://127.0.0.1:8797`이다.

다른 포트를 쓰려면:

```bash
AGENT_CONSOLE_PORT=8798 bash scripts/run_agent_console.sh
```

## 주요 환경변수

- `AGENT_CONSOLE_DB`: World Memory SQLite 경로. 기본값은 `~/.local/share/stock-report/agent_console.sqlite3`.
- `AGENT_CONSOLE_REPORTS_DIR`: 리포트 루트. 기본값은 `~/reports`.
- `AGENT_CONSOLE_SOURCE_CACHE_DIR`: 뉴스/매크로 이벤트 캐시. 기본값은 `~/reports/source-cache`.
- `AGENT_CONSOLE_ML_DATA_DIR`: ML/추천/성과 원장. 기본값은 `~/reports/ml-data`.
- `AGENT_CONSOLE_SHARED_MEMORY_DIR`: FinanceAgentGUI 호환 공유 메모리 루트. 기본값은 `data/shared-memory`.
- `AGENT_CONSOLE_SHARED_MEMORY_ENABLED`: `0`이면 공유 메모리 기록을 비활성화한다.
- `AGENT_CONSOLE_HOST`: 바인드 주소. 기본값은 `127.0.0.1`.
- `AGENT_CONSOLE_PORT`: 포트. 기본값은 `8797`.
- `STOCK_COLLECTOR_ARCA_PROXY`: Arca 조회에 사용할 프록시. 서버 기본 수동 버튼은 `socks5://127.0.0.1:1080`을 사용한다.

## API

- `GET /api/health`
- `GET /api/context/overview?surface=market&hours=72`
- `GET /api/memory`
- `POST /api/memory`
- `POST /api/memory/context`
- `POST /api/memory/ingest`
- `GET /api/memory/events`
- `POST /api/memory/events`
- `POST /api/agent/chat`
- `GET /api/agent/context-prompt`
- `GET /api/portfolio-lab/scenarios`
- `POST /api/portfolio-lab/scenarios`
- `GET /api/local-install-prompt`

## 운영 메모

이 콘솔은 기존 대시보드를 대체하지 않는다. 대시보드는 차트와 운영 화면, 에이전트 콘솔은 맥락 축적과 질문 응답, 전략 실험의 가벼운 작업대 역할로 나눈다.
