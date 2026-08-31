# 추천 피드백 루프와 응답 UX 설계

## 1. 목표와 범위

추천을 더 자주 만드는 것이 아니라, 사용자가 한눈에 판단하고 추천의 실제 결과를 같은 기준으로 측정하며, 검증된 경우에만 다음 추천 정책을 개선하는 폐루프를 만든다.

범위는 세 가지다.

1. AI 콘솔의 첫 응답과 진행 상태를 짧고 정직하게 만든다.
2. 자동 진입 추천 텔레그램을 상위 후보 digest와 종목별 상세 조회로 분리한다.
3. 추천 시점 스냅샷과 실제 가격 경로를 연결해 단기·스윙 horizon별 성과를 측정하고, OOS 검증을 통과한 challenger만 보정 파라미터로 승격한다.

실거래 주문, 자동 매매, 외부 사용자별 알림 설정은 이 설계의 범위에 포함하지 않는다. 기존 정보형 추천과 모의투자 안전 경계를 유지한다.

## 2. 현재 구조와 문제

현재 코드에는 좋은 기반이 이미 있다.

- `ml/entry_feedback.py`가 추천 당시 피처를 append-only decision 원장에 저장한다.
- 20·60거래일 outcome을 가격 데이터와 벤치마크로 백필한다.
- 조건별 점수 보정과 60/40 시간순 OOS 검증, champion/challenger 게이트가 있다.
- `telegram_bot.notify_entry_signals()`가 전체 감시 점수를 기록한 뒤 신규 enter 알림을 보낸다.
- `/signals entry TICKER`가 단일 종목 상세 진입 분석을 제공한다.

문제는 다음과 같다.

- `format_alert_message()`가 한 종목당 긴 설명을 생성해 여러 후보가 나오면 핵심 가격과 위험이 묻힌다.
- 자동 알림이 후보별 개별 발송이라 알림을 훑는 시간이 길다.
- 현재 feedback horizon은 단기 추천의 즉시 방향성을 설명하기 어렵다.
- 진행 상태는 실제 작업 단계와 분리되어 있어 LLM 호출 중 같은 상태가 반복되거나, 아직 일어나지 않은 단계를 미리 표시할 수 있다.
- 결과를 보더라도 방향 적중, 벤치마크 초과, 목표/무효화선 도달을 하나의 성공 플래그로만 해석하면 어떤 파라미터를 바꿔야 하는지 불분명하다.

## 3. 제안 아키텍처

### 3.1 응답 UX

AI 콘솔은 다음 순서로 렌더링한다.

1. 질문 제출 직후: 질문, 현재 맥락, 데이터 시점, `분석 준비 중` 상태를 즉시 표시한다.
2. 데이터 준비 후: 한 줄 판단, 신뢰도, 핵심 수치 2~4개를 먼저 표시한다.
3. LLM 완료 후: 근거, 반대 근거, 리스크, 참고 문서, 사용한 도구와 엔진을 추가한다.

진행 상태는 실제 이벤트에만 대응한다. `context_ready`, `tools_started`, `llm_started`, `answer_ready`, `postprocess_queued`, `failed`를 상태 계약으로 두고, 이벤트를 알 수 없는 provider는 `분석 중` 단일 상태를 표시한다. 실제로 하지 않은 도구 조회나 LLM 호출을 완료한 것처럼 표시하지 않는다.

답변 본문은 다음 고정 순서를 사용한다.

- 핵심 판단
- 근거
- 반대 근거와 리스크
- 데이터 시점·출처
- 다음에 확인할 항목

상세 원문과 위키는 기본 답변을 길게 만들지 않고 참고 패널로 둔다. 기존 LLM 우선·규칙 기반 fallback 표시는 유지하되, 엔진과 fallback 여부를 메타데이터에 명시한다.

### 3.2 텔레그램 추천 digest

자동 알림 한 번의 실행에서 신규 후보를 점수 내림차순으로 정렬하고 최대 5개를 하나의 메시지로 보낸다. 후보가 없으면 메시지를 보내지 않는다. 같은 종목의 12시간 cooldown과 기존 알림 상태는 유지한다.

기본 digest 형식은 다음 필드만 포함한다.

```text
🎯 진입 후보 | 2026-08-31 09:30 KST

1) 🟢 NVDA · 점수 0.78 · 신뢰도 중간
   현재 $000 · 관찰 $000~$000
   목표 $000 (+x.x%) · 무효화 $000 (-x.x%)
   20d 승률 xx% · 기대 +x.x% · 표본 n건
   주의: 기술 추세 충돌

...

데이터 기준: 2026-08-31 09:30 KST
상세: /signals entry NVDA
⚠️ 정보형 추천 · 자동 주문 아님
```

