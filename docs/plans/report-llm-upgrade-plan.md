# 레포트 LLM 업그레이드 계획

작성일: 2026-06-05

## 파일 정리 점검

현재 `investment_report.py`는 실행 진입점, 데이터 수집, 점수/신호 결합, Markdown/TXT 출력, LLM overlay를 함께 가진 큰 파일이다. 기능별 분리는 가능하지만, 아침 cron 경로(`deliver_investment_report_cron.sh` → `investment_report.py`)와 기존 테스트 import 경로가 직접 연결되어 있다.

이번 작업에서는 실행 경로를 깨뜨리지 않는 것이 우선이므로 파일 이동은 하지 않는다. 정리가 필요하면 다음 순서로 별도 작업에서 진행한다.

1. `investment_report_llm.py`로 LLM prompt/fact guard/output formatting 함수만 이동
2. 기존 함수명 re-export 또는 import 경로 유지
3. `tests/test_investment_report.py` focused green 확인
4. bounded live sample 확인
5. cron wrapper는 그대로 유지

## LLM 가독성 개선 계획

1. Python 산출값을 계속 source of truth로 둔다.
2. LLM은 숫자 계산/사실 생성 없이 editor overlay만 작성한다.
3. 모바일 summary는 아래 4개 고정 섹션으로 읽히게 한다.
   - 오늘의 해석
   - 오늘 할 일
   - 리스크 확인
   - 추가 확인
4. fact guard는 입력 JSON/source digest에 없는 숫자·티커를 계속 차단한다.
5. LLM 실패/guard 실패/timeout 시 deterministic report를 그대로 유지한다.
6. 향후 개선은 source digest 품질 확대와 prompt payload 선정 기준 개선으로 제한한다.

## 이번 즉시 구현 범위

- LLM prompt에 "숫자/티커는 입력 JSON 표현만 사용" 규칙을 더 명시한다.
- 모바일 summary의 LLM 섹션 제목을 이모지 라벨로 구조화한다.
- 기존 fact guard와 fallback 동작은 변경하지 않는다.
