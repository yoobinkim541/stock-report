"""ml/leverage_optimizer.py — 레버리지 전략 파라미터 스위트스팟 탐색

최적화 목표: 진입·청산·비중 파라미터 그리드 탐색 → Calmar ratio 최대화
방법:
  1. BacktestEngine — 단일 파라미터 세트 백테스트 (shift(1) 룩어헤드 방지)
  2. walk_forward_optimize() — 18개월 학습 / 6개월 OOS 롤링 WF
  3. optimize_leverage() — Optuna TPE로 파라미터 공간 탐색
  4. 결과 저장 / 로드 → leverage_signal.py 자동 연동

파라미터 공간 (18개):
  instrument    : QLD / TQQQ (기준 레버리지 ETF)
  min_dd        : 진입 최소 낙폭 (-5% ~ -25%)
  max_vix_entry : 진입 시 VIX 상한 (20 ~ 50)
  min_rsi_entry : 진입 시 RSI 하한 (20 ~ 55)
  max_fg_entry  : 진입 시 FG 상한 (30 ~ 70)
  lev_weight    : 레버리지 최대 비중 (15% ~ 50%)
  sgov_floor    : SGOV 최소 비중 (25% ~ 60%)
  exit_ma       : 청산 MA 기간 (10 ~ 50일)
  exit_vix      : VIX 급등 청산 (28 ~ 45)
  trailing_stop : 진입가 대비 트레일링 스탑 (-5% ~ -20%)
  hold_days_max : 최대 보유 기간 (21 ~ 126일)
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

# ── 파라미터 공간 ─────────────────────────────────────────────────────────────

PARAM_GRID = {
    "instrument":     ["QLD", "TQQQ"],
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
    "instrument":     ("categorical", ["QLD", "TQQQ"]),
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


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def _load_prices(days: int = 2520) -> dict[str, pd.Series]:
    from ml.data_pipeline import fetch_prices
    tickers = ["QLD", "TQQQ", "SOXL", "UPRO", "QQQ", "SGOV", "^VIX", "HYG", "IEF", "SHV"]
    p = fetch_prices(tickers, days=days)
    result = {t: df["Close"] for t, df in p.items() if "Close" in df.columns}

    # SGOV 없는 기간(2020 이전) → SHV 또는 무위험금리 프록시로 보완
    sgov  = result.get("SGOV")
    shv   = result.get("SHV")
    if sgov is not None and shv is not None:
        # SGOV 시작 전 구간은 SHV로 채우기 (수익률 스케일 보정)
        scale = float(sgov.iloc[0] / shv.reindex(sgov.index).iloc[0]) if len(shv.reindex(sgov.index)) > 0 else 1.0
        pre   = shv.loc[:sgov.index[0]] * scale
        filled = pd.concat([pre, sgov]).sort_index()
        filled = filled[~filled.index.duplicated(keep="last")]
        result["SGOV"] = filled
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

    def __init__(self, params: dict, prices: dict[str, pd.Series]):
        self.p    = params
        self.inst = params["instrument"]
        self.px   = prices

        # 공통 날짜 인덱스
        req = [self.inst, "QQQ", "SGOV", "^VIX"]
        idx = prices["QQQ"].dropna().index
        for t in req:
            s = prices.get(t)
            if s is not None:
                idx = idx.intersection(s.dropna().index)
        self.idx = sorted(idx)

    def _signals(self) -> pd.DataFrame:
        """모든 신호 계산 (shift(1) 적용 전)."""
        qqq = self.px["QQQ"].reindex(self.idx)
        vix = self.px["^VIX"].reindex(self.idx).ffill()

        # 낙폭
        dd    = (qqq / qqq.cummax() - 1)
        # RSI
        delta = qqq.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        # MA
        qqq_ma = qqq.rolling(self.p["exit_ma"], min_periods=5).mean()
        # 진입 신호: 낙폭 충분 + VIX 낮음 + RSI 낮음
        enter = (
            (dd <= self.p["min_dd"]) &
            (vix <= self.p["max_vix_entry"]) &
            (rsi <= self.p["min_rsi_entry"])
        )
        # 청산 신호: MA 이탈 OR VIX 급등
        exit_ = (qqq < qqq_ma) | (vix >= self.p["exit_vix"])

        sig = pd.DataFrame({
            "dd": dd, "vix": vix, "rsi": rsi,
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

        # 포트폴리오 상태
        cash        = 1.0
        lev_shares  = 0.0
        sgov_shares = cash / float(sgov.iloc[0]) if not np.isnan(sgov.iloc[0]) else 0.0
        entry_price = 0.0
        entry_date  = None
        n_trades    = 0
        trade_rets  = []
        port_values = []

        # 진입/청산 신호는 shift(1): 오늘 신호 → 내일 실행
        enter_signal = sig["enter"].shift(1).fillna(False)
        exit_signal  = sig["exit"].shift(1).fillna(False)

        for i, date in enumerate(self.idx):
            pv_inst = float(inst.iloc[i]) if not np.isnan(inst.iloc[i]) else (inst.iloc[i-1] if i > 0 else 1.0)
            pv_sgov = float(sgov.iloc[i]) if not np.isnan(sgov.iloc[i]) else (sgov.iloc[i-1] if i > 0 else 1.0)

            in_pos = lev_shares > 0

            # ── 청산 체크 ──
            if in_pos:
                cur_val = lev_shares * pv_inst
                ret_from_entry = (pv_inst / entry_price - 1) if entry_price > 0 else 0

                hold_days = (date - entry_date).days if entry_date else 0

                should_exit = (
                    exit_signal.iloc[i] or
                    ret_from_entry <= t_stop or
                    hold_days >= max_hd
                )
                if should_exit:
                    cash += cur_val
                    lev_shares = 0.0
                    trade_rets.append(ret_from_entry)
                    n_trades += 1
                    entry_price = 0.0
                    entry_date  = None
                    in_pos      = False

            # ── 진입 체크 ──
            if not in_pos and enter_signal.iloc[i]:
                total = cash + lev_shares * pv_inst + sgov_shares * pv_sgov
                invest = total * lev_w
                if invest > cash * 0.95:
                    invest = cash * 0.95
                if invest > 0:
                    lev_shares  = invest / pv_inst
                    cash       -= invest
                    entry_price = pv_inst
                    entry_date  = date

            # ── SGOV 리밸런싱 (현금 → SGOV) ──
            sgov_val = sgov_shares * pv_sgov
            total    = cash + lev_shares * pv_inst + sgov_val
            target_sgov = max(total * sgov_fl, total * (1 - lev_w) * 0.8)
            if sgov_val < target_sgov * 0.9 and cash > 0:
                add = min(cash, target_sgov - sgov_val)
                sgov_shares += add / pv_sgov
                cash        -= add

            port_values.append(cash + lev_shares * pv_inst + sgov_shares * pv_sgov)

        equity = pd.Series(port_values, index=self.idx)
        rets   = equity.pct_change().dropna()

        # 성과 지표
        n_days  = (self.idx[-1] - self.idx[0]).days
        years   = max(n_days / 365.25, 0.1)
        cagr    = float(equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
        rf_d    = RF_ANNUAL / 252
        excess  = rets - rf_d
        sharpe  = float(excess.mean() / excess.std() * np.sqrt(252)) if excess.std() > 0 else 0.0
        peak    = equity.cummax()
        dd_s    = (equity / peak - 1)
        max_dd  = float(dd_s.min())
        calmar  = cagr / abs(max_dd) if max_dd < 0 else 0.0
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
      - Sharpe와 CAGR을 함께 고려해 균형 잡힌 전략 선호
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
    return calmar * 0.40 + sharpe * 0.30 + cagr_sc * 0.30


# ── Walk-Forward 최적화 ────────────────────────────────────────────────────────

def walk_forward_optimize(
    prices:        dict[str, pd.Series],
    train_months:  int = 18,
    test_months:   int = 6,
    step_months:   int = 3,
    n_optuna:      int = 80,
) -> list[BacktestResult]:
    """롤링 Walk-Forward: 훈련 창에서 Optuna 최적화 → OOS 평가.

    Returns: OOS BacktestResult 목록
    """
    qqq = prices.get("QQQ")
    if qqq is None:
        return []

    idx       = sorted(qqq.dropna().index)
    start     = idx[0]
    wf_results: list[BacktestResult] = []

    cursor = start + pd.DateOffset(months=train_months)
    while cursor + pd.DateOffset(months=test_months) <= idx[-1] + pd.DateOffset(days=1):
        train_end   = cursor
        test_end    = cursor + pd.DateOffset(months=test_months)

        train_prices = {t: s.loc[:train_end] for t, s in prices.items()}
        test_prices  = {t: s.loc[train_end:test_end] for t, s in prices.items()}

        # 훈련 창에서 최적 파라미터 탐색
        best_p = _optuna_search(train_prices, n_trials=n_optuna)

        # OOS 평가
        try:
            eng = BacktestEngine(best_p, test_prices)
            res = eng.run()
            res.period = f"{train_end.strftime('%Y-%m')}~{test_end.strftime('%Y-%m')}"
            wf_results.append(res)
            logger.info(
                "WF fold %s: Calmar=%.2f  CAGR=%.1f%%  MDD=%.1f%%  trades=%d",
                res.period, res.calmar, res.cagr * 100, res.max_dd * 100, res.n_trades,
            )
        except Exception as e:
            logger.warning("WF fold 실패: %s", e)

        cursor += pd.DateOffset(months=step_months)

    return wf_results


# ── Optuna 파라미터 탐색 ──────────────────────────────────────────────────────

def _optuna_search(prices: dict[str, pd.Series], n_trials: int = 80) -> dict:
    """Optuna TPE로 최적 파라미터 탐색."""
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def objective(trial):
            params = {
                "instrument":    trial.suggest_categorical("instrument",   ["QLD", "TQQQ"]),
                "min_dd":        trial.suggest_float("min_dd",             -0.25, -0.03),
                "max_vix_entry": trial.suggest_float("max_vix_entry",       18.0, 50.0),
                "min_rsi_entry": trial.suggest_float("min_rsi_entry",       20.0, 55.0),
                "lev_weight":    trial.suggest_float("lev_weight",           0.10,  0.55),
                "sgov_floor":    trial.suggest_float("sgov_floor",           0.20,  0.65),
                "exit_ma":       trial.suggest_int("exit_ma",                5,    60),
                "exit_vix":      trial.suggest_float("exit_vix",            25.0, 48.0),
                "trailing_stop": trial.suggest_float("trailing_stop",       -0.22, -0.03),
                "hold_days_max": trial.suggest_int("hold_days_max",         14,   130),
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
        return _grid_search(prices)


def _grid_search(prices: dict[str, pd.Series]) -> dict:
    """Optuna 미설치 시 간략 그리드 서치 fallback."""
    import itertools
    mini_grid = {
        "instrument":    ["QLD", "TQQQ"],
        "min_dd":        [-0.08, -0.12, -0.18],
        "max_vix_entry": [30, 40],
        "min_rsi_entry": [35, 45],
        "lev_weight":    [0.25, 0.35],
        "sgov_floor":    [0.35, 0.50],
        "exit_ma":       [20, 30],
        "exit_vix":      [32, 38],
        "trailing_stop": [-0.08, -0.12],
        "hold_days_max": [42, 63],
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

def optimize_leverage(
    days:          int = 2520,
    n_optuna:      int = 200,
    train_months:  int = 18,
    test_months:   int = 6,
    step_months:   int = 3,
) -> OptimizationResult:
    """전체 최적화 파이프라인.

    1. 전체 기간 Optuna 최적화 (in-sample 기준 파라미터 탐색)
    2. Walk-forward OOS 검증
    3. 결과 저장
    """
    logger.info("레버리지 스위트스팟 최적화 시작 (n_trials=%d)", n_optuna)
    prices = _load_prices(days=days)

    # ── Step 1: 전체 기간 최적 파라미터 ──
    logger.info("Step 1: 전체 기간 Optuna 탐색...")
    best_p = _optuna_search(prices, n_trials=n_optuna)
    eng    = BacktestEngine(best_p, prices)
    best_r = eng.run()
    logger.info(
        "Best params: Calmar=%.2f  CAGR=%.1f%%  MDD=%.1f%%  Sharpe=%.2f  trades=%d",
        best_r.calmar, best_r.cagr * 100, best_r.max_dd * 100, best_r.sharpe, best_r.n_trades,
    )

    # ── Step 2: Walk-Forward 검증 ──
    logger.info("Step 2: Walk-Forward 검증 (%d개월 학습 / %d개월 OOS)...",
                train_months, test_months)
    wf = walk_forward_optimize(
        prices, train_months=train_months, test_months=test_months,
        step_months=step_months, n_optuna=max(40, n_optuna // 4),
    )

    wf_calmars = [r.calmar for r in wf if np.isfinite(r.calmar)]
    wf_mean    = float(np.mean(wf_calmars)) if wf_calmars else 0.0
    wf_std     = float(np.std(wf_calmars))  if len(wf_calmars) > 1 else 0.0

    logger.info(
        "Walk-Forward: %d폴드  mean_Calmar=%.2f  std=%.2f",
        len(wf), wf_mean, wf_std,
    )

    # ── Step 3: 결과 저장 ──
    ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    result = OptimizationResult(
        best_params    = best_p,
        best_calmar    = best_r.calmar,
        best_sharpe    = best_r.sharpe,
        best_cagr      = best_r.cagr,
        best_max_dd    = best_r.max_dd,
        wf_results     = wf,
        wf_mean_calmar = round(wf_mean, 3),
        wf_std_calmar  = round(wf_std, 3),
        all_trials     = pd.DataFrame(),
        optimized_at   = ts,
    )
    save_result(result)
    return result


# ── 저장 / 로드 ───────────────────────────────────────────────────────────────

def save_result(r: OptimizationResult) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "best_params":     r.best_params,
        "best_calmar":     r.best_calmar,
        "best_sharpe":     r.best_sharpe,
        "best_cagr":       r.best_cagr,
        "best_max_dd":     r.best_max_dd,
        "wf_mean_calmar":  r.wf_mean_calmar,
        "wf_std_calmar":   r.wf_std_calmar,
        "n_wf_folds":      len(r.wf_results),
        "optimized_at":    r.optimized_at,
        "wf_fold_calmars": [round(f.calmar, 3) for f in r.wf_results],
        "wf_fold_periods": [f.period for f in r.wf_results],
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
        f"  평균 Calmar: {r.wf_mean_calmar:.2f} ± {r.wf_std_calmar:.2f}",
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
