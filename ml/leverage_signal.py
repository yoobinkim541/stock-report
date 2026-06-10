"""ml/leverage_signal.py — ML 기반 레버리지 ETF 진입 비중·타점 예측

목적: 현재 시황(낙폭/VIX/RSI/FG/MA) → 각 레버리지 ETF 권장 진입 비중
     조건부 기대수익·손익비·Kelly 비중 계산

공개 API:
    LeverageModel.train(dataset)          → 학습
    LeverageModel.predict(feats)          → EntrySignal
    get_entry_signal()                    → 현재 시황 EntrySignal (캐시 1h)
    format_leverage_report(signal)        → 텔레그램 발송 포맷
"""
from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_PATH = Path.home() / "reports" / "ml-cache" / "leverage_model.pkl"

INSTRUMENTS = ["SGOV", "QLD", "TQQQ", "SOXL", "UPRO"]
HORIZONS    = [21, 42, 63, 126]


# ── 결과 컨테이너 ─────────────────────────────────────────────────────────────

@dataclass
class InstrumentSignal:
    name:             str
    recommended_weight: float       # Kelly 기반 권장 비중 (0~1)
    expected_ret_30d: float         # 30일 기대수익 (중앙값)
    expected_ret_90d: float         # 90일 기대수익
    downside_p25_30d: float         # 30일 25th percentile (하방)
    hit_rate_30d:     float         # 30일 양수 수익 확률
    max_hist_dd:      float         # 과거 최악 낙폭
    risk_reward_30d:  float         # 기대수익 / |최악낙폭|
    ml_pred_30d:      float         # ML 예측 30일 수익 (있으면)


@dataclass
class EntrySignal:
    current_drawdown: float
    current_vix:      float
    current_rsi:      float
    fg_proxy:         float
    ma200_gap:        float
    bucket_label:     str
    n_similar:        int
    instruments:      dict[str, InstrumentSignal]
    total_weight:     float
    entry_advice:     str           # "분할진입 / 보류 / 적극진입"
    next_entry_levels: list[float]  # 추가 진입 타점 낙폭 목표
    stop_signal:      str           # 청산 조건
    timestamp:        str = ""


# ── Kelly 비중 계산 ───────────────────────────────────────────────────────────

def _kelly_weight(
    hit_rate: float,
    avg_win: float,
    avg_loss: float,
    fraction: float = 0.25,   # fractional Kelly (보수적)
) -> float:
    """Fractional Kelly criterion."""
    if avg_loss == 0 or hit_rate <= 0 or avg_win <= 0:
        return 0.0
    odds  = avg_win / abs(avg_loss)
    kelly = (hit_rate * odds - (1 - hit_rate)) / odds
    return max(0.0, min(kelly * fraction, 0.40))   # 0~40% 상한


# ── ML 모델 ───────────────────────────────────────────────────────────────────