필수 필드는 티커, 현재가, 점수·신뢰도, 관찰 구간, 목표·무효화선, 대표 horizon, 표본 수, 핵심 주의사항, 데이터 시점이다. CI·산식·60일 보조 설명·긴 해석은 상세 커맨드로 이동한다.

기존 `format_alert_message()`는 단일 종목 상세 포맷으로 유지한다. 신규 `format_entry_digest()`는 순수 함수로 만들고 시장/통화별 가격 형식과 줄 수를 테스트한다. Telegram 4,000자 제한을 넘으면 후보 수를 줄여 한 메시지로 유지하고, 그래도 넘을 때만 안전한 줄 단위 분할을 사용한다.

### 3.3 추천 성과 원장

기존 decision/outcome 원장을 확장하되 기존 레코드를 수정하지 않는다.

decision에는 다음 메타데이터를 추가한다.

- `decision_id`, `snapshot_ts`, `market`, `session`, `source`, `universe`
- `model_version`, `parameter_version`, `feature_version`
- `evaluation_profile`: `short` 또는 `swing`
- 현재가, 관찰 구간, 목표, 무효화선, 점수, 원점수, 적용 보정값
- 추천 당시 피처와 데이터 freshness

중복 방지를 위해 기존 일·종목 단위 ID는 `daily` 프로필에 그대로 사용한다. 장중 추천은 같은 종목을 매 15분마다 새 샘플로 쌓지 않고, `enter` 전환 또는 cooldown 만료 후 최초 알림을 하나의 recommendation event로 기록한다. 이벤트 ID에는 시장 세션과 snapshot bucket을 포함한다.

outcome은 horizon별 별도 레코드로 append한다.

- `30m`, `1h`, `4h`, `1d`: 장중/단기 프로필에서 사용 가능한 시장만 생성
- `3d`, `5d`, `20d`: 국내·미국 공통 스윙 프로필
- `direction_hit`: 진입 기준 종가/청산가 방향 적중 여부
- `stock_ret`, `benchmark_ret`, `excess_ret`
- `target_first`, `stop_first`, `none`
- `mfe`, `mae`, `time_to_target`, `time_to_stop`
- 수수료·슬리피지 적용 전후 수익률과 데이터 품질 상태

데이터가 부족하거나 장이 닫혀 horizon이 아직 성숙하지 않은 경우 outcome을 만들지 않는다. 휴장일은 시간 경과가 아니라 거래 bar 수로 계산한다. 같은 bar에서 목표와 무효화선이 모두 터치되면 기존 보수적 규칙대로 무효화선 선행으로 기록한다.

## 4. 튜닝 및 채택 정책

단순히 추천 뒤 주가가 올랐는지만 보고 즉시 가중치를 바꾸지 않는다. 다음 순서로 처리한다.

1. horizon별 outcome을 성숙시킨다.
2. 방향 적중률, 평균 초과수익, 평균 R, MFE/MAE, 목표/무효화선 비율을 계산한다.
3. 피처·시장·horizon별 calibration과 실패 요인을 집계한다.
4. 시간순 train/OOS로 challenger를 만들고 현재 champion과 같은 OOS 기간에서 비교한다.
5. 최소 표본, 신뢰구간, 초과수익 개선, MDD·평균 손실 제한을 모두 통과한 경우에만 shadow 정책으로 저장한다.
6. shadow 기간 동안 기존 정책과 동시에 계산하고, 안정된 결과가 확인될 때만 opt-in 라이브 점수에 반영한다.

초기 채택 게이트는 기존 정책을 보수적으로 재사용한다.

- horizon별 성숙 추천 최소 30건, OOS 최소 10건
- 후보가 기존 champion보다 평균 초과수익이 높고 양수여야 한다.
- 후보의 MDD/평균 손실이 기준을 초과하면 탈락한다.
- 조건별 보정은 항목별 ±0.04, 총 보정 ±0.08을 넘지 않는다.
- 새 정책은 `policy_version`, 학습 표본, OOS 기간, 채택 사유를 함께 저장한다.

초기에는 `enter_threshold`와 기존 조건별 점수 보정만 자동 후보로 둔다. 목표·무효화선 배수와 보유 기간은 outcome 표본이 충분히 쌓인 뒤 별도 challenger로 평가한다. 이는 한 번에 여러 축을 바꿔 원인을 잃는 것을 막는다.

## 5. 데이터 흐름과 컴포넌트

