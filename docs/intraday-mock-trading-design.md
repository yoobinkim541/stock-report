# 단기(1분/5분봉) 모의 트레이딩 — 판단근거 로직·아키텍처 설계

> 상태: **구현 완료 — Phase 0 (수집·shadow) 배포 대기** (2026-07-07)
> 목표: 기존 일간 모의 트레이딩(KR·US)과 **분리된 단기 슬리브**를 모의계좌에 추가하고,
> 판단근거를 point-in-time 원장으로 남겨 **엣지를 증명하거나 정직하게 기각**한다.
>
> 구현 노트 (설계 대비 변경): ① 유니버스 = 고정 리스트 → **동적 스캐너**(거래대금·등락
> 상위 "stocks in play" — providers/intraday_universe, 정적 env 는 폴백으로 강등, 사용자 결정).
> ② 주간 학습 크론 03:30 → **02:30 UTC**(kr_ranker_retrain 충돌 회피). ③ KR 스프레드 가드
> 기본 10→**25bps + max(2틱)**(호가단위상 1틱이 ~16bps — 10bps 는 전면 차단). ④ 원장표
> 필터 확장 대신 **paper 페이지 전용 🕐 섹션**(분봉 차트·마커 클릭 판단근거·그날 원장표 —
> 데이터 정렬이 더 깔끔). 활성화 절차는 CLAUDE.md env 표 + 아래 §5.

---

## 0. 정직한 전제 (이 설계가 지켜야 하는 것)

이 프로젝트의 6티어 검증은 이미 **장중 타이밍을 무엣지로 판정**했고, `/signals intraday` 는
그래서 표시 전용이다. 단기 트레이딩을 추가한다고 수익률이 오른다는 보장은 없다 — 오히려
비용 수학이 불리하다:

| 시장 | 왕복 비용 | 하루 2왕복 × 월 20일 | 트레이드당 손익분기 |
|------|-----------|----------------------|---------------------|
| KR (모의 bps 기준) | 22bps (매수 2 + 매도 20, 거래세 포함) | 월 ~8.8% 드래그 | 순 +0.22% 이상 |
| US | 30bps (15+15) | 월 ~12% 드래그 | 순 +0.30% 이상 |

**따라서 이 레이어는 "수익 기계"가 아니라 "엣지 검증 실험 장치"로 설계한다:**

1. **shadow-first**: 기본 `INTRADAY_SHADOW_ONLY=true` — 신호·가상체결만 원장에 기록, 모의 주문 0.
2. **게이트 통과 후 집행**: shadow 트레이드 ≥ 100건/축, 순비용 기대치 > 0, PSR ≥ 0.95(`ml/validation`),
   파라미터 그리드 PBO < 0.5 를 통과한 축만 모의 집행 허용.
3. **슬리브 캡**: 통과해도 모의 NAV 의 10%(기본)만 배정 — 기존 일간 전략 NAV 와 귀속 분리.
4. **무엣지면 정직 공개**: 기존 Tier4~6 NO-GO 처럼 `/evolve`·대시보드에 그대로 노출하고 표시 전용으로 남긴다.

비용 구조상 미시 평균회귀(스캘핑)는 개인 비용으로 불가능하다. 축 설계는 **큰 이동이 실제로
일어나는 순간**(이벤트·돌파·유동성 쏠림)에만 베팅하도록 편향시킨다.

---

## 1. 판단근거 로직 (축 → 합성 → 진입/청산)

### 1.1 축(axis) — 각 [0,1] 점수 + 방향

기존 `ml/intraday_signal.compute_intraday_features()`(VWAP·RSI·EMA·vol_z·BB·ATR)를 재사용하되,
정책 축으로 재구성한다. 초기 가중치(Policy 클램프·학습으로 갱신):