class LeverageModel:
    """레버리지 ETF 듀얼 모델 (분류 + 회귀).

    각 (instrument, horizon)마다 두 개의 서브모델:
      clf: LGBMClassifier — 방향 예측 P(return > 0)  → AUC로 평가
      reg: LGBMRegressor  — 수익률 크기 예측          → Pearson corr

    Kelly 비중 계산 시 clf의 hit_rate를 사용 → 회귀 IC가 낮아도 안정적 비중 결정.
    """

    def __init__(self):
        self._clf: dict = {}   # {(instrument, horizon): LGBMClassifier}
        self._reg: dict = {}   # {(instrument, horizon): LGBMRegressor}
        self._feat_names: list[str] = []
        self._trained   = False

    def train(self, dataset: dict, train_frac: float = 0.7) -> dict:
        """데이터셋으로 분류 + 회귀 모델 동시 학습. Returns 성능 지표."""
        import lightgbm as lgb
        from sklearn.metrics import roc_auc_score

        features: pd.DataFrame = dataset["features"]
        targets                = dataset["targets"]

        if features.empty:
            logger.warning("피처 데이터 없음")
            return {}

        self._feat_names = list(features.columns)
        dates = features.index
        # Purged split: 분할일 직전 max(HORIZONS) 거래일(≈달력일 ×1.45)의 이벤트는
        # 학습 제외 — 선행 레이블이 test 구간을 내다보는 누수 방지.
        # (이벤트 기반 희소 인덱스이므로 행 수가 아닌 날짜 기준으로 purge)
        embargo_cal = int(max(HORIZONS) * 1.45)
        split_idx = int(len(dates) * train_frac)
        split     = dates[split_idx]
        purge_cut = split - pd.Timedelta(days=embargo_cal)
        train_mask = dates < purge_cut
        test_mask  = dates >= split

        X_tr = features[train_mask].values.astype(float)
        X_te = features[test_mask].values.astype(float)
        perf: dict = {}

        params_shared = dict(
            n_estimators=150, num_leaves=20, learning_rate=0.05,
            min_child_samples=8, subsample=0.8, colsample_bytree=0.8,
            reg_alpha=0.05, reg_lambda=0.05, random_state=42,
            verbose=-1, n_jobs=-1,
        )

        for name in INSTRUMENTS:
            for h in HORIZONS:
                target = targets.get(name, {}).get(h)
                if target is None or target.empty:
                    continue

                y_cont = target.reindex(features.index).values.astype(float)
                y_bin  = (y_cont > 0).astype(int)   # 방향 라벨

                train_idx = train_mask.values if hasattr(train_mask, "values") else train_mask
                test_idx  = test_mask.values  if hasattr(test_mask,  "values") else test_mask
                valid_tr = np.isfinite(X_tr).all(axis=1) & np.isfinite(y_cont[train_idx])
                valid_te = np.isfinite(X_te).all(axis=1) & np.isfinite(y_cont[test_idx])
                if valid_tr.sum() < 20:
                    continue

                # ── 분류: P(return > 0) ──
                clf = lgb.LGBMClassifier(objective="binary", **params_shared)
                clf.fit(X_tr[valid_tr], y_bin[train_idx][valid_tr],
                        feature_name=self._feat_names)
                self._clf[(name, h)] = clf

                # ── 회귀: 수익률 크기 ──
                reg = lgb.LGBMRegressor(objective="regression", **params_shared)
                reg.fit(X_tr[valid_tr], y_cont[train_idx][valid_tr],
                        feature_name=self._feat_names)
                self._reg[(name, h)] = reg

                # 성능 평가
                if valid_te.sum() > 5:
                    y_te_cont = y_cont[test_idx][valid_te]
                    y_te_bin  = y_bin[test_idx][valid_te]
                    prob_te   = clf.predict_proba(X_te[valid_te])[:, 1]
                    pred_te   = reg.predict(X_te[valid_te])

                    try:
                        auc  = float(roc_auc_score(y_te_bin, prob_te))
                    except Exception:
                        auc = 0.5
                    corr = float(np.corrcoef(pred_te, y_te_cont)[0, 1]) if len(pred_te) > 2 else 0.0
                    perf[f"{name}_{h}d"] = {"auc": round(auc, 3), "corr": round(corr, 3)}

        self._trained = True
        logger.info(
            "LeverageModel 학습 완료: clf=%d reg=%d 서브모델",
            len(self._clf), len(self._reg),
        )
        return perf

    def predict_proba(self, feats: dict) -> dict[str, dict[int, float]]:
        """현재 피처 → 각 종목의 horizon별 P(양수 수익) 예측."""
        if not self._trained or not self._clf:
            return {}
        x = np.array([[feats.get(f, 0.0) for f in self._feat_names]])
        out: dict[str, dict[int, float]] = {}
        for (name, h), clf in self._clf.items():
            out.setdefault(name, {})
            try:
                out[name][h] = float(clf.predict_proba(x)[0, 1])
            except Exception:
                out[name][h] = 0.5
        return out

    def predict_returns(self, feats: dict) -> dict[str, dict[int, float]]:
        """현재 피처 → 각 종목의 horizon별 예측 수익률 (회귀 모델)."""
        if not self._trained or not self._reg:
            return {}
        x = np.array([[feats.get(f, 0.0) for f in self._feat_names]])
        out: dict[str, dict[int, float]] = {}
        for (name, h), reg in self._reg.items():
            out.setdefault(name, {})
            try:
                out[name][h] = float(reg.predict(x)[0])
            except Exception:
                pass
        return out

    def save(self, path: Path = MODEL_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(self))
        logger.info("LeverageModel 저장: %s", path)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> Optional["LeverageModel"]:
        if not path.exists():
            return None
        try:
            return pickle.loads(path.read_bytes())
        except Exception as e:
            logger.warning("LeverageModel 로드 실패: %s", e)
            return None