```text
entry analyzer
  -> recommendation event + decision ledger
  -> compact Telegram digest / detailed command
  -> outcome backfill (market-aware horizons)
  -> feedback metrics and factor diagnosis
  -> time-split challenger
  -> shadow policy
  -> OOS gate
  -> optional live score adjustment
```

예정 변경 경계는 다음과 같다.

- `dashboard/pages/ai_console.py`: 응답 상태 계약과 요약 우선 표시
- `ml/entry_analyzer.py`: compact digest formatter와 필드 선택
- `telegram_bot.py`: 후보 묶음 발송과 기존 상세 명령 연결
- `ml/entry_feedback.py`: horizon별 outcome·성과 집계·버전 메타데이터
- `crons/entry_signal_feedback.py`: 백필과 요약 보고
- `crons/entry_adaptive_learn.py`: challenger 채택 기록과 shadow 정책 상태
- `tests/`: formatter, event idempotency, outcome maturity, OOS adoption, UI smoke 회귀

기존 `entry_signals_decisions.jsonl`과 `entry_signals_outcomes.jsonl`은 그대로 읽을 수 있어야 한다. 새 필드가 없는 과거 레코드는 `legacy` 프로필로 분류하고, 가능한 horizon만 계산한다.

## 6. 오류 처리와 안전장치

- 가격·벤치마크 중 하나라도 없으면 outcome을 성공/실패로 추정하지 않고 `pending`으로 둔다.
- 데이터 시점이 추천 시점보다 앞서지 않으면 해당 결과를 폐기하고 품질 오류로 기록한다.
- Telegram 발송 실패는 원장 기록을 되돌리지 않고 다음 실행에서 재발송하지 않도록 알림 상태와 발송 상태를 분리한다.
- digest formatter가 특정 후보 하나 때문에 실패하지 않도록 후보 단위로 graceful fallback을 적용한다.
- 학습 파일 손상, 표본 부족, OOS 미개선은 기본 파라미터를 유지한다.
- 정책 자동 반영은 환경 플래그와 shadow 상태를 동시에 요구하며, 원장과 정책 파일에는 변경 사유를 남긴다.
- 사용자는 항상 정보형 추천과 데이터 기준 시각을 볼 수 있어야 하며 자동 주문으로 오인할 수 있는 문구는 사용하지 않는다.

## 7. 검증과 출시 순서

1. 순수 formatter와 event ID 멱등성 테스트를 먼저 추가한다.
2. 기존 20·60일 outcome과 새 1d·3d·5d outcome을 가짜 가격 데이터로 검증한다.
3. 결과가 없는 표본, 휴장일, 목표·무효화선 동시 터치, benchmark 누락을 검증한다.
4. champion/challenger가 표본 부족·OOS 미개선·MDD 초과를 거부하는지 확인한다.
5. Telegram은 digest shadow 출력으로 비교한 후 기본 발송으로 전환한다.
6. AI 콘솔은 기존 답변 내용과 엔진 판정은 유지한 채 렌더 순서와 상태 표시만 회귀 테스트한다.
7. 운영에서는 최소 한 주 동안 기존 정책과 challenger의 결과를 함께 기록하고, 기준을 통과한 경우에만 `ADAPTIVE_ENTRY_ENABLED`를 검토한다.

성공 기준은 다음과 같다.

- 추천 digest 한 번에 상위 후보와 가격·위험·데이터 시점을 읽을 수 있다.
- 추천마다 성숙 가능한 horizon 결과가 누락 없이 원장에 연결된다.
- 새 정책은 OOS 개선 없이 라이브 파라미터를 바꾸지 않는다.
- 응답 UI가 실제로 수행한 단계만 표시하고, 핵심 판단이 상세 설명보다 먼저 보인다.

## 8. Trade-off

digest는 알림 수와 인지 부하를 낮추지만, 낮은 순위 후보의 즉시 노출은 줄어든다. 검색과 `/signals entry` 상세 명령으로 접근성을 보완한다.

짧은 horizon을 추가하면 학습 표본은 빨리 쌓이지만 동일 시장 구간의 상관된 표본과 체결 비용 영향이 커진다. 따라서 단기 결과는 threshold 보정의 참고 자료로 먼저 사용하고, 여러 horizon에서 일관된 OOS 개선이 있을 때만 가중치 변경으로 승격한다.

자동 튜닝은 수동 판단보다 빠르지만 regime change와 데이터 결측에 취약하다. 표본·OOS·MDD 게이트와 shadow 단계로 속도와 안정성을 절충한다.
