# Project Structure

루트에는 현재 크론/봇이 직접 실행하는 운영 파일을 유지한다. import 경로가 안정화되기 전까지 기존 Python 모듈은 대량 이동하지 않는다.

## 폴더

- `docs/`: 문서, 설계, 디버그 노트 링크
- `docs/plans/`: 구현 계획과 단계별 작업 문서
- `scripts/`: 보조 실행 스크립트
- `tests/`: pytest 테스트
- `kiwoom_sync/`: 키움 동기화 관련 하위 모듈
- `logs/`: 로컬 로그
- `data/backups/portfolio/`: 오래된 포트폴리오 스냅샷 백업
- `reports/raw/`: SaveTicker 기사/리포트 원본 아카이브
- `reports/text/`: 원본에서 추출한 텍스트 사이드카

## 루트에 남기는 파일

- `telegram_bot.py`, `barbell_strategy.py`, `investment_report.py`, `market_report.py`
- `holding_commands.py`, `holding_manager.py`, `order_generator.py`, `stock_advisor.py`
- `portfolio_snapshot.json`, `target_weights.json`, `dca_weights.json`, `price_alerts.json`, `leverage_state.json`
- `deliver_*.sh`, `bot_watchdog.sh`, `sync_server_watchdog.sh`

이 파일들은 크론, 봇, 문서, 테스트에서 직접 참조하므로 별도 리팩터링 단계 없이 이동하지 않는다.
