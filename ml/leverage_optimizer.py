"""ml/leverage_optimizer.py — 레버리지 전략 파라미터 스위트스팟 탐색

최적화 목표: 진입·청산·비중 파라미터 그리드 탐색 → Calmar ratio 최대화
방법:
  1. BacktestEngine — 단일 파라미터 세트 백테스트 (shift(1) 룩어헤드 방지)
     - 기초지수 자동 선택: QLD/TQQQ→QQQ, UPRO→SPY, SOXL→SMH
  2. walk_forward_optimize() — 18개월 학습 / 6개월 OOS 롤링 WF
  3. optimize_leverage() — 종목별 독립 Optuna TPE 탐색 후 결과 통합
  4. 결과 저장 / 로드 → leverage_signal.py 자동 연동

종목별 기초지수:
  QLD   (QQQ 2×) → QQQ 낙폭·RSI·MA 기준
  TQQQ  (QQQ 3×) → QQQ 기준
  UPRO  (SPY 3×) → SPY 기준  ← 기존에 QQQ로 잘못 평가되던 문제 수정
  SOXL  (SMH 3×) → SMH 기준 (반도체)

파라미터 공간 (instrument 고정 후 나머지 10개):
  min_dd        : 진입 최소 낙폭 (-3% ~ -25%)
  max_vix_entry : 진입 시 VIX 상한 (18 ~ 50)
  min_rsi_entry : 진입 시 RSI 하한 (20 ~ 55)
  lev_weight    : 레버리지 최대 비중 (10% ~ 55%)
  sgov_floor    : SGOV 최소 비중 (20% ~ 65%)
  exit_ma       : 청산 MA 기간 (5 ~ 60일)
  exit_vix      : VIX 급등 청산 (25 ~ 48)
  trailing_stop : 진입가 대비 트레일링 스탑 (-3% ~ -22%)
  hold_days_max : 최대 보유 기간 (14 ~ 130일)
"""
from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RESULTS_PATH = Path.home() / "reports" / "ml-cache" / "leverage_best_params.json"
KST = timezone(timedelta(hours=9))

RF_ANNUAL = 0.0425   # 무위험 금리

# ── 종목별 기초지수 맵핑 ──────────────────────────────────────────────────────
# 진입·청산 신호는 기초지수 기준으로 계산 (잘못된 QQQ 통일 수정)
INSTRUMENT_UNDERLYING = {
    "QLD":  "QQQ",   # NASDAQ-100 2×
    "TQQQ": "QQQ",   # NASDAQ-100 3×
    "UPRO": "SPY",   # S&P500 3×
    "SOXL": "SMH",   # 반도체 3× (SOX 추종)
}
# 최적화 대상 종목 (순서: 2× 먼저, 3× 나중)
OPT_INSTRUMENTS = ["QLD", "TQQQ", "UPRO"]

# ── 파라미터 공간 (instrument 제외) ──────────────────────────────────────────

PARAM_GRID = {
    "min_dd":         [-0.05, -0.08, -0.10, -0.12, -0.15, -0.20, -0.25],
    "max_vix_entry":  [25, 30, 35, 40, 50],
    "min_rsi_entry":  [20, 30, 40, 50],
    "lev_weight":     [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50],
    "sgov_floor":     [0.25, 0.35, 0.45, 0.55],
    "exit_ma":        [10, 20, 30, 50],
    "exit_vix":       [28, 32, 36, 40, 45],
    "trailing_stop":  [-0.05, -0.08, -0.10, -0.12, -0.15, -0.20],
    "hold_days_max":  [21, 42, 63, 126],
}

OPTUNA_SPACE = {
    "min_dd":         ("float",  -0.25, -0.03),
    "max_vix_entry":  ("float",   18.0, 50.0),
    "min_rsi_entry":  ("float",   20.0, 55.0),
    "lev_weight":     ("float",    0.10,  0.55),
    "sgov_floor":     ("float",    0.20,  0.65),
    "exit_ma":        ("int",      5,    60),
    "exit_vix":       ("float",   25.0, 48.0),
    "trailing_stop":  ("float",  -0.22, -0.03),
    "hold_days_max":  ("int",     14,   130),
}


# ── 결과 컨테이너 ─────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    params:       dict
    cagr:         float
    sharpe:       float
    calmar:       float
    max_dd:       float
    n_trades:     int
    win_rate:     float
    equity:       pd.Series = field(default_factory=pd.Series)
    period:       str = ""


