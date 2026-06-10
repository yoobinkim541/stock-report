# ML 전략 로드맵

> 목표: 개별 모델 5개가 따로 작동하는 현재 구조를 **계층적 의사결정 파이프라인**으로 통합해
> 실제 DCA·레버리지·종목 비중 결정에 ML이 직접 반영되도록 한다.

---

## 1. 현재 구조 진단

### 아키텍처 개요

```
Layer 1  Phase 규칙 (barbell_strategy)
          ↓ Phase별 DCA 배율 + 레버리지 임계
Layer 2  Ranker ──────────────────────────────→ _ml_dca_blend() → 실제 DCA 비중 ✅
         LeverageModel ─────────────────────→ /leverage + opt 임계·vol scale   ✅
         ExcessReturnModel ──────────────────→ MetaAllocator 내부               ✅
         MetaAllocator (5신호 통합) ──────────→ DCA 블렌딩 + paper_track A/B    ✅
Layer 3  LeverageOptimizer ─────────────────→ live 진입 임계·비중 직접 반영     ✅
         EntryAnalyzer ─────────────────────→ 자동 목표가/손절가 알림 → R-multiple 기록 ✅
```

(2026-06-10 갱신 — 연결 단계는 완료. 남은 축은 신호 품질 검증: paper_track A/B와
purged WF 기준으로 각 신호의 실제 기여를 측정하는 단계.)

### 모델별 상세 진단

| 모델 | 피처 | 레이블 | 검증 방식 | 실제 반영 | 핵심 문제 |
|------|------|--------|-----------|-----------|-----------|
| **Ranker** | 가격 68개 + 펀더멘털 틸트 (추론 시) | 20일 QQQ 초과수익 | **Purged WF (embargo 20일)** | DCA 비중 (±15%) + 랭킹 매매가이드 | ⚠️ purged IC ≈ 0.003 (최근 폴드 음수) — 단순분할 0.13은 누수 과대평가였음. paper_track으로 DCA 틸트 기여 검증 중 |
| **LeverageModel** | QQQ 낙폭·VIX·RSI·FG·크레딧 스프레드 11개 | 21/42/63/126일 수익 방향+크기 | **Purged 분할 (embargo 126d)** | /leverage 출력 + opt 파라미터·vol scale 반영 | ⚠️ 21d AUC ≈ 0.5 (무력) / 63~126d AUC 0.6~0.71 — Kelly 블렌딩이 21d 기준인 것은 개선 필요 |
| **ExcessReturnModel** | 모멘텀·변동성·RSI·VIX·크레딧 스프레드 8개 | 20일 QQQ 초과수익 | WF | MetaAllocator 내부 | — |
| **MetaAllocator** | 위 3개 + FG proxy + Phase | — | paper_track A/B (2026-06-10~) | DCA 블렌딩 + /meta | 가중치 고정 (Ridge 학습은 3-A) |
| **LeverageOptimizer** | Optuna 16파라미터 (거래비용 5bp·VIX텀·트랜치·자금곡선스톱·소프트추세 포함) | Calmar 0.45 + Sharpe 0.25 + CAGR 0.30 − MDD 페널티 | WF OOS (median Calmar 선택) | live 신호 임계·비중·vol targeting·백워데이션 게이트 반영 | 비용 반영 후 QLD 저노출 수렴 (CAGR 6.8%/MDD -6.3%) — 잦은 매매 엣지 대부분이 비용으로 소멸함을 확인 (2026-06-10) |

---

## 2. 핵심 원칙

실전 퀀트 연구(López de Prado, AQR)에서 일관되게 확인된 원칙:

1. **모델 복잡도 < 신호 품질**: IC(정보계수) > 0.05면 이미 양호. 복잡한 모델은 과적합만 늘림.
2. **신호 다양성이 정확도보다 중요**: 가격 기반 신호 10개 < 독립적인 신호 3개 (모멘텀·매크로·펀더멘털).
3. **거래비용 인식 필수**: 보유기간 < 5일 신호는 비용으로 알파 소멸.
4. **WF 검증 + embargo 기간**: 리밸런싱 주기만큼 embargo 없으면 데이터 누수.
5. **연결이 먼저, 정확도는 나중**: 정확한 신호가 DCA에 연결 안 되면 0의 효과.

---

## 3. 로드맵

### Phase 1 — 연결 (1~2주, 최고 임팩트)

**목표: 기존 ML 신호를 실제 DCA·레버리지 결정에 연결한다.**

#### 1-A. MetaAllocator → DCA 실반영

현재 `_ml_dca_blend()`는 Ranker 신호만 사용.
MetaAllocator의 `weights` 딕셔너리를 DCA 배분에 직접 반영.

```python
# barbell_strategy.py 변경 방향
def _ml_dca_blend(base_weights, market_type, phase_key):
    from ml.meta_allocator import get_meta_allocation
    alloc = get_meta_allocation(market_type, phase_key)
    # alloc.weights: {MSFT: 0.18, NVDA: 0.22, SGOV: 0.36, ...}
    # Phase 안전마진 유지하면서 MetaAllocator 비중으로 블렌딩
    blend = _phase_blend_factor(market_type, phase_key)
    merged = {}
    for t in base_weights:
        ml_w  = alloc.weights.get(t, base_weights[t])
        merged[t] = base_weights[t] * (1 - blend) + ml_w * blend
    return merged, alloc.signal_summary, alloc.confidence
```

