#!/usr/bin/env python3
"""regime_core_backtest.py — 저MDD·지수 아웃퍼폼 코어 로직 후보 검증 (개발용)

가설: "지수가 200일 MA 위 + 변동성 정상 구간"에서만 레버리지를 들고,
그 외에는 SGOV로 피하면 — 레버리지 데케이(고변동 구간에 집중)는 피하고
복리 드리프트(저변동 상승 구간에 집중)만 취해 장기적으로 지수를 이기면서
MDD는 지수보다 낮출 수 있다 (Gayed, "Leverage for the Long Run" 변형).

파라미터는 문헌 표준값 고정 (그리드 탐색 없음 — 과적합 배제):
  추세: 200일 SMA / 변동성 타깃: 연 20% / VIX 텀: VIX3M/VIX < 0.95 = 회피
  실행: t 종가 신호 → t+1 종가 체결 (shift 1), 비용 5bp/편도

전략 비교:
  A. QQQ Buy&Hold (벤치마크)
  B. QLD Buy&Hold (레버리지 데케이 참고)
  C. 200MA 로테이션: QQQ ↔ SGOV
  D. 200MA 로테이션: QLD ↔ SGOV
  E. D + 변동성 타게팅 (목표 20%, QLD 비중 = min(1, 0.20/실현vol_QLD))
  F. E + VIX 텀 게이트 (백워데이션 시 전량 SGOV)
  G. F의 TQQQ 버전 (참고)

검증: 전체 기간 + 전·후반 분할 + 연도별 수익 — 특정 구간 의존 여부 확인.

실행:
    uv run python backtest/regime_core_backtest.py [--days 2520]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

COST = 5 / 10000          # 편도 5bp
MA_N = 200
TARGET_VOL = 0.20         # 연율 목표 변동성
VOL_WIN = 20
MIN_VIX_TERM = 0.95


def _load(days: int) -> dict[str, pd.Series]:
    from ml.leverage_optimizer import _load_prices   # SGOV←SHV 스플라이스 포함
    px = _load_prices(days=days)
    # 카나리아(EEM/AGG — Keller DAA)·금(GLD) 추가
    from ml.data_pipeline import fetch_prices
    extra = fetch_prices(["EEM", "AGG", "GLD"], days=days)
    for t, df in extra.items():
        if df is not None and "Close" in df.columns:
            px[t] = df["Close"]
    return px


def _metrics(eq: pd.Series, name: str) -> dict:
    eq = eq.dropna()
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = float(eq.iloc[-1] / eq.iloc[0]) ** (1 / max(years, 0.1)) - 1
    mdd = float((eq / eq.cummax() - 1).min())
    rf_d = 0.0425 / 252
    ex = rets - rf_d
    sharpe = float(ex.mean() / ex.std() * np.sqrt(252)) if ex.std() > 0 else 0.0
    return {"name": name, "cagr": cagr, "mdd": mdd, "sharpe": sharpe,
            "calmar": cagr / max(abs(mdd), 0.01), "final": float(eq.iloc[-1])}


def _run_weighted(w: pd.Series, risk_ret: pd.Series, sgov_ret: pd.Series) -> pd.Series:
    """위험자산 비중 w (이미 shift된 실행 비중) → 자본곡선. 턴오버 비용 반영."""
    w = w.fillna(0.0).clip(0, 1)
    turnover = w.diff().abs().fillna(w.iloc[0] if len(w) else 0)
    strat = w * risk_ret + (1 - w) * sgov_ret - turnover * COST
    return (1 + strat.fillna(0)).cumprod()


def build_strategies(px: dict[str, pd.Series]) -> tuple[dict[str, pd.Series], pd.DataFrame]:
    qqq, sgov = px["QQQ"].dropna(), px["SGOV"].dropna()
    idx = qqq.index.intersection(sgov.index)
    for t in ("QLD", "TQQQ"):
        if t in px:
            idx = idx.intersection(px[t].dropna().index)
    qqq, sgov = qqq.reindex(idx), sgov.reindex(idx)
    qld = px["QLD"].reindex(idx)
    tqqq = px["TQQQ"].reindex(idx)

    qqq_ret, sgov_ret = qqq.pct_change(), sgov.pct_change()
    qld_ret, tqqq_ret = qld.pct_change(), tqqq.pct_change()

    ma = qqq.rolling(MA_N, min_periods=MA_N).mean()
    above = (qqq > ma)

    # 변동성 타게팅: 레버리지 ETF 자체 실현변동성 기준
    vol_qld = qld_ret.rolling(VOL_WIN).std() * np.sqrt(252)
    vol_tqqq = tqqq_ret.rolling(VOL_WIN).std() * np.sqrt(252)

    # VIX 텀
    vix, vix3m = px.get("^VIX"), px.get("^VIX3M")
    if vix is not None and vix3m is not None:
        term = (vix3m.reindex(idx).ffill() / vix.reindex(idx).ffill())
    else:
        term = pd.Series(np.nan, index=idx)
    term_ok = term.isna() | (term >= MIN_VIX_TERM)   # 데이터 없으면 통과 (보수적 아님 주의)

    s = lambda x: x.shift(1)   # t 종가 신호 → t+1 실행

    eqs: dict[str, pd.Series] = {}
    eqs["A. QQQ B&H"] = (1 + qqq_ret.fillna(0)).cumprod()
    eqs["B. QLD B&H"] = (1 + qld_ret.fillna(0)).cumprod()
    eqs["C. 200MA QQQ/SGOV"] = _run_weighted(s(above.astype(float)), qqq_ret, sgov_ret)
    eqs["D. 200MA QLD/SGOV"] = _run_weighted(s(above.astype(float)), qld_ret, sgov_ret)

    w_vt = (TARGET_VOL / vol_qld).clip(upper=1.0).where(above, 0.0)
    eqs["E. D+volTarget20"] = _run_weighted(s(w_vt), qld_ret, sgov_ret)

    w_f = w_vt.where(term_ok, 0.0)
    eqs["F. E+VIX텀게이트"] = _run_weighted(s(w_f), qld_ret, sgov_ret)

    w_g = (TARGET_VOL / vol_tqqq).clip(upper=1.0).where(above & term_ok, 0.0)
    eqs["G. F의 TQQQ판"] = _run_weighted(s(w_g), tqqq_ret, sgov_ret)

    # H/I: 변동성 초과 시 SGOV가 아닌 QQQ로 강등 — 추세 위에서는 시장 노출을
    # 유지하되 레버리지 데케이만 차단. (3-state: QLD ↔ QQQ ↔ SGOV)
    def _three_state(w_lev: pd.Series, lev_ret: pd.Series) -> pd.Series:
        w_l = s(w_lev.where(above & term_ok, 0.0)).fillna(0).clip(0, 1)
        w_q = s(above.astype(float)).fillna(0) * (1 - w_l)          # 추세 위 잔여 → QQQ
        w_c = 1 - w_l - w_q
        turnover = (w_l.diff().abs() + w_q.diff().abs() + w_c.diff().abs()).fillna(1.0) / 2
        strat = (w_l * lev_ret + w_q * qqq_ret + w_c * sgov_ret).fillna(0) - turnover * COST
        return (1 + strat).cumprod()

    eqs["H. 추세내 QLD↔QQQ"] = _three_state((TARGET_VOL / vol_qld), qld_ret)
    eqs["I. H(타깃25%)"] = _three_state((0.25 / vol_qld), qld_ret)

    # J: I + 백워데이션 시 QQQ 잔여분까지 전량 SGOV 대피 (크래시 레짐 속도 보강 —
    # 200MA는 급락에 느리지만 VIX 텀 역전은 며칠 내 반응)
    def _core(risk_frac: pd.Series, lev_frac: pd.Series) -> pd.Series:
        """risk_frac: 위험자산(QLD+QQQ) 총 비중 0~1, lev_frac: 그중 QLD 비율 0~1."""
        w_l = (s(risk_frac) * s(lev_frac)).fillna(0).clip(0, 1)
        w_q = s(risk_frac).fillna(0).clip(0, 1) - w_l
        w_c = 1 - w_l - w_q
        turnover = (w_l.diff().abs() + w_q.diff().abs() + w_c.diff().abs()).fillna(1.0) / 2
        strat = (w_l * qld_ret + w_q * qqq_ret + w_c * sgov_ret).fillna(0) - turnover * COST
        return (1 + strat).cumprod()

    risk_on = above & term_ok
    lev_frac_vt = (0.25 / vol_qld).clip(upper=1.0)
    eqs["J. I+텀 전량대피"] = _core(risk_on.astype(float), lev_frac_vt)

    # K: J + 절대 모멘텀 게이트 (12개월 QQQ 수익 > 현금 수익 — Antonacci dual momentum)
    # 차입비용 인식: 고금리 환경에서 지수의 초과드리프트가 현금에 못 미치면 레버리지 무의미
    abs_mom = ((qqq / qqq.shift(252) - 1) > (sgov / sgov.shift(252) - 1)).fillna(False)
    eqs["K. J+절대모멘텀"] = _core((risk_on & abs_mom).astype(float), lev_frac_vt)

    # L: 단일 200MA 대신 룩백 앙상블 투표 (50/100/150/200/250) — 타이밍 럭 완화.
    # 위험자산 비중 = 투표 비율 (점진적 진입·청산), 백워데이션 시 전량 대피
    votes = sum((qqq > qqq.rolling(n, min_periods=n).mean()).astype(float)
                for n in (50, 100, 150, 200, 250)) / 5.0
    risk_L = votes.where(term_ok, 0.0)
    eqs["L. 앙상블추세 코어"] = _core(risk_L, lev_frac_vt)

    # M: L + Keller DAA 카나리아 (EEM·AGG 13612W 모멘텀) — 위기 선행 신호.
    # 카나리아 b개 음수 → 위험비중 ×(1 - b/2) (둘 다 음수면 전량 대피)
    def _13612w(p: pd.Series) -> pd.Series:
        return (12 * (p / p.shift(21) - 1) + 4 * (p / p.shift(63) - 1)
                + 2 * (p / p.shift(126) - 1) + (p / p.shift(252) - 1)) / 19
    eem, agg = px.get("EEM"), px.get("AGG")
    if eem is not None and agg is not None:
        bad = ((_13612w(eem.reindex(idx).ffill()) <= 0).astype(int)
               + (_13612w(agg.reindex(idx).ffill()) <= 0).astype(int))
        canary_mult = (1 - bad / 2.0).clip(0, 1)
        eqs["M. L+카나리아"] = _core(risk_L * canary_mult, lev_frac_vt)

    # N: L의 방어슬리브를 SGOV 50% + GLD 50%로 — 인플레형 약세장(2022) 대응
    gld = px.get("GLD")
    if gld is not None:
        gld_ret = gld.reindex(idx).ffill().pct_change()
        def_ret = 0.5 * sgov_ret + 0.5 * gld_ret
        w_l = (s(risk_L) * s(lev_frac_vt)).fillna(0).clip(0, 1)
        w_q = s(risk_L).fillna(0).clip(0, 1) - w_l
        w_c = 1 - w_l - w_q
        turnover = (w_l.diff().abs() + w_q.diff().abs() + w_c.diff().abs()).fillna(1.0) / 2
        strat = (w_l * qld_ret + w_q * qqq_ret + w_c * def_ret).fillna(0) - turnover * COST
        eqs["N. L+금방어슬리브"] = (1 + strat).cumprod()

    aux = pd.DataFrame({"above": above, "w_F": w_f}, index=idx)
    return eqs, aux


def report(eqs: dict[str, pd.Series], aux: pd.DataFrame) -> str:
    rows = [_metrics(eq, name) for name, eq in eqs.items()]
    lines = ["전략               CAGR     MDD    Sharpe  Calmar   배수"]
    for r in rows:
        lines.append(f"{r['name']:<18} {r['cagr']*100:+6.1f}% {r['mdd']*100:+6.1f}% "
                     f"{r['sharpe']:6.2f} {r['calmar']:6.2f}  {r['final']:5.2f}×")

    # 전·후반 분할
    any_eq = next(iter(eqs.values()))
    mid = any_eq.index[len(any_eq) // 2]
    lines.append("\n[전·후반 분할 CAGR/MDD]")
    for name, eq in eqs.items():
        h1, h2 = eq.loc[:mid], eq.loc[mid:]
        m1, m2 = _metrics(h1, name), _metrics(h2, name)
        lines.append(f"{name:<18} 전반 {m1['cagr']*100:+6.1f}%/{m1['mdd']*100:+5.1f}%  "
                     f"후반 {m2['cagr']*100:+6.1f}%/{m2['mdd']*100:+5.1f}%")

    # 연도별 (벤치마크 vs 최종 후보 N)
    cand = "N. L+금방어슬리브"
    lines.append(f"\n[연도별 수익 — QQQ vs {cand}]")
    a, f = eqs["A. QQQ B&H"], eqs[cand]
    for y, g in a.groupby(a.index.year):
        ra = g.iloc[-1] / g.iloc[0] - 1
        gf = f.loc[g.index]
        rf_ = gf.iloc[-1] / gf.iloc[0] - 1
        win = "✅" if rf_ > ra else "  "
        lines.append(f"  {y}:  QQQ {ra*100:+6.1f}%   {cand[0]} {rf_*100:+6.1f}%  {win}")

    # 최종 후보의 주요 낙폭 에피소드
    dd = f / f.cummax() - 1
    lines.append(f"\n[{cand} 낙폭 -15% 초과 에피소드]")
    in_dd, start = False, None
    for d, v in dd.items():
        if not in_dd and v < -0.15:
            in_dd, start = True, d
        elif in_dd and v > -0.02:
            seg = dd.loc[start:d]
            lines.append(f"  {start.date()} ~ {d.date()}  최저 {seg.min()*100:.1f}%")
            in_dd = False
    if in_dd:
        seg = dd.loc[start:]
        lines.append(f"  {start.date()} ~ 진행중  최저 {seg.min()*100:.1f}%")

    n_sw = int((aux["above"].astype(int).diff().abs() > 0).sum())
    tim = float((aux["w_F"] > 0).mean())
    lines.append(f"\n200MA 신호 전환 {n_sw}회 | F 시장노출 비율 {tim*100:.0f}%")

    # 고금리 시대 부분구간 (2022~) — 레버리지 차입비용이 실가격에 반영된 구간
    lines.append("\n[고금리 시대 2022-01~ 부분구간 — 차입비용 실반영 구간]")
    for name in ("A. QQQ B&H", "D. 200MA QLD/SGOV", "J. I+텀 전량대피",
                 "K. J+절대모멘텀", "L. 앙상블추세 코어", "M. L+카나리아", "N. L+금방어슬리브"):
        if name not in eqs:
            continue
        sub = eqs[name].loc["2022-01-01":]
        m = _metrics(sub, name)
        lines.append(f"{name:<18} CAGR {m['cagr']*100:+6.1f}%  MDD {m['mdd']*100:+6.1f}%  "
                     f"Sharpe {m['sharpe']:.2f}  Calmar {m['calmar']:.2f}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2520)
    args = ap.parse_args()
    px = _load(args.days)
    eqs, aux = build_strategies(px)
    print(report(eqs, aux))
    return 0


if __name__ == "__main__":
    sys.exit(main())