| 축 | w₀ | 정의 | 근거·비고 |
|----|----|------|-----------|
| `w_orb` 시가범위 돌파 | 0.20 | 개장 후 15분 고저(OR) 확정 → 종가 기준 OR 상단 돌파 + 거래량 z ≥ 1.5 → 롱 | ORB 문헌(QQQ 5m)상 gross 유의·비용 민감. 갭·이벤트일에 집중 |
| `w_vwap` VWAP 회귀/지지 | 0.15 | vwap_dev ≤ −2σ 에서 1m 반전봉 확인 → 롱 / VWAP 리클레임 | 기존 `vwap_dev` 재사용. 횡보일 가점 |
| `w_volspike` 거래량 스파이크 모멘텀 | 0.20 | **시간대 정규화**(같은 분대 20일 평균 대비 — 개장/마감 U자형 보정) vol z ≥ 3 + 가격 임펄스 ≥ +0.5% → 지속 | 기존 `vol_zscore` 는 미정규화 → 개장 오탐. 정규화 필수 |
| `w_ofi` 호가 불균형 | 0.15 (KR) / 0.05 (US) | OBI = (Σbid−Σask)/(Σbid+Σask) 60초 평균, \|OBI\| > 0.3 + 스프레드 정상 → 방향 가점 | KR 10단계 호가(realtime cache)로만 가능한 **유일한 신규 정보원**. 수명 짧음 — 단독 진입 금지, 확인용 |
| `w_news` 뉴스 이벤트 드리프트 | 0.20 | news_spike 이벤트(규칙 ≥7점 or LLM 라벨 강도 상위) 후 30–60분 창, 라벨 방향 추종 | 이벤트성 이동이 커 비용 커버 확률 최고. `providers/news_labels` 방향×강도 재사용 |
| `w_ema`/`w_rsi`/`w_bb` (기존 지표) | 각 0.03~0.04 | 기존 analyze_intraday 조건 | 6티어상 무엣지 성향 — 낮은 가중으로 학습에 맡기고, 학습이 0 으로 보내면 그대로 수용 |

**레짐 승수(축 아님)**: intraday Kaufman ER(`ml/regime_classifier` 개념 재사용)로 추세일/횡보일 판별 —
추세일엔 돌파축(orb·volspike) ×1.2·회귀축(vwap) ×0.8, 횡보일엔 반대. 룩어헤드 없음(확정 봉만).

### 1.2 합성·진입

```
score(sym) = Σ wᵢ · axisᵢ(sym)          # Policy 클램프, 축합 상한 패턴 재사용
진입 조건: score ≥ θ_entry(0.55)
         AND 하드가드 전부 통과 (스프레드·신선도·쿨다운·일손실·트레이드수·세션마감버퍼)
```

### 1.3 청산 (우선순위 — 일간 리밸런스와 가장 다른 부분)

| 순위 | 규칙 | 값(초기) |
|------|------|----------|
| ① 손절 | 진입가 − 1.2 × ATR(14, 1m) | 하드 스톱 — 봉 확정 기준 |
| ② 목표 | +2R (손절폭의 2배) | 부분청산 없음(비용) |
| ③ 타임스톱 | 진입 후 90분 무진전(\|PnL\| < 0.3R) | 자본 회전 |
| ④ 신호 붕괴 | score < 0.25 | 축 방향 소멸 |
| ⑤ EOD 강제 flat | KR 15:15 KST / US 15:50 ET 까지 전량 청산 | 오버나이트 갭 차단 + 일간 전략과 귀속 분리 |

### 1.4 포지션 사이징

```
주수 = floor( 슬리브NAV × risk_per_trade(0.5%) / 손절폭(원화|달러) )
상한: per-position ≤ 슬리브의 1/3, 정수주.
```
tranche 분할은 **미적용**(단타는 일괄 진입/청산 — lib/tranche 는 일간 전용 유지).

### 1.5 원장 (point-in-time 판단근거)

- **결정**(진입 시): `Ledger("kr_intraday"|"us_intraday").log_decision()` —
  `{ts, ticker, side, bar_ts, features:{axis별 점수, spread_bps, ofi, vol_z, regime_er, news_id},
   score, stop, target, qty, shadow: bool}`
