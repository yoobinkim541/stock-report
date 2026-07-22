# 로컬 노트북 설치 요청 프롬프트

아래 내용을 로컬 노트북의 Codex/개발 에이전트에게 그대로 전달하세요.

```text
내 노트북에 stock-report의 Agent Console을 설치해서 실행해줘.

목표:
- 기존 stock-report 저장소를 받아서 agent_console을 로컬에서 실행한다.
- 브라우저에서 http://127.0.0.1:8797 로 접속할 수 있게 한다.
- SaveTicker 원본과 추출 텍스트도 함께 옮길 수 있으면 ~/reports/raw 와 ~/reports/text 를 복사한다.
- 실제 주문, Toss 실제 자산 연동, Arca Cloudflare 우회는 하지 않는다.
- 우선 로컬 파일 기반 World Memory, Context Layer, Portfolio Lab만 동작시키면 된다.

절차:
1. 저장소를 클론하거나 기존 폴더를 최신화한다.
   - repo: https://github.com/devninjadev/stock-report.git
   - 브랜치가 지정되어 있으면 그 브랜치를 checkout한다.
2. Python 3.11 이상을 확인한다.
3. 프로젝트 루트에서 가상환경을 만든다.
   - macOS/Linux:
     python3 -m venv .venv
     . .venv/bin/activate
   - Windows PowerShell:
     py -3.11 -m venv .venv
     .\.venv\Scripts\Activate.ps1
4. 최소 실행 의존성을 설치한다.
   pip install flask python-dotenv
   전체 대시보드와 ML 기능까지 확인해야 하면 pip install -r requirements.txt 를 사용한다.
5. 필요하면 환경변수를 지정한다.
   - AGENT_CONSOLE_DB: 로컬 SQLite 파일 경로
   - AGENT_CONSOLE_REPORTS_DIR: 리포트 폴더
   - AGENT_CONSOLE_SOURCE_CACHE_DIR: source-cache 폴더
   - AGENT_CONSOLE_ML_DATA_DIR: ml-data 폴더
6. 실행한다.
   bash scripts/run_agent_console.sh
   Windows라면:
   .venv\Scripts\python -m agent_console.server
7. 브라우저에서 http://127.0.0.1:8797 을 연다.
8. 화면에서 새로고침, 메모리 적재, 에이전트 질문, 포트폴리오 시나리오 저장이 되는지 확인한다.

서버에 있는 기존 데이터까지 가져오려면:
- ~/reports/source-cache
- ~/reports/ml-data
- ~/reports/raw
- ~/reports/text
- ~/.local/share/stock-report/agent_console.sqlite3
이 세 경로를 노트북의 원하는 폴더로 복사한 뒤 위 환경변수를 그 경로에 맞춰 설정한다.

완료 기준:
- /api/health 가 ok true를 반환한다.
- Agent Console 첫 화면이 뜬다.
- market surface에서 질문을 보내면 답변이 나온다.
- Portfolio Lab에서 시나리오가 저장된다.
- 실제 매매나 개인 인증 연동은 수행하지 않는다.
```