# ── 신호 생성 ─────────────────────────────────────────────────────────────────

def _entry_advice(drawdown: float, vix: float, rsi: float, fg: float) -> str:
    """시황 기반 정성 조언."""
    score = 0
    if drawdown < -0.20:  score += 3
    elif drawdown < -0.10: score += 2
    elif drawdown < -0.05: score += 1
    if vix > 30:    score += 2
    elif vix > 20:  score += 1
    if rsi < 30:    score += 2
    elif rsi < 40:  score += 1
    if fg < 25:     score += 2
    elif fg < 40:   score += 1

    if score >= 6:   return "🔥 적극 진입 — 역사적 매수 기회 구간"
    if score >= 3:   return "⚡ 분할 진입 — 조건 양호, 단계적 매수"
    if score >= 1:   return "⏳ 소량 진입 — 추가 낙폭 대기 권장"
    return "🛑 진입 보류 — 현재 고평가 구간"


def _next_entry_levels(current_dd: float) -> list[float]:
    """다음 진입 타점 낙폭 목록."""
    levels = [-0.05, -0.10, -0.15, -0.20, -0.25, -0.30, -0.35, -0.40]
    return [l for l in levels if l < current_dd - 0.02][:4]


def build_entry_signal(context: dict, model: Optional[LeverageModel] = None) -> EntrySignal:
    """context 딕셔너리 → EntrySignal."""
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))

    cur_dd    = context.get("current_drawdown", 0.0)
    cur_feats = context.get("current_feats", {})
    stats     = context.get("current_stats", {})
    bucket    = context.get("current_bucket", (-0.05, 0.0))
    n_similar = context.get("n_similar", 0)

    vix_v = cur_feats.get("vix", np.nan)
    rsi_v = cur_feats.get("rsi", np.nan)
    fg_v  = cur_feats.get("fg_proxy", 50.0)

    # ML 예측: 분류(hit_rate) + 회귀(수익률 크기)
    ml_proba = model.predict_proba(cur_feats) if model and model._trained else {}
    ml_preds = model.predict_returns(cur_feats) if model and model._trained else {}

    instruments_out: dict[str, InstrumentSignal] = {}
    weights_raw: dict[str, float] = {}

    for name in INSTRUMENTS:
        st = stats.get(name)

        if st:
            er30  = st.median_ret.get(21, np.nan)
            er90  = st.median_ret.get(63, np.nan)
            p25   = st.p25_ret.get(21, np.nan)
            hr30  = st.hit_rate.get(21, np.nan)
            mdd   = st.max_drawdown
            rr    = abs(er30 / mdd) if mdd < 0 and np.isfinite(er30) else np.nan
        else:
            er30 = er90 = p25 = hr30 = np.nan
            mdd  = 0.0
            rr   = np.nan

        # 분류 모델 hit_rate로 역사적 hit_rate 보정 (신뢰도 가중 블렌딩)
        ml_hr30 = ml_proba.get(name, {}).get(21)
        if ml_hr30 is not None and np.isfinite(hr30):
            hr30 = 0.5 * hr30 + 0.5 * ml_hr30   # 역사적 + ML 평균

        ml_pred = ml_preds.get(name, {}).get(21, np.nan) if ml_preds else np.nan

        # Kelly 비중: SGOV는 잔여비중 처리
        if name == "SGOV":
            kw = 0.0   # 나중에 1 - sum(others) 로 설정
        else:
            avg_win  = max(er30, 0) if np.isfinite(er30) else 0.01
            avg_loss = abs(min(p25, 0)) if np.isfinite(p25) and p25 < 0 else 0.05
            kw = _kelly_weight(hr30 if np.isfinite(hr30) else 0.5, avg_win, avg_loss)

        weights_raw[name] = kw

        instruments_out[name] = InstrumentSignal(
            name              = name,
            recommended_weight = kw,
            expected_ret_30d  = er30 if np.isfinite(er30) else 0.0,
            expected_ret_90d  = er90 if np.isfinite(er90) else 0.0,
            downside_p25_30d  = p25 if np.isfinite(p25) else 0.0,
            hit_rate_30d      = hr30 if np.isfinite(hr30) else 0.5,
            max_hist_dd       = mdd,
            risk_reward_30d   = rr if np.isfinite(rr) else 0.0,
            ml_pred_30d       = ml_pred if np.isfinite(ml_pred) else 0.0,
        )

    # SGOV = 잔여 비중
    others_sum  = sum(w for n, w in weights_raw.items() if n != "SGOV")
    sgov_weight = max(0.0, 1.0 - others_sum)
    instruments_out["SGOV"].recommended_weight = sgov_weight
    weights_raw["SGOV"] = sgov_weight

    bucket_label = f"{int(bucket[0]*100)}%~{int(bucket[1]*100)}%"

    # 추세 게이트: 기초지수가 게이트 MA 아래면 신규 진입 보류/축소로 강등
    advice    = _entry_advice(cur_dd, vix_v or 20, rsi_v or 50, fg_v)
    ma200_gap = cur_feats.get("ma200_gap", 0.0)
    trend_ma  = context.get("opt_trend_ma", 0)
    below_scale = context.get("opt_below_trend_scale", 0.0)
    if trend_ma and ma200_gap < 0 and "보류" not in advice:
        if below_scale > 0.05:
            advice = f"⚠️ 추세 약세 — QQQ 200MA {ma200_gap*100:+.1f}% 아래, 비중 ×{below_scale:.1f} 축소 진입 ({advice.split(' — ')[0].strip()})"
        else:
            advice = f"🛑 추세 게이트 — QQQ가 200MA {ma200_gap*100:+.1f}% 아래, 신규 진입 보류 ({advice.split(' — ')[0].strip()} 조건이나 게이트 우선)"

    # VIX 백워데이션: 단기 변동성이 중기보다 높음 = 시장이 폭풍을 가격에 반영 중
    vix_term  = cur_feats.get("vix_term", float("nan"))
    min_term  = context.get("opt_min_vix_term", 0.0)
    if min_term and np.isfinite(vix_term) and vix_term < min_term and "보류" not in advice:
        advice = f"🛑 VIX 백워데이션 (텀 {vix_term:.2f} < {min_term:.2f}) — 신규 진입 보류 ({advice.split(' — ')[0].strip()})"

    return EntrySignal(
        current_drawdown   = cur_dd,
        current_vix        = vix_v if np.isfinite(vix_v) else 0.0,
        current_rsi        = rsi_v if np.isfinite(rsi_v) else 50.0,
        fg_proxy           = fg_v,
        ma200_gap          = cur_feats.get("ma200_gap", 0.0),
        bucket_label       = bucket_label,
        n_similar          = n_similar,
        instruments        = instruments_out,
        total_weight       = sum(weights_raw.values()),
        entry_advice       = advice,
        next_entry_levels  = _next_entry_levels(cur_dd),
        stop_signal        = "QQQ -5% 추가 하락 or VIX > 40 → 포지션 축소 / QQQ ATH 5% 이내 회복 → 단계적 청산",
        timestamp          = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
    )


