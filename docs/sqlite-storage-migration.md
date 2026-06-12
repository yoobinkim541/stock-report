# SQLite 저장소 통합 — 멀티유저 확장 기반

> 목적: 흩어진 JSON 파일 상태를 단일 SQLite DB로 통합하여 (1) 본인 시스템의
> 동시성·내구성을 강화하고, (2) `user_id` 차원을 도입해 향후 멀티유저 확장의
> 기반을 깐다.

## 왜 SQLite인가

- **멀티프로세스 안전**: 봇 상시 프로세스 + 다수 크론이 같은 상태를 동시 접근.
  WAL 모드 + `busy_timeout` 으로 파일 락 충돌·부분 쓰기 방지.
- **부분 갱신**: append-log를 전체 파일 재작성 없이 한 행만 추가.
- **멀티유저 기반**: 모든 레코드에 `user_id` 컬럼 — 확장 시 스키마 변경 없이
  `DEFAULT_USER` → 실제 chat_id 매핑만 분기하면 됨.

## 저장 모델 (`store.py`)

| API | 테이블 | 용도 |
|-----|--------|------|
| 컬렉션 `append`/`all`/`replace_all`/`count` | `collections(user_id, name, seq, item, created_at)` | append-log 리스트 (기록·이력) |
| 문서 `get_doc`/`put_doc` | `documents(user_id, key, data, updated_at)` | 단일 JSON blob (설정·상태) |
| 마이그레이션 `ensure_migrated`/`load_collection` | `migrations(user_id, name, done_at)` | 레거시 JSON 1회 import (멱등) |

- DB 경로: `~/.local/share/stock-report/stock_report.db` (env `STOCK_REPORT_DB` 로 override).
- 기본 사용자: `store.DEFAULT_USER = "default"` (실제 chat_id 하드코딩 금지 규칙 준수).
- 레거시 호환: 첫 접근 시 기존 JSON을 자동 import하되 **원본 파일은 보존**(롤백 대비).

## 마이그레이션 단계

### ✅ Phase 1 (완료) — 순수 기록로그

advisor(`bot/stock_advisor.py`)가 편집하지 않고, 단일 모듈이 소유하며, 외부에서
파일을 직접 읽지 않는 append-log만 이전. 라이브 매매·동기화 경로는 **미접촉**.

| 컬렉션 | 모듈 | 레거시 파일 |
|--------|------|-------------|
| `tax_records` | `tax_tracker.py` | `~/.local/share/stock-report/tax_records.json` |
| `portfolio_history` | `portfolio_tracker.py` | `…/portfolio_history.json` |
| `qqqi_dividends` | `portfolio_tracker.py` | `…/qqqi_dividends.json` |
| `signal_outcomes` | `telegram_bot.py` | `…/signal_outcomes.json` |

공개 함수 시그니처는 불변 → 호출부(텔레그램 봇·크론) 무수정.
검증: `tests/store_smoke_test.py` (네트워크 불필요, 16항목).

### ⬜ Phase 2 (예정) — 라이브 설정·상태

advisor 편집 대상 + 멀티라이터 + 라이브 브로커 경로라 신중히 이전해야 함.
각 파일의 모든 reader/writer를 함께 전환해야 함.

| 대상 | 현 위치 | 접근 모듈 | 주의 |
|------|---------|-----------|------|
| `portfolio_snapshot.json` | 루트 | holding_manager · barbell_strategy · portfolio_sync_server · kiwoom_sync_rest | 멀티라이터 + 라이브 키움 동기화 |
| `price_alerts.json` | 루트 | bot/price_alerts · telegram_bot | advisor 편집 대상 |
| `dca_weights.json` / `target_weights.json` | 루트 | barbell_strategy · holding_manager | advisor 편집 대상 |
| `leverage_state.json` | 루트 | 레버리지 신호 | advisor 편집 대상 |
| `barbell_state.json` / `barbell_anchor.json` | `~/.cache` | barbell_strategy · telegram_bot | fcntl 락과 함께 검토 |

> advisor가 파일을 직접 편집하는 워크플로는 Phase 2에서 store 문서 API 경유로
> 전환하거나, advisor에 store 접근 셰임을 제공해야 한다.

### ⬜ Phase 3 (예정) — 멀티유저 활성화

- 텔레그램 `chat_id → user_id` 레지스트리 (단일 `ALLOWED_CHAT_ID` 대체).
- 명령 핸들러 호출 체인에 `user` 컨텍스트 전파 (기본값 `DEFAULT_USER` 유지).
- 봇 프로세스는 **1개로 다수 사용자 처리** — `fcntl` 단일 인스턴스 락은 유지
  (중복 프로세스 방지 목적이므로 멀티유저와 무관).
- `portfolio_sync_server` 토큰 → user_id 매핑.

> ⚠️ 비기술 선결: 불특정 다수에게 구체적 매매신호 배포는 국내 유사투자자문업
> 규제 소지 — Phase 3 착수 전 법적 검토 필요.

## 롤백

레거시 JSON 원본은 삭제하지 않으므로, store 도입 이전 커밋으로 되돌리면
기존 파일 기반 동작이 그대로 복원된다. DB 파일(`stock_report.db`)만 제거하면 됨.
