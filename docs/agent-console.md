# Stock Agent Console

`agent_console`은 기존 `stock-report` 옆에 붙는 로컬 우선 콘솔이다. 외부 GUI를 통째로 합치지 않고, 지금 프로젝트가 이미 만드는 리포트/모의투자/ML 원장/뉴스 캐시를 읽어 대화형 컨텍스트 레이어와 World Memory를 제공한다.

## 포함한 것

- 대화형 에이전트: 현재 화면(`market`, `portfolio`, `ticker`, `paper`, `lab`)에 맞춰 최근 이벤트, 누적 기억, ML/모의투자 상태를 묶어 답변한다.
- World Memory: SQLite에 시장 이벤트, 리포트, 모델 활동, 수동 메모를 누적한다.
- Context Layer: 기존 `~/reports`, `~/reports/source-cache`, `~/reports/ml-data`를 읽어 모든 화면에서 필요한 근거를 API로 제공한다.
- Portfolio Lab: 실제 자산 연동 없이 전략 가설, 비중, 손실한도, 운용 규칙을 저장한다.
- Local Install Prompt: 노트북에서 같은 콘솔을 설치하도록 붙여넣을 프롬프트를 제공한다.

## 의도적으로 뺀 것

- Arca 수집 UI: Cloudflare challenge와 브라우저 쿠키 의존성이 있어 서버 사이드 통합에서 깨지기 쉽다.
- Toss 실제 자산 연동: FinanceAgentGUI의 Toss API는 읽기 전용 데모 성격이고, 개인 인증 정보를 다루므로 v0에서 제외한다.
- 자동 실주문: 이 콘솔은 분석/검증/시나리오 저장 전용이다.
- Binance/Magazine류 부가 탭: 현재 투자 워크플로우와 직접 연결되지 않아 제외한다.

## 실행

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
- `AGENT_CONSOLE_HOST`: 바인드 주소. 기본값은 `127.0.0.1`.
- `AGENT_CONSOLE_PORT`: 포트. 기본값은 `8797`.

## API

- `GET /api/health`
- `GET /api/context/overview?surface=market&hours=72`
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