#### 1-B. LeverageOptimizer → LeverageModel 연결

`get_optimized_params()`가 이미 있으나 `build_entry_signal()`에서 미사용.
최적 파라미터를 진입 임계 판단에 반영:

```python
# leverage_signal.py: build_entry_signal() 내
opt = get_optimized_params()
if opt:
    min_dd_threshold = opt.get("min_dd", -0.10)  # 기본값 대신 최적값 사용
    max_vix_entry    = opt.get("max_vix_entry", 35)
```

#### 1-C. ExcessReturnModel 레이블 수정

다음날 수익률 → **20일 QQQ 초과수익률**로 변경 + WF 검증 추가:

```python
# sweet_spot.py: _generate_ml_signal() 수정
fwd_return = (close.pct_change(20) - qqq_close.pct_change(20)).shift(-20)
# 정적 분할 제거 → expanding_window WF
```

---

### Phase 2 — 피처 강화 ✅ 부분 완료

**목표: 가격 기반 신호에서 독립적인 신호 추가로 신호 다양성 확보.**

#### 2-A. 매크로 피처 ✅ (ml/macro_features.py 신규)

모든 모델에 공통 추가:

| 피처 | 계산 | yfinance 티커 | 의미 |
|------|------|--------------|------|
| `yield_curve` | 10Y - 2Y Treasury | `^TNX - ^IRX` | 경기 선행 지표 |
| `vix_term` | VIX 3M / VIX 1M | `^VIX3M / ^VIX` | 단기 vs 중기 변동성 |
| `junk_spread` | HYG vs IEF 상대 수익률 | `HYG, IEF` | 신용 위험 선호도 |

```python
# data_pipeline.py: build_stock_features() 추가
feat["yield_curve"] = _close("^TNX").reindex(idx) - _close("^IRX").reindex(idx)
feat["vix_term"]    = _close("^VIX3M").reindex(idx) / _close("^VIX").reindex(idx)
```

#### 2-B. 펀더멘털 피처 (2개, 분기 갱신)

Ranker에 추가 (가격 기반 신호의 독립적 보완):

| 피처 | 계산 | 갱신 주기 |
|------|------|----------|
| `roe_ttm` | TTM ROE (yfinance quarterly) | 분기 |
| `earnings_growth` | EPS YoY 성장률 | 분기 |

```python
# data_pipeline.py 신규
def build_fundamental_features(ticker: str) -> dict:
    import yfinance as yf
    info = yf.Ticker(ticker).info
    return {
        "roe_ttm":        info.get("returnOnEquity", 0),
        "earnings_growth": info.get("earningsGrowth", 0),
    }
```

> 주의: 분기 갱신 데이터는 당일 발표 전까지 이전 분기 값 사용 → 룩어헤드 없음.

#### 2-C. Ranker WF + embargo

현재 단순 시계열 분할 → **Purged Walk-Forward** (embargo 20일):

```python
# ranker.py: walk_forward_backtest() 수정
# 각 폴드에서 train_end ~ train_end + 20일은 test에서 제외
embargo_days = 20
test_start = train_end + pd.Timedelta(days=embargo_days)
```

---

### Phase 3 — 통합 최적화 (3~4주, 장기 임팩트)

**목표: MetaAllocator 신호 가중치를 데이터에서 학습한다.**

#### 3-A. 학습된 신호 가중치 (MetaAllocator)

현재 고정 가중치(`SignalWeights`)를 Ridge Regression으로 대체:

```
학습:  [ranker_score, excess_signal, lev_signal, fg_signal, phase_signal]
레이블: 다음 20일 포트폴리오 수익률
모델:  Ridge (규제 강도로 overfitting 방지)
검증:  Rolling WF (12개월 학습, 3개월 OOS)
```

#### 3-B. 포트폴리오 최적화 레이어

DCA 배분에 Mean-Variance 또는 Risk Parity 적용:

```python
# 현재: 점수 비례 배분
# 변경: Markowitz 최소분산 포트폴리오 (공분산 행렬 = 60일 rolling cov)
from scipy.optimize import minimize
# 제약: SGOV ≥ sgov_floor, 레버리지 ≤ phase_max, 개별주 ≤ 0.35
```

#### 3-C. A/B 페이퍼 트레이딩

MetaAllocator 결정 vs 현재 Phase 규칙 비교 추적:

```
paper_track.json: {
  "meta_alloc":  {date: {weights, return_next_5d, ...}},
  "phase_rule":  {date: {weights, return_next_5d, ...}}
}
```
30일 데이터 누적 후 Sharpe 비교 → 우위 검증 시 MetaAllocator 반영 비율 상향.

---

## 4. 우선순위 매트릭스

