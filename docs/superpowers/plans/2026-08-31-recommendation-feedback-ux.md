# 추천 피드백 루프와 응답 UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 콘솔의 첫 응답을 빠르고 정직하게 표시하고, 자동 진입 추천을 하나의 compact digest로 전달하며, 추천 시점과 실제 가격 경로를 연결해 단기·스윙 성과를 측정하고 OOS 검증을 통과한 경우에만 파라미터 후보를 shadow 정책으로 승격한다.

**Architecture:** 기존 agent/entry analyzer/append-only ledger를 유지한다. UI는 실제 발생한 진행 이벤트만 상태로 노출한다. 텔레그램은 자동 알림과 상세 조회를 분리한다. feedback은 기존 20/60일 레코드와 호환되는 새 horizon 레코드를 추가하고, time-split champion/challenger 게이트 뒤에만 정책 파일을 쓴다.

**Tech Stack:** Python 3.11, Streamlit, pandas, existing `ml.adaptive.Ledger`, existing Telegram sender and JSONL stores, pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-recommendation-feedback-ux-design.md`

## Global Constraints

- 기존 `entry_signals_decisions.jsonl`와 `entry_signals_outcomes.jsonl`를 수정하지 않고 새 레코드만 append한다.
- 과거 레코드는 `legacy` 프로필로 읽을 수 있어야 한다.
- 가격·벤치마크·freshness가 없으면 성공/실패를 추정하지 않고 `pending` 또는 품질 오류로 남긴다.
- 추천은 정보형이며 자동 주문 경로와 섞지 않는다.
- 기존 `/signals entry TICKER` 상세 메시지와 `format_alert_message()`의 계약은 유지한다.
- 모든 새 순수 로직은 먼저 실패 테스트를 추가한 뒤 구현한다.
- 자동 정책 반영은 기존 환경 플래그와 shadow 게이트를 우회하지 않는다.

## Task 1: Baseline and test fixtures

**Files:** `tests/test_entry_feedback.py`, `tests/test_entry_analyzer.py`, `tests/test_dashboard_pages.py`, `tests/test_telegram_bot.py` (또는 현재 관련 테스트 파일), `tests/conftest.py`

- [ ] 현재 entry feedback, alert formatter, Telegram notify, AI console 테스트의 fixture와 기존 레코드 모양을 확인한다.
- [ ] 새 테스트에서 사용할 `EntryScore`, daily legacy decision, intraday decision, OHLC/benchmark fixture를 최소 데이터로 만든다.
- [ ] 실행 명령과 예상 baseline을 기록하고, 테스트가 새 기능 없이 실패하는지 확인한다.

## Task 2: Compact Telegram digest formatter

**Files:** `ml/entry_analyzer.py`, `tests/test_entry_analyzer.py`

- [ ] `format_entry_digest(scores, as_of=None)`에 대한 실패 테스트를 작성한다.
- [ ] 점수 내림차순 최대 5개, 티커·현재가·점수·신뢰도·관찰 구간·목표·무효화선·대표 horizon·표본 수·주의 문구·기준 시각을 출력하도록 구현한다.
- [ ] KRW/USD 가격 형식을 검증하고, 후보 하나의 잘못된 필드가 전체 digest를 깨지 않도록 후보 단위 graceful fallback을 넣는다.
- [ ] 4,000자 제한에서 후보 수를 줄이는 순수 helper를 추가하고, 줄 단위 분할은 최후의 fallback으로만 사용한다.
- [ ] 기존 `format_alert_message()`와 단일 종목 상세 명령의 결과가 변하지 않는 회귀 테스트를 통과시킨다.

## Task 3: Group automatic Telegram delivery

**Files:** `telegram_bot.py`, `bot/entry_commands.py`, `tests/test_telegram_bot.py`, `tests/test_entry_commands.py`

- [ ] 신규 alert 여러 건이 후보별 발송되지 않고 한 번의 digest로 묶이는 실패 테스트를 작성한다.
- [ ] `notify_entry_signals()`에서 기존 cooldown과 `_register_trade_level_alerts()`는 유지하고, 발송 대상만 digest로 묶는다.
- [ ] 후보가 없으면 빈 메시지를 보내지 않는 테스트를 추가한다.
- [ ] Telegram 발송 실패와 ledger 기록을 분리하고, digest 발송 이후에도 종목별 가격 알림 등록이 유지되는지 검증한다.
- [ ] `/signals entry TICKER`는 긴 상세 포맷, `/signals entry` universe 조회는 기존 보고서 계약을 유지한다.

## Task 4: Recommendation event idempotency and metadata

**Files:** `ml/entry_feedback.py`, `ml/entry_analyzer.py`, `tests/test_entry_feedback.py`

- [ ] model/parameter/feature version, session, evaluation profile, freshness를 포함하는 decision metadata 테스트를 작성한다.
- [ ] daily 레코드 ID는 기존 형식을 유지하고, intraday는 enter 전환 또는 cooldown 만료 이벤트만 기록하는 event ID를 정의한다.
- [ ] 같은 종목의 반복 실행이 같은 session bucket에서 중복 decision을 추가하지 않는지 검증한다.
- [ ] 기존 `score_to_decision()` 호출자가 새 인자를 주지 않아도 동작하도록 기본값을 둔다.

## Task 5: Market-aware outcome horizons

**Files:** `ml/entry_feedback.py`, `crons/entry_signal_feedback.py`, `tests/test_entry_feedback.py`

- [ ] `30m`, `1h`, `4h`, `1d`, `3d`, `5d`, `20d` maturity와 휴장일 fixture를 먼저 테스트한다.
- [ ] 장중 horizon은 intraday bar, 스윙 horizon은 거래일 bar를 사용하고, 단순 wall-clock elapsed가 휴장일을 outcome으로 만들지 않게 한다.
- [ ] `direction_hit`, `stock_ret`, `benchmark_ret`, `excess_ret`, `target_first`, `stop_first`, `mfe`, `mae`, time-to-level, gross/net return, fee/slippage를 outcome에 추가한다.
- [ ] 같은 bar에서 목표와 무효화선이 모두 닿으면 stop 우선인 기존 보수적 규칙을 유지한다.
- [ ] 데이터가 부족하거나 timestamp가 역전되면 outcome을 쓰지 않고 `pending`/품질 상태로 처리한다.
- [ ] 구형 20/60일 outcome과 새 horizon이 함께 백필되는지 cron 요약 회귀 테스트를 추가한다.

## Task 6: Feedback diagnostics and OOS challenger

**Files:** `ml/entry_feedback.py`, `crons/entry_adaptive_learn.py`, `ml/adaptive.py` (필요 시), `tests/test_entry_feedback.py`, `tests/test_entry_adaptive_learn.py`

- [ ] horizon·market·factor별 방향 적중, 평균 excess, 평균 R, MFE/MAE, target/stop/none 집계를 테스트한다.
- [ ] 최소 30건 전체·10건 OOS, 양의 초과수익, MDD/평균 손실 제한, 항목별 ±0.04·총 ±0.08 cap을 검증한다.
- [ ] time-split champion/challenger가 동일 OOS 구간을 사용하고 표본 부족·OOS 미개선·MDD 초과를 거절하도록 구현한다.
- [ ] 초기 자동 후보는 enter threshold와 기존 factor adjustment만 포함하고 target/stop/holding period 튜닝은 보류한다.
- [ ] 채택된 후보에 `policy_version`, 표본 수, OOS 기간, 채택 사유를 기록하고 shadow 파일에만 저장한다.
- [ ] 기존 adaptive policy가 새 outcome 하나 때문에 즉시 live 변경되지 않는지 검증한다.

## Task 7: AI console response state contract

**Files:** `agent_console/agent.py`, `dashboard/pages/ai_console.py`, `agent_console/context.py` (필요 시), `tests/test_agent_console.py`, `tests/test_dashboard_pages.py`

- [ ] `context_ready`, `tools_started`, `llm_started`, `answer_ready`, `postprocess_queued`, `failed` 이벤트를 실제 호출 지점에서 기록하는 실패 테스트를 작성한다.
- [ ] provider가 이벤트를 제공하지 않는 경우 한 개의 `분석 중` 상태만 표시하고, 실행하지 않은 도구 단계는 표시하지 않게 한다.
- [ ] 질문·맥락·기준 시각을 먼저 표시하고 핵심 판단·근거·반대 근거/리스크·출처/시점·다음 확인 순서로 렌더링한다.
- [ ] async wiki postprocess가 답변 표시를 막지 않고 `postprocess_queued`로만 표시되는지 검증한다.
- [ ] 기존 LLM 우선과 규칙 fallback 표기, 엔진 메타데이터, 질문 라우팅 회귀를 유지한다.

## Task 8: Integration verification and rollout guard

**Files:** `tests/test_entry_feedback.py`, `tests/test_entry_analyzer.py`, `tests/test_telegram_bot.py`, `tests/test_agent_console.py`, `tests/test_dashboard_pages.py`, `docs/superpowers/specs/2026-08-31-recommendation-feedback-ux-design.md` (필요 시)

- [ ] 관련 테스트를 실행하고 digest shadow 출력과 기존 alert 출력의 차이를 fixture snapshot으로 확인한다.
- [ ] 실제 외부 발송 없이 후보 없음, 후보 1건, 후보 5건, 4,000자 경계, send 실패를 검증한다.
- [ ] 최소 한 주 운영 전에는 shadow/opt-in 플래그를 유지하는 운영 문구와 상태 표시를 확인한다.
- [ ] 전체 회귀 테스트를 실행하고, 기존에 알려진 `scripts/cloudflared_watchdog.sh` 문자열 기대 실패는 현재 작업과 분리해 보고한다.

## Definition of Done

- [ ] 자동 추천 한 번의 Telegram 메시지에서 상위 후보의 가격·위험·기준 시각을 빠르게 읽을 수 있다.
- [ ] `/signals entry TICKER` 상세 조회는 유지된다.
- [ ] 새 추천 decision과 maturity 가능한 horizon outcome이 append-only로 연결된다.
- [ ] 표본 부족, stale, OOS 미개선 상태에서는 정책이 바뀌지 않는다.
- [ ] AI 콘솔이 실제 수행한 단계만 표시하고 핵심 판단을 먼저 렌더링한다.

## Trade-offs

Digest는 알림 수와 인지 부하를 줄이는 대신 낮은 순위 후보의 즉시 노출을 줄인다. 짧은 horizon은 표본을 빨리 만들지만 상관된 표본과 비용 왜곡이 커지므로 threshold 보정의 참고 자료로 시작한다. 자동 튜닝은 빠르지만 regime change에 취약하므로 OOS와 shadow 단계가 필요하다.