@dataclass
class OptimizationResult:
    best_params:     dict
    best_calmar:     float
    best_sharpe:     float
    best_cagr:       float
    best_max_dd:     float
    wf_results:      list[BacktestResult]   # Walk-forward OOS 결과
    wf_mean_calmar:  float
    wf_std_calmar:   float
    all_trials:      pd.DataFrame
    optimized_at:    str = ""
    wf_median_calmar: float = 0.0           # OOS 안정성 대표값 (이상치에 강건)


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def _load_prices(days: int = 2520) -> dict[str, pd.Series]:
    from ml.data_pipeline import fetch_prices
    tickers = [
        "QLD", "TQQQ", "SOXL", "UPRO",
        "QQQ", "SPY", "SMH",            # 기초지수별 별도 추가 (UPRO→SPY, SOXL→SMH)
        "SGOV", "^VIX", "^VIX3M", "HYG", "IEF", "SHV",
    ]
    p = fetch_prices(tickers, days=days)
    result = {t: df["Close"] for t, df in p.items() if "Close" in df.columns}

    # SGOV 없는 기간(2020 이전) → SHV 스케일 보정으로 연결
    sgov  = result.get("SGOV")
    shv   = result.get("SHV")
    if sgov is not None and shv is not None:
        scale = float(sgov.iloc[0] / shv.reindex(sgov.index).iloc[0]) if len(shv.reindex(sgov.index)) > 0 else 1.0
        pre   = shv.loc[:sgov.index[0]] * scale
        filled = pd.concat([pre, sgov]).sort_index()
        filled = filled[~filled.index.duplicated(keep="last")]
        result["SGOV"] = filled

    # SMH 없으면 QQQ 대용 (반도체 ETF 데이터 기간 짧을 수 있음)
    if "SMH" not in result and "QQQ" in result:
        result["SMH"] = result["QQQ"]
        logger.warning("SMH 데이터 없음 — QQQ로 대체 (SOXL 백테스트 근사치)")

    return result


# ── 백테스트 엔진 ─────────────────────────────────────────────────────────────