def get_optimized_params() -> dict | None:
    """leverage_optimizer 최적 파라미터 로드."""
    try:
        from ml.leverage_optimizer import load_result
        r = load_result()
        return r.get("best_params") if r else None
    except Exception:
        return None


def get_entry_signal(retrain: bool = False) -> EntrySignal:
    """현재 시황 기반 레버리지 진입 신호.

    Optimizer best_params가 저장되어 있으면 진입 임계값에 반영.
    """
    import warnings; warnings.filterwarnings("ignore")

    from ml.leverage_backtester import get_current_entry_context
    context = get_current_entry_context(days=2520)

    # Optimizer 최적 파라미터 → context에 진입 임계 주입
    opt = get_optimized_params()
    if opt:
        context["opt_min_dd"]        = opt.get("min_dd",        -0.10)
        context["opt_max_vix_entry"] = opt.get("max_vix_entry",  35.0)
        context["opt_min_rsi_entry"] = opt.get("min_rsi_entry",  40.0)
        context["opt_lev_weight"]    = opt.get("lev_weight",      0.25)
        context["opt_trailing_stop"] = opt.get("trailing_stop",  -0.10)
        context["opt_trend_ma"]      = int(opt.get("trend_ma", 0) or 0)
        context["opt_below_trend_scale"] = float(opt.get("below_trend_scale", 0.0) or 0.0)
        context["opt_min_vix_term"]  = float(opt.get("min_vix_term", 0.0) or 0.0)

        # vol targeting: 실현변동성이 목표 초과 시 권장 비중 축소
        target_vol = opt.get("target_vol")
        real_vol   = context.get("current_feats", {}).get("real_vol_20d", np.nan)
        if target_vol and np.isfinite(real_vol) and real_vol > 1e-6:
            vol_scale = min(1.0, target_vol / real_vol)
            context["opt_lev_weight"] = context["opt_lev_weight"] * vol_scale
            context["vol_scale"]      = vol_scale
        logger.info(
            "Optimizer 파라미터 적용 — min_dd=%.1f%%  max_vix=%.0f  lev_w=%.0f%%  vol_scale=%.2f  trend_ma=%d",
            opt.get("min_dd", -0.10) * 100,
            opt.get("max_vix_entry", 35.0),
            context["opt_lev_weight"] * 100,
            context.get("vol_scale", 1.0),
            context["opt_trend_ma"],
        )

    model = None if retrain else LeverageModel.load()
    if model is None or not model._trained:
        logger.info("LeverageModel 학습 시작...")
        model = LeverageModel()
        perf  = model.train(context)
        logger.info("학습 완료: %s", perf)
        model.save()

    return build_entry_signal(context, model)


