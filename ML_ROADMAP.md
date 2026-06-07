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
         LeverageModel ─────────────────────→ /leverage 출력만                  ❌
         ExcessReturnModel ──────────────────→ MetaAllocator 내부               ❌
         MetaAllocator (5신호 통합) ──────────→ /meta 출력만                    ❌
Layer 3  LeverageOptimizer ─────────────────→ best_params.json 저장만           ❌
```

**실제 ML이 의사결정에 영향을 미치는 경로: Ranker → DCA 비중 조정 1개뿐.**

### 모델별 상세 진단

| 모델 | 피처 | 레이블 | 검증 방식 | 실제 반영 | 핵심 문제 |
|------|------|--------|-----------|-----------|-----------|
| **Ranker** | 가격 기반 11개 (모멘텀·RSI·베타·VIX) | 60일 QQQ 초과수익 4분위 | WF (train_frac 0.7) | DCA 비중 (±15%) | 펀더멘털·매크로 피처 없음 |
| **LeverageModel** | QQQ 낙폭·VIX·RSI·FG·크레딧 스프레드 11개 | 30/60/90일 수익 방향+크기 | 단순 시계열 분할 | /leverage 출력만 | MetaAllocator와 단절 |
| **ExcessReturnModel** | 모멘텀·변동성·RSI·VIX·크레딧 스프레드 8개 | **다음날 수익률** (정적 2/3 분할) | WF 없음 | MetaAllocator 내부 (출력만) | 레이블 설계 오류·WF 없음 |
| **MetaAllocator** | 위 3개 + FG proxy + Phase | — | — | /meta 출력만 | DCA·주문과 단절 |
| **LeverageOptimizer** | Optuna (파라미터 탐색) | Calmar 최대화 | WF OOS | 매주 월 재최적화만 | 최적 파라미터 LeverageModel에 미반영 |

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

### Phase 2 — 피처 강화 (2~3주, 중간 임팩트)

**목표: 가격 기반 신호에서 독립적인 신호 추가로 신호 다양성 확보.**

#### 2-A. 매크로 피처 (3개)

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

| 작업 | 임팩트 | 난이도 | 우선순위 |
|------|--------|--------|----------|
| 1-A. MetaAllocator → DCA 연결 | ⭐⭐⭐⭐⭐ | ★★☆ | **즉시** |
| 1-B. Optimizer 파라미터 → LeverageModel | ⭐⭐⭐ | ★☆☆ | **즉시** |
| 1-C. ExcessReturnModel 레이블 수정 + WF | ⭐⭐⭐⭐ | ★★☆ | 1주차 |
| 2-A. 매크로 피처 3개 추가 | ⭐⭐⭐ | ★★☆ | 1~2주차 |
| 2-B. 펀더멘털 피처 (ROE, EPS 성장) | ⭐⭐ | ★★☆ | 2주차 |
| 2-C. Ranker Purged WF (embargo 20일) | ⭐⭐⭐ | ★★☆ | 2주차 |
| 3-A. MetaAllocator 가중치 Ridge 학습 | ⭐⭐⭐ | ★★★ | 3~4주차 |
| 3-B. Mean-Variance 포트폴리오 최적화 | ⭐⭐ | ★★★ | 4주차 |
| 3-C. A/B 페이퍼 트레이딩 추적 | ⭐⭐⭐ | ★★☆ | 3주차 |

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

| 지표 | 현재 측정 | 목표 |
|------|----------|------|
| Ranker OOS IC | 매일 `/ranking` | > 0.05 (연환산) |
| Ranker ICIR | 매일 `/ranking` | > 0.5 |
| MetaAllocator WF Sharpe | `/meta` | > QQQ Sharpe |
| LeverageModel WF Calmar | `leverage_best_params.json` | > 1.5 |
| DCA 비중 vs 실현수익 상관 | `paper_track.json` (구현 후) | > 0.15 |