- **결과**(청산 시 즉시): `log_outcome()` — `{decision_id, exit_ts, exit_reason, realized_r,
   net_pnl(비용+슬리피지 차감), holding_min}`
  → 일간(20거래일 horizon 대기)과 달리 **청산 즉시 보상 확정** — 학습 루프가 빠르다.
- **슬리피지 정직성**: 모의 체결·shadow 가상체결 모두 best 호가 기준(중간가 금지) +
  보수 페널티(진입·청산 각 스프레드/2 + 1틱)를 net_pnl 에 가산 차감.

### 1.6 학습 (주간)

`crons/intraday_mock_learn.py` (토 03:30 UTC) — `kr_mock_learn.py` 템플릿 그대로:
- `learner.robust_axis_weight(축점수, realized net R, stability=True)` → 축별 상관
- `learner.refit_and_adopt(...)` walk-forward OOS 게이트 (min_samples=100 — 트레이드가 많아 통계 파워 빨리 참)
- `reward.objective_score` 재사용 — 단기 슬리브 MDD 가 지수 MDD 초과 시 하드 디스퀄
- `evolution.record_learning()` → `/evolve`·대시보드 학습곡선에 `kr_intraday`/`us_intraday` 표면 추가

---

## 2. 아키텍처

```
┌─ 데이터층 ──────────────────────────────────────────────────────┐
│ kis_stream.py (기존 WS) ──[신규 bar sink]──▶ intraday_bars/     │
│   · 모든 틱을 이미 수신 → 1m OHLCV 정확 집계                    │
│   · INTRADAY_BARS_ENABLED 게이트 · safe_io atomic append        │
│   · ~/reports/ml-data/intraday_bars/{market}/{YYYY-MM-DD}.jsonl │
│ providers/intraday_bars.py (신규 reader)                        │
│   · load_bars(sym, date, interval) → DataFrame (1m→5m 리샘플)   │
│   · 폴백/백필: ml/intraday_signal.fetch_intraday (yfinance)     │
│ providers/realtime_quotes.py (기존) — 현재가·호가·신선도        │
└─────────────────────────────────────────────────────────────────┘
                              │ 확정 분봉 + 실시간 호가
┌─ 판단층 ────────────────────▼───────────────────────────────────┐
│ ml/intraday_axes.py (신규·순수함수)                             │
│   · orb/vwap/volspike/ofi/news 축 점수 (compute_intraday_        │
│     features 재사용) + 시간대 정규화 + 레짐 승수                │
│ ml/intraday_policy.py (신규)                                    │
│   · Policy("kr_intraday"), Policy("us_intraday") — 클램프·학습  │
└─────────────────────────────────────────────────────────────────┘
                              │ score·진입/청산 판단
┌─ 실행층 ────────────────────▼───────────────────────────────────┐
│ crons/intraday_mock_track.py (신규 — 매 1분 크론, flock)        │
│   · is_kr/us_market_open() 게이트 → 열린 시장만                 │
│   · 하드가드 → 진입/청산 결정                                   │
│   · shadow: 가상체결만 원장 / live: kiwoom_mock·kis_mock 지정가 │
│   · Ledger 결정/결과 + lib/trade_events.record_trade() ← 차트   │
│   · 상태: ~/.cache/intraday_mock_state.json                     │
│     (당일 트레이드수·halt·쿨다운·오픈포지션·스톱)               │
└─────────────────────────────────────────────────────────────────┘
                              │ 원장(JSONL append-only)
┌─ 학습·가시화층 ─────────────▼───────────────────────────────────┐
│ crons/intraday_mock_learn.py (토 03:30) — 주간 재적합·OOS 게이트│
│ 리포트: kiwoom_mock_report/us_mock_report "🕐 단기" 섹션        │
│ 봇: /paper 에 단기 슬리브 병기 · /evolve 표면 추가              │
│ 대시보드: paper 페이지 1m/5m 캔들 + ▲▼ 트레이드 마커           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 실행 주기 — 상시 프로세스가 아니라 **매 1분 크론**

`news_spike_detector` 로 검증된 패턴(flock + state json + 게이트) 재사용. 이유:
- 판단 단위가 "확정 1분봉"이라 1분 크론이면 충분 (틱 반응 불필요)
- 크론은 죽어도 다음 분에 자동 재기동 — watchdog·PID 관리 불필요
- 두 시장 세션(KR 00:00–06:30 UTC · US 13:30/14:30–20/21:00 UTC)을 한 엔트리가 처리

정밀 OHLCV 집계만 상시 프로세스(kis_stream)가 담당 — 이미 모든 틱을 보고 있어
**sink 추가**가 최소 변경이다. (크론 폴링 집계는 분당 1샘플이라 high/low 소실 → ORB·ATR 스톱 불가)

### 2.2 워치리스트

kis_stream 심볼 캡(KR/US 각 10, 41심볼/세션)과 양립하도록 단기 유니버스는 **소수 고유동성 종목**:
- KR: KOSPI 시총 상위 + 당일 news_spike 티커 (합 ≤ REALTIME_KR_MAX)
- US: 보유 + QQQ/SPY + 당일 이벤트 티커 (합 ≤ REALTIME_US_MAX)
- `compute_watchlist()` 확장으로 intraday 후보를 스트림 구독에 편입

### 2.3 모의 주문

- KR: `kiwoom_mock.place_order(code, qty, side, price)` — 지정가(best 호가), 모의 도메인 하드락 유지
- US: `kis_mock.place_order(symbol, qty, side, price)` — 정수주·지정가
- 주문 실패(`ok=False` — RC4058 장종료 등)는 재시도 없이 스킵·다음 분 재평가 (중복체결 방지 원칙 유지)
- **실계좌 경로 0** — 기존 하드락·grep 강제 테스트 불변

---

## 3. 차트 buy/sell 표시 (요청 반영)

기존 인프라가 이미 대부분 있다 (`lib/trade_events.py` + `dashboard/charts._add_trade_markers`
— ▲매수(green)/▼매도(red) + 호버에 수량·체결가·평단·계좌·메모):

1. **자동 편입**: 엔진이 체결(또는 shadow 가상체결)마다
   `trade_events.record_trade(source="intraday_mock", ticker, side, qty, price, note=exit_reason...)`
   → 종목분석 페이지 일봉 차트(`price_line`/`price_candle`, `trades=` 배선 완료)에 마커 자동 표시.
2. **분봉 전용 차트 (신규)**: 일봉엔 당일 다회 트레이드가 뭉개지므로, 모의투자(paper) 페이지에
   **"🕐 단기 트레이딩" 섹션** 추가:
   - `providers/intraday_bars.load_bars(sym, date, "1m"|"5m")` → `charts.price_candle(hist, trades=...)`
     **그대로 재사용** (`_trade_price` 가 timestamp nearest 매칭이라 분봉 인덱스에 동작)
   - 오버레이: VWAP 라인·OR 박스(개장 15분 고저)·스톱/목표 점선 — 판단근거가 차트에서 재현되게
   - 날짜 선택 + 심볼 segmented, `@st.fragment` (기존 UX 패턴)
   - 마커 클릭 → 해당 결정의 원장 레코드(축 점수·exit_reason) 표시 (ticker 페이지 `_selected_trade` 패턴 재사용)
3. **원장표**: paper 페이지 판단근거 원장표의 구분 필터에 `단기` side 추가.
4. (선택) 일일 리포트 PNG(`report_charts`)에 당일 단기 트레이드 패널 1장.

---

## 4. 안전장치 · 환경변수

| 변수 | 기본 | 역할 |
|------|------|------|
| `INTRADAY_MOCK_ENABLED` | `false` | 마스터 게이트 — off 면 크론 no-op |
| `INTRADAY_SHADOW_ONLY` | `true` | 가상체결만 원장 기록·모의 주문 0 (게이트 통과 후에만 false) |
| `INTRADAY_MARKETS` | `kr,us` | 활성 시장 |
| `INTRADAY_BAR_INTERVAL` | `1m` | 판단 봉 (5m 신호는 리샘플) |
| `INTRADAY_SLEEVE_FRAC` | `0.10` | 모의 NAV 중 단기 슬리브 비율 |
| `INTRADAY_RISK_PER_TRADE` | `0.005` | 트레이드당 슬리브 리스크 (사이징) |
| `INTRADAY_MAX_TRADES_DAY` | `6` | 시장별 일일 왕복 상한 |
| `INTRADAY_COOLDOWN_MIN` | `30` | 심볼별 재진입 쿨다운 |
| `INTRADAY_DAILY_LOSS_HALT` | `0.015` | 슬리브 −1.5% 도달 시 당일 신규진입 정지·전량 청산 |
| `INTRADAY_MAX_SPREAD_BPS` | KR `10` / US `5` | 스프레드 초과 시 진입 스킵 |
| `INTRADAY_FLAT_BUFFER_MIN` | `15` | 마감 N분 전 강제 flat |
| `INTRADAY_BARS_ENABLED` | `false` | kis_stream 1m bar sink 게이트 |

추가 하드가드: realtime heartbeat/심볼 신선도 실패(기존 `REALTIME_*_STALE_S`) → **신규 진입 금지,
청산만 REST(`kis_quote`/`kiwoom_mock.get_price`) 폴백으로 수행.**

---

## 5. 단계별 로드맵

| Phase | 내용 | 기간 | 산출 |
|-------|------|------|------|
| **0 수집·shadow** | kis_stream bar sink + intraday_bars + axes/policy + 엔진(shadow) + 차트 마커 | 2–4주 가동 | 분봉 데이터 축적 + shadow 원장 (트레이드 수백 건) |
| **1 게이트** | shadow 원장 평가 — 순비용 기대치·PSR·PBO (`ml/validation` 재사용), 축별 verdict | 평가 1회 | GO/OBSERVE/NO-GO 정직 공개 (`/evolve`·대시보드) |
| **2 모의 집행** | GO 축만 `INTRADAY_SHADOW_ONLY=false` — 슬리브 캡·리포트 통합 | 게이트 통과 시 | 모의 체결 + 차트 ▲▼ + 일일 보고 |
| **3 진화** | 주간 재적합·챔피언-챌린저·evolution 텔레메트리 | 상시 | 축 가중 자동 갱신 (OOS 게이트) |

**신규 파일**: `providers/intraday_bars.py` · `ml/intraday_axes.py` · `ml/intraday_policy.py` ·
`crons/intraday_mock_track.py` · `crons/intraday_mock_learn.py` · paper 페이지 단기 섹션
**수정 파일**: `kis_stream.py`(bar sink) · `crons/{kiwoom,us}_mock_report.py` · `bot/evolve_command.py` ·
`dashboard/pages/paper.py` · `deploy/crontab.stock-report` · `.env.example`
**테스트**: 합성 분봉으로 축·정책·가드·사이징 무네트워크 단위 (`tests/intraday_smoke_test.py`) —
ORB 돌파/손절/타임스톱/EOD flat/일손실 halt 시나리오.

---

## 6. 알려진 한계 (설계 시점 정직 기록)

- **분봉 히스토리 부재**: yfinance 1m=7일·5m=60일 → 장기 백테스트 불가. 그래서 gate 가
  백테스트가 아니라 **shadow 라이브 수집**이다. 자체 bar store 축적이 곧 미래의 백테스트 데이터.
- **모의 체결의 낙관 편향**: 모의서버 체결은 실제 슬리피지·부분체결을 과소반영 → 보수 페널티 차감으로 보정하되, GO 판정 시에도 실계좌 이전 근거로는 부족함을 명시.
- **US 호가 1단계**: OFI 축은 KR 전용에 가깝다. US 는 가중 축소.
- **기존 무엣지 판정과의 관계**: 이 설계는 그 판정을 뒤집는 게 아니라, 판정에 포함되지 않았던
  정보원(10단계 호가·뉴스 이벤트 창·ORB)을 **비용 반영 상태로 재검증**하는 것이다.