# ── 텔레그램 포맷 ─────────────────────────────────────────────────────────────

def format_leverage_report(sig: EntrySignal) -> str:
    """텔레그램 발송용 레버리지 분석 리포트."""
    lines = [
        "📊 레버리지 ETF 진입 분석",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        f"현재 QQQ 낙폭: {sig.current_drawdown*100:+.1f}%  ({sig.bucket_label} 구간)",
        f"VIX: {sig.current_vix:.1f}  RSI: {sig.current_rsi:.1f}  F&G Proxy: {sig.fg_proxy:.0f}",
        f"200MA 위치: {sig.ma200_gap*100:+.1f}%  유사 과거: {sig.n_similar}건",
        "",
        f"{sig.entry_advice}",
        "",
        "[ 수익 분포 — 30일 중앙값 / 90일 중앙값 / 손익비 ]",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for name in INSTRUMENTS:
        inst = sig.instruments.get(name)
        if not inst:
            continue
        bar = "█" * int(inst.recommended_weight * 20)
        rr_str = f"{inst.risk_reward_30d:.2f}" if inst.risk_reward_30d else "—"
        ml_str = (f"  ML:{inst.ml_pred_30d*100:+.1f}%" if inst.ml_pred_30d else "")
        lines.append(
            f"  {name:<6}  "
            f"30d:{inst.expected_ret_30d*100:+5.1f}%  "
            f"90d:{inst.expected_ret_90d*100:+5.1f}%  "
            f"손익비:{rr_str}{ml_str}"
        )
        lines.append(
            f"  {'':6}  히트율:{inst.hit_rate_30d*100:.0f}%  "
            f"MDD:{inst.max_hist_dd*100:+.0f}%  "
            f"P25:{inst.downside_p25_30d*100:+.1f}%"
        )

    lines += [
        "",
        "[ 권장 진입 비중 ]",
        "━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for name in INSTRUMENTS:
        inst = sig.instruments.get(name)
        if not inst:
            continue
        pct = inst.recommended_weight * 100
        bar = "█" * max(1, int(pct / 5))
        lines.append(f"  {name:<6}  {pct:5.1f}%  {bar}")

    lines += [
        "",
        "[ 다음 추가 진입 타점 ]",
    ]
    if sig.next_entry_levels:
        for lvl in sig.next_entry_levels:
            lines.append(f"  QQQ {lvl*100:.0f}% → 레버리지 비중 단계적 확대")
    else:
        lines.append("  현재 낙폭이 이미 충분 — 타점 없음")

    lines += [
        "",
        f"🚨 청산 조건: {sig.stop_signal}",
    ]

    # 최적화 결과 섹션 — 종목별 독립 파라미터 표시
    try:
        from ml.leverage_optimizer import load_result, INSTRUMENT_UNDERLYING
        opt = load_result()
        if opt:
            per = opt.get("per_instrument", {})
            # per_instrument 있으면 종목별 표시, 없으면 단일 결과 표시
            show_items = list(per.items()) if per else [(opt.get("best_params", {}).get("instrument","?"), opt)]

            lines += ["", "[ 🏆 Optuna 스위트스팟 — 종목별 최적 파라미터 ]"]
            for inst, d in show_items:
                p   = d.get("best_params", {})
                und = INSTRUMENT_UNDERLYING.get(inst, "QQQ")
                wf_pos = sum(1 for c in d.get("wf_fold_calmars",[]) if c > 0)
                wf_tot = len([c for c in d.get("wf_fold_calmars",[]) if c != 0.0])
                medal  = "🥇" if inst == opt.get("best_params",{}).get("instrument") else "  "
                lines += [
                    f"  {medal} {inst} (기초: {und}) ─────────────",
                    f"     진입낙폭 {und} ≤{p.get('min_dd',0)*100:.1f}%  "
                    f"VIX ≤{p.get('max_vix_entry',0):.0f}  "
                    f"RSI ≤{p.get('min_rsi_entry',0):.0f}",
                    f"     레버 {p.get('lev_weight',0)*100:.0f}%  "
                    f"SGOV최소 {p.get('sgov_floor',0)*100:.0f}%  "
                    f"청산MA {p.get('exit_ma',0)}일  "
                    f"스탑 {p.get('trailing_stop',0)*100:.1f}%",
                    f"     IS: CAGR {d.get('best_cagr',0)*100:+.1f}%  "
                    f"Sharpe {d.get('best_sharpe',0):.2f}  "
                    f"MDD {d.get('best_max_dd',0)*100:.1f}%",
                    f"     WF: Calmar {d.get('wf_mean_calmar',0):.2f}±{d.get('wf_std_calmar',0):.1f}  "
                    f"양수폴드 {wf_pos}/{wf_tot}",
                ]
            lines.append(f"  최적화: {opt.get('optimized_at','')}")
    except Exception:
        pass

    lines += [
        "",
        f"⚠️ 레버리지는 변동성 붕괴(decay) 위험 있음",
        f"⚠️ 과거 분포 기반 — 미래 수익 보장 없음",
        f"({sig.timestamp})",
    ]
    return "\n".join(lines)
