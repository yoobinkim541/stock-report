"""ml/meta_allocator.py — ML 신호 통합 앙상블 포트폴리오 배분기

5개 신호를 통합해 최종 포트폴리오 비중 결정:
  1. LightGBM Ranker (종목 선택)
  2. ExcessReturnModel sweet_spot (QQQ vs 현금 방향)
  3. LeverageModel (SGOV vs 레버리지 비율)
  4. Fear/Greed Proxy (시장 체온)
  5. Phase 규칙 (기준 배분)

공개 API:
    get_meta_allocation()       → MetaAllocation (현재 권장 비중)
    format_meta_report(alloc)   → 텔레그램 텍스트
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# 포트폴리오 전체 자산 후보
ASSET_CLASSES = {
    "cash":     ["SGOV"],                              # 안전자산
    "equity":   ["MSFT","NVDA","GOOGL","ORCL","UNH","SAP","SPMO"],  # 개별주
    "leverage": ["QLD", "TQQQ", "SOXL", "UPRO"],      # 레버리지
    "income":   ["QQQI"],                              # 배당 엔진
}


@dataclass
class SignalWeights:
    """각 신호별 메타 가중치 (Phase에 따라 동적 변경)."""
    ranker:   float = 0.25   # LightGBM Ranker 신뢰도
    excess:   float = 0.20   # ExcessReturnModel 방향
    leverage: float = 0.20   # LeverageModel
    fg_proxy: float = 0.20   # Fear/Greed Proxy
    phase:    float = 0.15   # 규칙 기반 Phase


@dataclass
class MetaAllocation:
    weights:        dict[str, float]   # 최종 권장 비중 {ticker: weight}
    signal_summary: dict               # 각 신호별 원시값
    regime:         str                # "risk_on" / "risk_off" / "neutral"
    confidence:     float              # 신호 일치도 (0~1)
    note:           str
    timestamp:      str = ""


# ── 신호 수집 ─────────────────────────────────────────────────────────────────

def _get_ranker_signal() -> dict[str, float]:
    """포트폴리오 종목별 ML 점수 → -1~1 정규화."""
    try:
        from barbell_strategy import _ml_dca_blend, _DCA_WEIGHTS_DEFAULT
        # use_meta=False 필수 — True면 meta→ranker→meta 무한 상호 재귀
        _, scores, _ = _ml_dca_blend(_DCA_WEIGHTS_DEFAULT, use_meta=False)
        if not scores:
            return {}
        vals = list(scores.values())
        mn, mx = min(vals), max(vals)
        if mx == mn:
            return {t: 0.0 for t in scores}
        return {t: (s - mn) / (mx - mn) * 2 - 1 for t, s in scores.items()}
    except Exception as e:
        logger.warning("Ranker 신호 실패: %s", e)
        return {}


def _get_excess_signal() -> float:
    """ExcessReturnModel OOS 방향 (-1=현금 선호, +1=주식 선호).

    레이블: QQQ 20일 초과수익률 (다음날 수익률 → 수정).
    검증:   Expanding Walk-Forward (정적 2/3 분할 → 수정).
    """
    try:
        import warnings; warnings.filterwarnings("ignore")
        import numpy as np
        from ml.data_pipeline import build_real_sweetspot_data, fetch_prices
        from ml.models import ExcessReturnModel

        # 실데이터 (SGOV 포함해 QQQ 초과수익 계산 가능하도록)
        data = build_real_sweetspot_data("QQQ", days=504)  # 약 2년
        features_df = data["features"]
        close       = data["close"]
        qqq_close   = data.get("qqq_close", close)

        n = len(features_df)
        if n < 100:
            return 0.0

        # ── 레이블: 20일 QQQ 초과수익률 (shift(-20) = 룩어헤드 방지용 당일 생성) ──
        # 미실현 구간(최근 20일)은 NaN 유지 — fillna(0)하면 가짜 0 라벨로 학습됨
        fwd_20 = close.pct_change(20).shift(-20)
        qqq_fwd_20 = qqq_close.pct_change(20).shift(-20)
        label = (fwd_20 - qqq_fwd_20).reindex(features_df.index)

        X = features_df.values.astype(float)
        y = label.values

        # ── Expanding Walk-Forward (최소 학습 60일, 예측 20일 단위 슬라이딩) ──
        min_train = min(60, n // 3)
        preds = np.full(n, np.nan)

        for end in range(min_train, n - 20, 20):
            model = ExcessReturnModel()
            X_tr, y_tr = X[:end], y[:end]
            valid = np.isfinite(X_tr).all(axis=1) & np.isfinite(y_tr)
            if valid.sum() < 30:
                continue
            model.fit(X_tr[valid], y_tr[valid])
            end2 = min(end + 20, n)
            preds[end:end2] = model.predict(X[end:end2])

        last = pd.Series(preds, index=features_df.index).dropna()
        if last.empty:
            return 0.0

        # 최근 20일 평균 방향 → -1~1 정규화
        recent   = float(last.tail(20).mean())
        pct_rank = float((last < recent).mean()) * 2 - 1
        return max(-1.0, min(1.0, pct_rank))

    except Exception as e:
        logger.warning("ExcessReturnModel 신호 실패: %s", e)
        return 0.0


def _get_leverage_signal() -> dict[str, float]:
    """LeverageModel 권장 비중 (SGOV vs 레버리지 배분)."""
    try:
        from ml.leverage_signal import get_entry_signal
        sig = get_entry_signal()
        return {name: inst.recommended_weight
                for name, inst in sig.instruments.items()}
    except Exception as e:
        logger.warning("LeverageModel 신호 실패: %s", e)
        return {"SGOV": 1.0}


def _get_fg_signal() -> float:
    """Fear/Greed Proxy → -1(극도공포) ~ +1(극도탐욕)."""
    try:
        from ml.data_pipeline import get_fg_proxy_score
        fg = get_fg_proxy_score()
        return (fg - 50) / 50   # 0~100 → -1~+1
    except Exception:
        return 0.0


def _get_phase_signal(market_type: str, phase_key) -> float:
    """Phase → -1(안전 극대화) ~ +1(공격 극대화)."""
    if market_type == "bull":
        return {"bull2": 0.8, "bull1": 0.5}.get(str(phase_key), 0.3)
    if market_type == "bear":
        # 하락장에서는 낙폭이 클수록 매수 기회 → 공격적
        return {0: 0.0, 1: -0.2, 2: -0.1, 3: 0.1, 4: 0.3, 5: 0.5}.get(phase_key, 0.0)
    return 0.0


# ── 체제 판단 ──────────────────────────────────────────────────────────────────

def _determine_regime(
    excess: float,
    fg:     float,
    phase:  float,
    lev_sgov: float,
) -> tuple[str, float]:
    """4개 신호 합의도 → 체제 + 신뢰도."""
    # risk_on 신호 수집 (양수 = 공격적)
    signals = [excess, fg, phase, 1 - lev_sgov * 2]   # SGOV 높으면 음수
    mean_s  = float(np.mean(signals))
    std_s   = float(np.std(signals))
    confidence = max(0.0, 1.0 - std_s)   # 신호 일치할수록 높음

    if mean_s > 0.15:
        regime = "risk_on"
    elif mean_s < -0.15:
        regime = "risk_off"
    else:
        regime = "neutral"

    return regime, round(confidence, 2)


# ── 최종 배분 계산 ────────────────────────────────────────────────────────────

def _build_weights(
    regime:        str,
    ranker_scores: dict[str, float],
    lev_weights:   dict[str, float],
    excess_sig:    float,
    sw:            SignalWeights,
) -> dict[str, float]:
    """체제 + 신호 → 최종 비중 (합계 1.0)."""

    # 기본 배분 (체제별)
    if regime == "risk_on":
        base = {
            "SGOV":  0.20, "QQQI": 0.05,
            "MSFT":  0.10, "NVDA": 0.12, "GOOGL": 0.10,
            "ORCL":  0.10, "UNH":  0.07, "SAP":   0.04, "SPMO": 0.07,
            "QLD":   0.08, "TQQQ": 0.04, "SOXL":  0.01, "UPRO": 0.02,
        }
    elif regime == "risk_off":
        base = {
            "SGOV":  0.50, "QQQI": 0.10,
            "MSFT":  0.08, "NVDA": 0.08, "GOOGL": 0.06,
            "ORCL":  0.06, "UNH":  0.05, "SAP":   0.03, "SPMO": 0.02,
            "QLD":   0.01, "TQQQ": 0.01, "SOXL":  0.00, "UPRO": 0.00,
        }
    else:   # neutral
        base = {
            "SGOV":  0.35, "QQQI": 0.08,
            "MSFT":  0.09, "NVDA": 0.10, "GOOGL": 0.08,
            "ORCL":  0.08, "UNH":  0.06, "SAP":   0.03, "SPMO": 0.05,
            "QLD":   0.04, "TQQQ": 0.02, "SOXL":  0.01, "UPRO": 0.01,
        }

    # Ranker 조정: 개별주 비중 ×(0.7~1.3)
    if ranker_scores:
        for ticker, norm_score in ranker_scores.items():
            if ticker in base:
                base[ticker] = max(0.0, base[ticker] * (1.0 + sw.ranker * norm_score))

    # LeverageModel 조정: 레버리지 vs SGOV 비율
    lev_sgov = lev_weights.get("SGOV", 0.6)
    lev_qld  = lev_weights.get("QLD",  0.1)
    lev_tqqq = lev_weights.get("TQQQ", 0.1)
    if "QLD" in base:
        base["QLD"]  = base["QLD"]  * (1 + sw.leverage * (lev_qld  * 2 - 1))
        base["TQQQ"] = base["TQQQ"] * (1 + sw.leverage * (lev_tqqq * 2 - 1))
        base["SGOV"] = base["SGOV"] * (1 + sw.leverage * (lev_sgov * 2 - 1))

    # 음수 제거 + 정규화
    base = {k: max(0.0, v) for k, v in base.items()}
    total = sum(base.values())
    if total > 0:
        base = {k: round(v / total, 4) for k, v in base.items()}

    return base


# ── 공개 API ──────────────────────────────────────────────────────────────────

_REENTRY_GUARD = False   # meta→ranker→meta 무한 상호 재귀 방지

# 결과 캐시 — ExcessReturn walk-forward 학습 등 신호 계산이 수십 초 걸리므로
# /dca·/report·/rebalance·주문서가 연달아 호출해도 1회만 계산
META_CACHE_TTL_S = 900   # 15분
_RESULT_CACHE: dict[tuple, tuple[float, "MetaAllocation"]] = {}


def _meta_cache_file(market_type: str, phase_key) -> "Path":
    from pathlib import Path
    import os
    from ml._safe_cache import harden_cache_dir
    d = Path(os.path.expanduser("~/reports/ml-cache"))
    d.mkdir(parents=True, exist_ok=True)
    harden_cache_dir(d)  # 0700 best-effort — 타 사용자 파일 주입 방지
    return d / f"meta_alloc_{market_type}_{phase_key}.pkl"


def _load_meta_cache(market_type: str, phase_key) -> Optional["MetaAllocation"]:
    import time
    key = (market_type, str(phase_key))
    hit = _RESULT_CACHE.get(key)
    if hit and time.time() - hit[0] < META_CACHE_TTL_S:
        return hit[1]
    try:   # 파일 캐시 (크론 등 별도 프로세스와 공유)
        path = _meta_cache_file(market_type, phase_key)
        if path.exists() and time.time() - path.stat().st_mtime < META_CACHE_TTL_S:
            # 안전 로더: 심링크·소유자 검증 후 역직렬화(실패 시 None=캐시 미스)
            from ml._safe_cache import safe_unpickle
            alloc = safe_unpickle(path)
            if alloc is None:
                return None
            _RESULT_CACHE[key] = (path.stat().st_mtime, alloc)
            return alloc
    except Exception:
        pass
    return None


def _save_meta_cache(market_type: str, phase_key, alloc: "MetaAllocation"):
    import time, pickle, os
    _RESULT_CACHE[(market_type, str(phase_key))] = (time.time(), alloc)
    try:
        path = _meta_cache_file(market_type, phase_key)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(pickle.dumps(alloc))
        os.replace(tmp, path)
    except Exception:
        pass


def get_meta_allocation(
    market_type: str = "neutral",
    phase_key         = 0,
    force: bool = False,
) -> MetaAllocation:
    """현재 시황 기반 통합 포트폴리오 배분 (15분 캐시, 재진입 시 즉시 예외)."""
    global _REENTRY_GUARD
    if _REENTRY_GUARD:
        raise RuntimeError("get_meta_allocation 재진입 차단 — 신호 함수가 메타를 역호출함")
    if not force:
        cached = _load_meta_cache(market_type, phase_key)
        if cached is not None:
            return cached
    _REENTRY_GUARD = True
    try:
        alloc = _get_meta_allocation_impl(market_type, phase_key)
        _save_meta_cache(market_type, phase_key, alloc)
        return alloc
    finally:
        _REENTRY_GUARD = False


def _get_meta_allocation_impl(
    market_type: str = "neutral",
    phase_key         = 0,
) -> MetaAllocation:
    """현재 시황 기반 통합 포트폴리오 배분."""
    import warnings; warnings.filterwarnings("ignore")
    logger.info("MetaAllocator 신호 수집 중...")

    # 신호 수집 (병렬 처리가 아닌 순차 — 각각 캐시 활용)
    ranker_scores = _get_ranker_signal()
    excess_sig    = _get_excess_signal()
    lev_weights   = _get_leverage_signal()
    fg_sig        = _get_fg_signal()
    phase_sig     = _get_phase_signal(market_type, phase_key)

    # 신호 강도에 따른 SignalWeights (Phase별 동적)
    if market_type == "bear" and isinstance(phase_key, int) and phase_key >= 3:
        sw = SignalWeights(ranker=0.30, excess=0.15, leverage=0.25, fg_proxy=0.15, phase=0.15)
    elif market_type == "bull":
        sw = SignalWeights(ranker=0.20, excess=0.25, leverage=0.10, fg_proxy=0.25, phase=0.20)
    else:
        sw = SignalWeights()

    regime, confidence = _determine_regime(
        excess=excess_sig, fg=fg_sig,
        phase=phase_sig,   lev_sgov=lev_weights.get("SGOV", 0.5),
    )

    weights = _build_weights(regime, ranker_scores, lev_weights, excess_sig, sw)

    # 요약 노트
    top3_equity = sorted(
        [(t, w) for t, w in weights.items()
         if t in ASSET_CLASSES["equity"] and w > 0.01],
        key=lambda x: x[1], reverse=True,
    )[:3]
    top3_str = "  ".join(f"{t}({w:.0%})" for t, w in top3_equity)

    note = (
        f"체제: {regime}  신뢰도: {confidence:.0%}\n"
        f"Ranker 상위 3: {top3_str}\n"
        f"레버리지/현금: {sum(weights.get(t,0) for t in ['QLD','TQQQ','SOXL','UPRO']):.0%}"
        f" / {weights.get('SGOV',0):.0%}"
    )

    logger.info("MetaAllocator 완료: %s (신뢰도 %.0f%%)", regime, confidence * 100)

    return MetaAllocation(
        weights        = weights,
        signal_summary = {
            "ranker_breadth": round(float(np.mean(list(ranker_scores.values()))) if ranker_scores else 0, 3),
            "excess_signal":  round(excess_sig, 3),
            "fg_proxy":       round(fg_sig * 50 + 50, 1),   # 0~100
            "phase_signal":   round(phase_sig, 3),
            "lev_sgov":       round(lev_weights.get("SGOV", 0.5), 2),
        },
        regime     = regime,
        confidence = confidence,
        note       = note,
        timestamp  = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    )


def format_meta_report(alloc: MetaAllocation) -> str:
    """텔레그램 발송용 MetaAllocator 리포트."""
    regime_emoji = {"risk_on": "🟢", "risk_off": "🔴", "neutral": "🟡"}.get(alloc.regime, "⚪")
    lines = [
        "🤖 MetaAllocator — ML 통합 포트폴리오",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"{regime_emoji} 체제: {alloc.regime.upper()}  |  신뢰도: {alloc.confidence:.0%}",
        "",
        "[ 신호 요약 ]",
        f"  Ranker 강도:    {alloc.signal_summary['ranker_breadth']:+.3f}",
        f"  ExcessReturn:   {alloc.signal_summary['excess_signal']:+.3f}",
        f"  F&G Proxy:      {alloc.signal_summary['fg_proxy']:.1f}/100",
        f"  Phase 신호:     {alloc.signal_summary['phase_signal']:+.2f}",
        f"  레버리지 SGOV:  {alloc.signal_summary['lev_sgov']:.0%}",
        "",
        "[ 통합 권장 비중 ]",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # 자산군별 그룹
    groups = [
        ("현금/안전자산", ["SGOV"]),
        ("배당",         ["QQQI"]),
        ("개별주",       ["MSFT","NVDA","GOOGL","ORCL","UNH","SAP","SPMO"]),
        ("레버리지",     ["QLD","TQQQ","SOXL","UPRO"]),
    ]
    for group, tickers in groups:
        group_sum = sum(alloc.weights.get(t, 0) for t in tickers)
        if group_sum < 0.005:
            continue
        lines.append(f"  [{group}]  합계 {group_sum:.0%}")
        for t in tickers:
            w = alloc.weights.get(t, 0)
            if w < 0.005:
                continue
            bar = "█" * max(1, int(w * 20))
            lines.append(f"    {t:<6}  {w:5.1%}  {bar}")

    lines += [
        "",
        alloc.note,
        "",
        "⚠️ 모든 비중은 참고용 — 실매매 미연결",
        f"({alloc.timestamp})",
    ]
    return "\n".join(lines)