| 작업 | 임팩트 | 난이도 | 상태 |
|------|--------|--------|------|
| 1-A. MetaAllocator → DCA 연결 | ⭐⭐⭐⭐⭐ | ★★☆ | ✅ 완료 |
| 1-B. Optimizer 파라미터 → LeverageModel | ⭐⭐⭐ | ★☆☆ | ✅ 완료 |
| 1-C. ExcessReturnModel 레이블 수정 + WF | ⭐⭐⭐⭐ | ★★☆ | ✅ 완료 |
| 2-A. 매크로 피처 (수익률곡선/VIX텀/크레딧/달러/금/원유 등 40개) | ⭐⭐⭐ | ★★☆ | ✅ 완료 |
| 2-B. 기술지표 확장 (Stochastic/WR/CCI/이격도/OBV/CMF/감마) | ⭐⭐⭐ | ★★☆ | ✅ 완료 |
| 2-C. 일목균형표 신호 + MA 크로스오버 신호 | ⭐⭐ | ★☆☆ | ✅ 완료 |
| 2-D. 펀더멘털 피처 (ROE, EPS 성장) | ⭐⭐ | ★★☆ | 🟡 부분 — 추론 틸트 적용 + 주간 point-in-time 스냅샷 적재 시작 (crons/fundamental_snapshot.py), 학습 피처 투입은 데이터 6개월+ 축적 후 |
| 2-E. Ranker Purged WF (embargo 20일) | ⭐⭐⭐ | ★★☆ | ✅ 완료 — purged WF mean IC 0.003 (기존 단순분할 0.13은 과대평가였음) |
| 3-A. MetaAllocator 가중치 Ridge 학습 | ⭐⭐⭐ | ★★★ | ⬜ 보류 — paper_track 30일+ 축적 후 착수 |
| 3-B. Mean-Variance 포트폴리오 최적화 | ⭐⭐ | ★★★ | ⬜ 보류 — 하려면 inverse-vol 라이트 버전 권장 |
| 3-C. A/B 페이퍼 트레이딩 추적 | ⭐⭐⭐ | ★★☆ | ✅ 완료 — crons/paper_track.py (월요일 Sharpe 비교 발송) |
| (신규) 자동 알림 → 신호 성과 기록 (R-multiple) | ⭐⭐⭐ | ★☆☆ | ✅ 완료 — signal_outcomes.json, 캘리브레이션 실전 레이블 |
| (신규) 진입점수 월간 재캘리브레이션 | ⭐⭐ | ★☆☆ | ✅ 완료 — 크론 등록(매월 1일) + 2026-06-10 첫 채택 (OOS +6.5% vs 기본 +3.1%, 임계 0.74) |
| (신규) 레짐 코어(L): 앙상블추세(MA50~250 투표)×vol타깃25%×VIX텀 (QLD↔QQQ↔SGOV) | ⭐⭐⭐⭐ | ★★☆ | 🟡 백테스트 검증 완료 (16y CAGR 20.5% vs QQQ 19.6%, MDD -27.6% vs -35.1%, Calmar 0.74 vs 0.56; 고금리 2022+ 전지표 우위. backtest/regime_core_backtest.py) — paper_track 3번째 암으로 실데이터 검증 중, 60일+ 후 실전 슬리브 전환 판단. 절대모멘텀 게이트(K)는 MDD 악화로 기각 |

---

## 5. 하지 말아야 할 것

| 방향 | 이유 |
|------|------|
| LSTM / Transformer 도입 | 학습 데이터 ~2000일 → 딥러닝 파라미터 과다, overfitting 불가피 |
| 피처 10개 이상 추가 | 상관된 가격 피처 추가는 IC 개선 없이 과적합 위험만 증가 |
| 다음날 예측 신호로 매일 리밸런싱 | 거래비용으로 알파 소멸, 보유기간 최소 5~10일 |
| In-sample 최적화 결과를 바로 실전 반영 | Walk-Forward OOS 검증 없이는 곡선 맞추기에 불과 |

---

## 6. 성과 측정 기준

ML 개선 전후 비교 지표:

| 지표 | 현재 측정 | 현재값 (2026-06-10) | 목표 |
|------|----------|--------------------|------|
| Ranker OOS IC (purged WF) | 재학습 시 | 0.008 (3폴드, 라벨 정렬 수정 후 — 단일분할 IC -0.03, 알파 여전히 미검증) | > 0.05 |
| Ranker ICIR (purged WF) | 재학습 시 | 0.15 | > 0.5 |
| MetaAllocator vs Rule Sharpe | `paper_track.json` (매주 월 발송) | 적재 시작 (30일 후 첫 비교) | > Rule |
| Leverage WF median Calmar | `leverage_best_params.json` | 0.85 (TQQQ, 엔진 수정 후 — 유휴현금 이중계상 제거·폴드 웜업 보정, 무거래 폴드는 SGOV 캐리 반영) | > 1.0 (비용 반영 기준) |
| Phase 래더 vs 플랫 DCA | `backtest/phase_ladder_backtest.py` | XIRR +3.0%p (24.2% vs 21.2%), MDD -6.8%p 불리 | XIRR 우위 유지 |
| 진입신호 실전 R-multiple | `signal_outcomes.json` | 적재 시작 | 평균 > 0.5R |