class BacktestEngine:
    """단일 파라미터 세트 레버리지 전략 백테스트.

    포지션 상태:
      SGOV  — 기본 현금성 자산 (항상 보유)
      inst  — 레버리지 ETF (조건 충족 시 보유)

    룩어헤드 방지:
      모든 신호는 shift(1): t일 종가 신호 → t+1일 시가 실행.
    """

    def __init__(self, params: dict, prices: dict[str, pd.Series],
                 eval_start: pd.Timestamp | None = None):
        """eval_start: 지정 시 신호는 전체 히스토리로 계산하되 포트폴리오 평가는
        eval_start 이후만 수행 — WF OOS 폴드에서 RSI/MA 웜업 NaN과 폴드-로컬
        낙폭 앵커(cummax가 폴드 시작점에서 리셋) 왜곡을 방지."""
        self.p    = params
        self.inst = params["instrument"]
        self.px   = prices
        # 기초지수: UPRO→SPY, SOXL→SMH, QLD/TQQQ→QQQ
        self.underlying = INSTRUMENT_UNDERLYING.get(self.inst, "QQQ")

        # 공통 날짜 인덱스 (기초지수 기준)
        base = prices.get(self.underlying)
        if base is None:
            base = prices.get("QQQ")
        req  = [self.inst, self.underlying, "SGOV", "^VIX"]
        idx  = base.dropna().index
        for t in req:
            s = prices.get(t)
            if s is not None:
                idx = idx.intersection(s.dropna().index)
        self.idx = sorted(idx)
        self.eval_start = pd.Timestamp(eval_start) if eval_start is not None else None

    def _signals(self) -> pd.DataFrame:
        """모든 신호 계산 — 기초지수(QQQ/SPY/SMH) 기준 낙폭·RSI·MA."""
        _und = self.px.get(self.underlying)
        if _und is None:
            _und = self.px.get("QQQ")
        und = _und.reindex(self.idx)
        vix = self.px["^VIX"].reindex(self.idx).ffill()

        # 낙폭 (기초지수 기준)
        dd    = (und / und.cummax() - 1)
        # RSI (기초지수 기준)
        delta = und.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        # 청산 MA (기초지수 기준)
        und_ma = und.rolling(self.p["exit_ma"], min_periods=5).mean()
        # 실현 변동성 (20일, 연율화) — vol targeting 용
        real_vol = und.pct_change().rolling(20).std() * np.sqrt(252)

        # VIX 텀스트럭처: VIX3M/VIX (>1 콘탱고=정상, <1 백워데이션=단기 폭풍 가격반영)
        vix3m = self.px.get("^VIX3M")
        if vix3m is not None:
            vix_term = (vix3m.reindex(self.idx).ffill() / vix).fillna(1.0)
        else:
            vix_term = pd.Series(1.0, index=self.idx)

        # 진입: 기초지수 낙폭 충분 + VIX 낮음 + RSI 낮음 (+ 텀스트럭처 정상)
        enter = (
            (dd <= self.p["min_dd"]) &
            (vix <= self.p["max_vix_entry"]) &
            (rsi <= self.p["min_rsi_entry"])
        )
        min_term = float(self.p.get("min_vix_term", 0.0) or 0.0)
        if min_term > 0:
            enter = enter & (vix_term >= min_term)

        # 추세선 위치 (run()에서 below_trend_scale 비중 조절에 사용; scale=0이면 기존 게이트와 동일)
        trend_ma = int(self.p.get("trend_ma", 0) or 0)
        if trend_ma > 0:
            trend_line  = und.rolling(trend_ma, min_periods=trend_ma // 2).mean()
            above_trend = (und > trend_line).fillna(True)
        else:
            above_trend = pd.Series(True, index=self.idx)

        # 청산: 기초지수 MA 이탈 OR VIX 급등 OR 백워데이션 심화
        exit_ = (und < und_ma) | (vix >= self.p["exit_vix"])
        if min_term > 0:
            exit_ = exit_ | (vix_term < min_term - 0.05)

        sig = pd.DataFrame({
            "dd": dd, "vix": vix, "rsi": rsi,
            "real_vol": real_vol,
            "vix_term": vix_term,
            "above_trend": above_trend.astype(bool),
            "enter": enter.astype(bool),
            "exit":  exit_.astype(bool),
        }, index=self.idx)
        return sig

    def run(self) -> BacktestResult:
        sig   = self._signals()
        inst  = self.px[self.inst].reindex(self.idx)
        sgov  = self.px["SGOV"].reindex(self.idx)

        lev_w   = self.p["lev_weight"]
        sgov_fl = self.p["sgov_floor"]
        t_stop  = self.p["trailing_stop"]
        max_hd  = self.p["hold_days_max"]
        cost    = float(self.p.get("cost_bps", 5.0)) / 10000   # 편도 거래비용
        n_tr    = max(1, int(self.p.get("n_tranches", 1)))
        tr_step = float(self.p.get("tranche_dd_step", 0.04))
        below_scale = float(self.p.get("below_trend_scale", 0.0))  # 추세선 아래 비중 (0=게이트)
        eq_limit    = self.p.get("eq_dd_limit")                    # 자금곡선 스톱 (None=off)

        # 평가 시작 위치 (eval_start 이전은 신호 웜업 구간 — 포트폴리오 평가 제외)
        start_pos = 0
        if self.eval_start is not None:
            start_pos = int(np.searchsorted(pd.DatetimeIndex(self.idx), self.eval_start))
            start_pos = min(start_pos, max(len(self.idx) - 2, 0))

        # 포트폴리오 상태 — 초기 자본 1.0 전액 SGOV (현금 이중계상 금지:
        # 기존 cash=1.0 + SGOV 1.0 구조는 자본의 절반이 0% 수익 유휴현금으로 잠김)
        cash        = 0.0
        lev_shares  = 0.0
        sgov_px0    = float(sgov.iloc[start_pos])
        sgov_shares = 1.0 / sgov_px0 if np.isfinite(sgov_px0) and sgov_px0 > 0 else 0.0
        avg_entry   = 0.0       # 가중평균 진입가 (트레일링스탑 기준)
        entry_date  = None      # 첫 트랜치 진입일
        filled      = 0         # 채워진 트랜치 수
        n_trades    = 0
        trade_rets  = []
        port_values = []
        eq_peak     = 1.0

        # 진입/청산 신호는 shift(1): 오늘 신호 → 내일 실행
        enter_signal = sig["enter"].shift(1).fillna(False)
        exit_signal  = sig["exit"].shift(1).fillna(False)
        dd_prev      = sig["dd"].shift(1)
        above_prev   = sig["above_trend"].shift(1).fillna(True)

        eval_idx = self.idx[start_pos:]
        for i, date in enumerate(self.idx[start_pos:], start=start_pos):
            pv_inst = float(inst.iloc[i]) if not np.isnan(inst.iloc[i]) else (inst.iloc[i-1] if i > 0 else 1.0)
            pv_sgov = float(sgov.iloc[i]) if not np.isnan(sgov.iloc[i]) else (sgov.iloc[i-1] if i > 0 else 1.0)

            # ── 청산 체크 (전 트랜치 일괄) ──
            if lev_shares > 0:
                ret_from_entry = (pv_inst / avg_entry - 1) if avg_entry > 0 else 0
                hold_days = (date - entry_date).days if entry_date else 0
                should_exit = (
                    exit_signal.iloc[i] or
                    ret_from_entry <= t_stop or
                    hold_days >= max_hd
                )
                if should_exit:
                    cash += lev_shares * pv_inst * (1 - cost)
                    lev_shares = 0.0
                    trade_rets.append(ret_from_entry)
                    n_trades += 1
                    avg_entry, entry_date, filled = 0.0, None, 0

            # ── 자금곡선 스톱: 전략 자체 낙폭이 한도 초과면 신규 진입 차단 ──
            eq_blocked = False
            if eq_limit and port_values:
                eq_blocked = (port_values[-1] / eq_peak - 1) <= -float(eq_limit)

            # ── 진입 체크 (트랜치 k: 낙폭이 min_dd - k×step 도달 시 추가 진입) ──
            if (not eq_blocked and filled < n_tr and enter_signal.iloc[i]):
                dd_y = float(dd_prev.iloc[i]) if np.isfinite(dd_prev.iloc[i]) else 0.0
                if dd_y <= self.p["min_dd"] - filled * tr_step:
                    total = cash + lev_shares * pv_inst + sgov_shares * pv_sgov
                    # vol targeting: 실현변동성이 목표를 넘으면 비중 축소
                    target_vol = self.p.get("target_vol")
                    vol_scale  = 1.0
                    if target_vol:
                        rv = float(sig["real_vol"].iloc[i - 1]) if i > 0 else float("nan")
                        if np.isfinite(rv) and rv > 1e-6:
                            vol_scale = min(1.0, target_vol / rv)
                    # 소프트 추세 스케일: 추세선 아래면 비중 below_scale배 (0이면 진입 안 함)
                    trend_scale = 1.0 if bool(above_prev.iloc[i]) else below_scale
                    invest = total * lev_w * vol_scale * trend_scale / n_tr
                    # 자금 조달: 현금 부족분은 SGOV 매도로 충당 (sgov_floor 하한 유지)
                    shortfall = invest - cash
                    if shortfall > 0 and sgov_shares > 0:
                        sellable = max(0.0, sgov_shares * pv_sgov - total * sgov_fl)
                        sell_amt = min(shortfall, sellable)
                        if sell_amt > 0:
                            sgov_shares -= sell_amt / pv_sgov
                            cash        += sell_amt
                    invest = min(invest, cash)
                    if invest > 1e-9:
                        buy_shares = invest * (1 - cost) / pv_inst
                        # 가중평균 진입가 갱신
                        prev_val   = lev_shares * avg_entry
                        lev_shares += buy_shares
                        avg_entry  = (prev_val + buy_shares * pv_inst) / lev_shares
                        cash      -= invest
                        if entry_date is None:
                            entry_date = date
                        filled += 1

            # ── SGOV 리밸런싱 (현금 → SGOV) ──
            sgov_val = sgov_shares * pv_sgov
            total    = cash + lev_shares * pv_inst + sgov_val
            target_sgov = max(total * sgov_fl, total * (1 - lev_w) * 0.8)
            if sgov_val < target_sgov * 0.9 and cash > 0:
                add = min(cash, target_sgov - sgov_val)
                sgov_shares += add / pv_sgov
                cash        -= add

            pv = cash + lev_shares * pv_inst + sgov_shares * pv_sgov
            port_values.append(pv)
            eq_peak = max(eq_peak, pv)

        equity = pd.Series(port_values, index=eval_idx)
        rets   = equity.pct_change().dropna()

        # 성과 지표
        n_days  = (eval_idx[-1] - eval_idx[0]).days
        years   = max(n_days / 365.25, 0.1)
        cagr    = float(equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
        rf_d    = RF_ANNUAL / 252
        excess  = rets - rf_d
        sharpe  = float(excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0.0
        peak    = equity.cummax()
        dd_s    = (equity / peak - 1)
        max_dd  = float(dd_s.min())
        # MDD 분모 floor 5%: 낙폭이 거의 없는 폴드의 Calmar 발산 방지
        calmar  = cagr / max(abs(max_dd), 0.05)
        win_r   = float(np.mean([r > 0 for r in trade_rets])) if trade_rets else 0.5

        return BacktestResult(
            params    = self.p,
            cagr      = round(cagr, 4),
            sharpe    = round(sharpe, 3),
            calmar    = round(calmar, 3),
            max_dd    = round(max_dd, 4),
            n_trades  = n_trades,
            win_rate  = round(win_r, 3),
            equity    = equity,
        )


# ── 복합 점수 ─────────────────────────────────────────────────────────────────

def composite_score(r: BacktestResult) -> float:
    """복합 점수 — QQQ 대비 초과수익 + MDD 제한 + 최소 거래 요건.

    설계 원칙:
      - 거래 3건 미만 → 무효 (SGOV만 보유하는 비활성 전략 방지)
      - CAGR이 무위험금리(4.25%) 이하 → 낮은 점수
      - Calmar 최대 5.0 캡 (MDD≈0 분모 발산 방지)
      - Calmar 비중 최대 + MDD 25% 초과분 직접 페널티 — 낙폭 방어 우선
    """
    if r.max_dd < -0.55:
        return float("-inf")   # MDD 55% 초과 = 사용 불가
    if r.n_trades < 3:
        return float("-inf")   # 최소 3건 거래 없으면 무효
    if r.cagr < RF_ANNUAL * 0.5:
        return float("-inf")   # 무위험금리의 절반도 안 나오면 무효

    calmar  = min(r.calmar, 5.0) if np.isfinite(r.calmar) and r.calmar > 0 else 0.0
    sharpe  = r.sharpe if np.isfinite(r.sharpe) else 0.0
    cagr_sc = min(r.cagr / 0.20, 1.5)   # CAGR 20% 기준으로 정규화 (최대 1.5)
    score   = calmar * 0.45 + sharpe * 0.25 + cagr_sc * 0.30
    if r.max_dd < -0.25:
        score -= (abs(r.max_dd) - 0.25) * 2.0   # MDD 25% 초과분 페널티
    return score


# ── Walk-Forward 최적화 ────────────────────────────────────────────────────────

def walk_forward_optimize(
    prices:           dict[str, pd.Series],
    train_months:     int = 18,
    test_months:      int = 6,
    step_months:      int = 3,
    n_optuna:         int = 80,
    fixed_instrument: str | None = None,
) -> list[BacktestResult]:
    """롤링 Walk-Forward: 훈련 창에서 Optuna 최적화 → OOS 평가."""
    # 기초지수 기준으로 날짜 범위 설정
    inst       = fixed_instrument or OPT_INSTRUMENTS[0]
    underlying = INSTRUMENT_UNDERLYING.get(inst, "QQQ")
    ref        = prices.get(underlying)
    if ref is None:
        ref = prices.get("QQQ")
    if ref is None:
        return []

    idx       = sorted(ref.dropna().index)
    start     = idx[0]
    wf_results: list[BacktestResult] = []

    cursor = start + pd.DateOffset(months=train_months)
    while cursor + pd.DateOffset(months=test_months) <= idx[-1] + pd.DateOffset(days=1):
        train_end  = cursor
        test_end   = cursor + pd.DateOffset(months=test_months)

        train_prices = {t: s.loc[:train_end] for t, s in prices.items()}
        # OOS 평가: 신호(RSI/MA/낙폭 앵커)는 전체 히스토리로 계산하고
        # 포트폴리오 평가만 train_end 이후 수행 — 폴드-로컬 cummax/웜업 NaN 왜곡 방지
        test_prices  = {t: s.loc[:test_end] for t, s in prices.items()}

        best_p_raw = _optuna_search(train_prices, n_trials=n_optuna,
                                    fixed_instrument=fixed_instrument)
        best_p = {**best_p_raw, "instrument": inst}

        try:
            eng = BacktestEngine(best_p, test_prices, eval_start=train_end)
            res = eng.run()
            res.period = f"{train_end.strftime('%Y-%m')}~{test_end.strftime('%Y-%m')}"
            wf_results.append(res)
            logger.info(
                "WF [%s] %s: Calmar=%.2f  CAGR=%.1f%%  MDD=%.1f%%  trades=%d",
                inst, res.period, res.calmar, res.cagr*100, res.max_dd*100, res.n_trades,
            )
        except Exception as e:
            logger.warning("WF fold 실패 [%s]: %s", inst, e)

        cursor += pd.DateOffset(months=step_months)

    return wf_results


# ── Optuna 파라미터 탐색 ──────────────────────────────────────────────────────

def _optuna_search(
    prices: dict[str, pd.Series],
    n_trials: int = 80,
    fixed_instrument: str | None = None,
) -> dict:
    """Optuna TPE로 최적 파라미터 탐색.

    fixed_instrument가 지정되면 해당 종목으로 고정하고 나머지만 탐색.
    """
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def objective(trial):
            inst = fixed_instrument or trial.suggest_categorical("instrument", OPT_INSTRUMENTS)
            params = {
                "instrument":    inst,
                "min_dd":        trial.suggest_float("min_dd",             -0.25, -0.03),
                "max_vix_entry": trial.suggest_float("max_vix_entry",       18.0, 50.0),
                "min_rsi_entry": trial.suggest_float("min_rsi_entry",       20.0, 55.0),
                "lev_weight":    trial.suggest_float("lev_weight",           0.10,  0.55),
                "sgov_floor":    trial.suggest_float("sgov_floor",           0.20,  0.65),
                "exit_ma":       trial.suggest_int("exit_ma",                5,    60),
                "exit_vix":      trial.suggest_float("exit_vix",            25.0, 48.0),
                "trailing_stop": trial.suggest_float("trailing_stop",       -0.22, -0.03),
                "hold_days_max": trial.suggest_int("hold_days_max",         14,   130),
                "target_vol":    trial.suggest_float("target_vol",           0.15,  0.45),
                "trend_ma":      trial.suggest_categorical("trend_ma",      [0, 100, 200]),
                "below_trend_scale": trial.suggest_float("below_trend_scale", 0.0, 1.0),
                "min_vix_term":  trial.suggest_float("min_vix_term",         0.80,  1.05),
                "eq_dd_limit":   trial.suggest_float("eq_dd_limit",          0.10,  0.35),
                "n_tranches":    trial.suggest_int("n_tranches",             1,     3),
                "tranche_dd_step": trial.suggest_float("tranche_dd_step",    0.02,  0.06),
            }
            try:
                res = BacktestEngine(params, prices).run()
                return composite_score(res)
            except Exception:
                return float("-inf")

        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42),
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        return study.best_params

    except ImportError:
        return _grid_search(prices, fixed_instrument=fixed_instrument)


def _grid_search(
    prices: dict[str, pd.Series],
    fixed_instrument: str | None = None,
) -> dict:
    """Optuna 미설치 시 간략 그리드 서치 fallback."""
    import itertools
    instruments = [fixed_instrument] if fixed_instrument else OPT_INSTRUMENTS
    mini_grid = {
        "instrument":    instruments,
        "min_dd":        [-0.08, -0.12, -0.18],
        "max_vix_entry": [30, 40],
        "min_rsi_entry": [35, 45],
        "lev_weight":    [0.25, 0.35],
        "sgov_floor":    [0.35, 0.50],
        "exit_ma":       [20, 30],
        "exit_vix":      [32, 38],
        "trailing_stop": [-0.08, -0.12],
        "hold_days_max": [42, 63],
        "target_vol":    [0.25, 0.40],
        "trend_ma":      [0, 200],
        "below_trend_scale": [0.0, 0.5],
        "min_vix_term":  [0.0, 0.95],
        "eq_dd_limit":   [0.15, 0.35],
        "n_tranches":    [1, 3],
        "tranche_dd_step": [0.04],
    }
    best_score, best_params = float("-inf"), {}
    keys   = list(mini_grid.keys())
    combos = list(itertools.product(*mini_grid.values()))
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            res   = BacktestEngine(params, prices).run()
            score = composite_score(res)
            if score > best_score:
                best_score  = score
                best_params = params
        except Exception:
            pass
    return best_params


# ── 전체 최적화 실행 ──────────────────────────────────────────────────────────

def _optimize_one(
    instrument:    str,
    prices:        dict[str, pd.Series],
    n_optuna:      int,
    train_months:  int,
    test_months:   int,
    step_months:   int,
) -> OptimizationResult:
    """단일 종목 최적화 파이프라인 (instrument 고정)."""
    underlying = INSTRUMENT_UNDERLYING.get(instrument, "QQQ")
    logger.info("[%s] Optuna 탐색 시작 (기초지수: %s)", instrument, underlying)

    # instrument를 파라미터에 고정한 채 나머지 최적화
    best_p_raw = _optuna_search(prices, n_trials=n_optuna, fixed_instrument=instrument)
    best_p     = {**best_p_raw, "instrument": instrument}

    eng    = BacktestEngine(best_p, prices)
    best_r = eng.run()
    logger.info(
        "[%s] Best: Calmar=%.2f  CAGR=%.1f%%  MDD=%.1f%%  Sharpe=%.2f  trades=%d",
        instrument, best_r.calmar, best_r.cagr*100, best_r.max_dd*100,
        best_r.sharpe, best_r.n_trades,
    )

    wf = walk_forward_optimize(
        prices, train_months=train_months, test_months=test_months,
        step_months=step_months, n_optuna=max(30, n_optuna // 5),
        fixed_instrument=instrument,
    )
    wf_calmars = [r.calmar for r in wf if np.isfinite(r.calmar)]
    wf_mean   = float(np.mean(wf_calmars))   if wf_calmars else 0.0
    wf_std    = float(np.std(wf_calmars))    if len(wf_calmars) > 1 else 0.0
    wf_median = float(np.median(wf_calmars)) if wf_calmars else 0.0
    logger.info("[%s] WF: %d폴드  median=%.2f  mean=%.2f  std=%.2f",
                instrument, len(wf), wf_median, wf_mean, wf_std)

    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    return OptimizationResult(
        best_params    = best_p,
        best_calmar    = best_r.calmar,
        best_sharpe    = best_r.sharpe,
        best_cagr      = best_r.cagr,
        best_max_dd    = best_r.max_dd,
        wf_results     = wf,
        wf_mean_calmar = round(wf_mean, 3),
        wf_std_calmar  = round(wf_std, 3),
        wf_median_calmar = round(wf_median, 3),
        all_trials     = pd.DataFrame(),
        optimized_at   = ts,
    )


def optimize_leverage(
    days:          int = 2520,
    n_optuna:      int = 200,
    train_months:  int = 18,
    test_months:   int = 6,
    step_months:   int = 3,
) -> OptimizationResult:
    """전체 최적화 파이프라인 — 종목별 독립 최적화.

    OPT_INSTRUMENTS (QLD, TQQQ, UPRO) 각각 독립 Optuna 탐색 후
    전체 기간 Calmar 기준 최우수 종목을 대표 결과로 반환.
    모든 종목별 결과는 RESULTS_PATH에 함께 저장.
    """
    logger.info("레버리지 스위트스팟 최적화 시작 (종목: %s, n_trials=%d)",
                OPT_INSTRUMENTS, n_optuna)
    prices = _load_prices(days=days)

    # 종목별 독립 최적화
    per_instrument: dict[str, OptimizationResult] = {}
    for inst in OPT_INSTRUMENTS:
        try:
            res = _optimize_one(
                inst, prices, n_optuna, train_months, test_months, step_months,
            )
            per_instrument[inst] = res
        except Exception as e:
            logger.warning("[%s] 최적화 실패: %s", inst, e)

    if not per_instrument:
        raise RuntimeError("모든 종목 최적화 실패")

    # OOS 안정성(WF median Calmar) 기준 최우수 종목을 대표 결과로
    # — 전체기간 best_calmar는 in-sample 과적합에 취약하므로 보조 기준으로만 사용
    def _select_key(k: str) -> tuple:
        r = per_instrument[k]
        return (r.wf_median_calmar if r.wf_results else float("-inf"), r.best_calmar)
    best_inst = max(per_instrument, key=_select_key)
    best      = per_instrument[best_inst]

    save_result(best, per_instrument=per_instrument)
    logger.info("최적화 완료 — 최우수 종목: %s (WF median Calmar %.2f, 전체 Calmar %.2f)",
                best_inst, best.wf_median_calmar, best.best_calmar)
    return best


# ── 저장 / 로드 ───────────────────────────────────────────────────────────────

def _result_to_dict(r: OptimizationResult) -> dict:
    return {
        "best_params":     r.best_params,
        "best_calmar":     r.best_calmar,
        "best_sharpe":     r.best_sharpe,
        "best_cagr":       r.best_cagr,
        "best_max_dd":     r.best_max_dd,
        "wf_mean_calmar":  r.wf_mean_calmar,
        "wf_std_calmar":   r.wf_std_calmar,
        "wf_median_calmar": r.wf_median_calmar,
        "n_wf_folds":      len(r.wf_results),
        "optimized_at":    r.optimized_at,
        "wf_fold_calmars": [round(f.calmar, 3) for f in r.wf_results],
        "wf_fold_periods": [f.period for f in r.wf_results],
    }


def save_result(
    r: OptimizationResult,
    per_instrument: dict[str, OptimizationResult] | None = None,
) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = _result_to_dict(r)
    # 종목별 결과도 함께 저장
    if per_instrument:
        data["per_instrument"] = {
            inst: _result_to_dict(res)
            for inst, res in per_instrument.items()
        }
    RESULTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    logger.info("최적화 결과 저장: %s", RESULTS_PATH)


def load_result() -> Optional[dict]:
    if not RESULTS_PATH.exists():
        return None
    try:
        return json.loads(RESULTS_PATH.read_text())
    except Exception:
        return None


# ── 리포트 포맷 ───────────────────────────────────────────────────────────────

def format_optimization_report(r: OptimizationResult, bm_qqq: float = 0.0) -> str:
    p = r.best_params
    wf = r.wf_results

    lines = [
        "🏆 레버리지 스위트스팟 최적화 결과",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"최적화 시각: {r.optimized_at}",
        "",
        "[ 최적 파라미터 ]",
        f"  종목:      {p.get('instrument')}",
        f"  진입 낙폭: QQQ ≤ {p.get('min_dd', 0)*100:.1f}%",
        f"  진입 VIX:  ≤ {p.get('max_vix_entry', 0):.1f}",
        f"  진입 RSI:  ≤ {p.get('min_rsi_entry', 0):.1f}",
        f"  레버리지 비중: {p.get('lev_weight', 0)*100:.0f}%",
        f"  SGOV 하한: {p.get('sgov_floor', 0)*100:.0f}%",
        f"  청산 MA:   {p.get('exit_ma', 0)}일",
        f"  청산 VIX:  ≥ {p.get('exit_vix', 0):.1f}",
        f"  트레일링스탑: {p.get('trailing_stop', 0)*100:.1f}%",
        f"  최대보유: {p.get('hold_days_max', 0)}일",
        f"  목표변동성: {p['target_vol']*100:.0f}% (실현변동성 초과 시 비중 축소)" if p.get("target_vol") else "  목표변동성: 미사용",
        (f"  추세스케일: {p['trend_ma']}일 MA 아래 비중 ×{p.get('below_trend_scale', 0):.2f}"
         if p.get("trend_ma") else "  추세게이트: 미사용"),
        f"  VIX텀 진입: ≥ {p['min_vix_term']:.2f} (백워데이션 회피)" if p.get("min_vix_term") else "  VIX텀: 미사용",
        f"  자금곡선 스톱: 전략 낙폭 -{p['eq_dd_limit']*100:.0f}% 시 신규진입 차단" if p.get("eq_dd_limit") else "  자금곡선 스톱: 미사용",
        f"  분할진입: {p.get('n_tranches', 1)}트랜치 (낙폭 {p.get('tranche_dd_step', 0)*100:.1f}%p 간격)",
        f"  거래비용: {p.get('cost_bps', 5.0):.0f}bp/편도 반영",
        "",
        "[ 전체 기간 성과 ]",
        f"  CAGR:    {r.best_cagr*100:+.1f}%",
        f"  Sharpe:  {r.best_sharpe:.2f}",
        f"  Calmar:  {r.best_calmar:.2f}",
        f"  Max DD:  {r.best_max_dd*100:.1f}%",
    ]
    if bm_qqq > 0:
        lines.append(f"  QQQ 대비 초과: {(r.best_cagr - bm_qqq)*100:+.1f}%p")

    lines += [
        "",
        f"[ Walk-Forward OOS ({len(wf)}폴드) ]",
        f"  중앙값 Calmar: {r.wf_median_calmar:.2f}  (평균 {r.wf_mean_calmar:.2f} ± {r.wf_std_calmar:.2f})",
    ]
    if wf:
        lines.append("  폴드별 Calmar:")
        for fold in wf:
            bar = "█" * max(1, min(int(fold.calmar * 3), 10))
            lines.append(
                f"    {fold.period}  Calmar={fold.calmar:.2f}  "
                f"CAGR={fold.cagr*100:+.1f}%  MDD={fold.max_dd*100:.1f}%  {bar}"
            )

    lines += [
        "",
        "⚠️ 백테스트 결과 — 미래 수익 보장 없음",
        "⚠️ 실매매 연결 전 페이퍼트레이딩 권장",
    ]
    return "\n".join(lines)
