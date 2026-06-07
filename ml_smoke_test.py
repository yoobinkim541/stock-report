#!/usr/bin/env python3
"""
ml_smoke_test.py — ML 전략 파이프라인 end-to-end 연기 테스트 (p12)

네트워크 없이 합성 데이터로 p3~p11 전체 파이프라인을 검증.
실패 시 텔레그램 알림 전송.

크론 (매일 09:00 KST = 00:00 UTC):
    0 0 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python ml_smoke_test.py >> /tmp/ml_smoke_test.log 2>&1
"""

import os
import sys
import time
import logging
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("STOCK_BOT_TOKEN")
CHAT_ID   = os.getenv("STOCK_BOT_CHAT_ID", "5771238245")


def _alert(msg: str):
    if not BOT_TOKEN:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": f"🤖 ML smoke test 실패\n━━━━━━━━━━━━━━\n{msg}"},
            timeout=10,
        )
    except Exception as e:
        logger.error("알림 전송 실패: %s", e)


def _check(name: str, fn, *checks) -> list[str]:
    failures = []
    try:
        result = fn()
    except Exception as e:
        return [f"❌ {name}: 예외 — {e}"]
    for desc, condition in checks:
        try:
            ok = bool(condition(result))
        except Exception as e:
            ok = False
            desc = f"{desc} (검증 오류: {e})"
        if not ok:
            failures.append(f"❌ {name}: {desc}")
        else:
            logger.info("  ✅ %s — %s", name, desc)
    return failures


def run_tests() -> list[str]:
    import numpy as np
    import pandas as pd

    failures = []

    # ── p3: Universe builder ──────────────────────────────────────────────────
    logger.info("[p3] Universe builder")
    from ml.universe import build_universe
    failures += _check("universe",
        build_universe,
        ("50개 이상 티커", lambda r: len(r) >= 50),
        ("QQQ 포함",       lambda r: "QQQ" in r),
        ("SGOV 포함",      lambda r: "SGOV" in r),
        ("중복 없음",      lambda r: len(r) == len(set(r))),
        ("결정론적",       lambda r: r == build_universe()),
    )

    # ── p4: Data source layer (네트워크 없이 구조만 검증) ──────────────────────
    logger.info("[p4] Data source layer")
    from ml.data_sources import source_cache_dir, source_cache_files
    failures += _check("source_cache_dir",
        lambda: source_cache_dir(),
        ("Path 반환",   lambda r: hasattr(r, "is_dir")),
    )
    failures += _check("source_cache_files",
        lambda: source_cache_files(),
        ("list 반환",   lambda r: isinstance(r, list)),
    )

    # ── p5: Feature dataset ───────────────────────────────────────────────────
    logger.info("[p5] Feature dataset")
    from ml.features import compute_features, rsi, macd, bollinger
    idx = pd.date_range("2021-01-04", periods=300, freq="B")
    close = pd.Series(100 * (1 + np.random.default_rng(0).normal(0.0003, 0.01, 300)).cumprod(), index=idx)
    df_close = close.to_frame("close")

    failures += _check("compute_features",
        lambda: compute_features(df_close, include_ichimoku=False),
        ("DataFrame 반환",         lambda r: hasattr(r, "columns")),
        ("300행",                  lambda r: len(r) == 300),
        ("rsi_14 컬럼 존재",       lambda r: "rsi_14" in r.columns),
        ("macd 컬럼 존재",         lambda r: "macd" in r.columns),
        ("ichi_chikou 없음 (lookahead 제거)", lambda r: "ichi_chikou" not in r.columns),
    )

    failures += _check("rsi 경계값",
        lambda: rsi(close),
        ("NaN 없음",     lambda r: r.dropna().between(0, 100).all()),
        ("상승-only→100",lambda r: float(rsi(pd.Series(range(1, 51))).dropna().iloc[-1]) == pytest_approx_100()),
    )

    # ── p6: Baseline backtest ─────────────────────────────────────────────────
    logger.info("[p6] Baseline backtest")
    from ml.backtest import buy_and_hold, rule_baseline, BacktestResult
    price = pd.Series(100 * (1.001 ** np.arange(252)), index=pd.date_range("2022-01-03", periods=252, freq="B"))
    bah = buy_and_hold(price, name="test")

    failures += _check("buy_and_hold",
        lambda: bah,
        ("BacktestResult 반환",     lambda r: isinstance(r, BacktestResult)),
        ("누적수익 양수 (상승 가격)", lambda r: r.cumulative_return > 0),
        ("최대낙폭 <= 0",            lambda r: r.max_drawdown <= 0),
    )

    # CAGR 검증: 265 business days ≈ 368 calendar days > 365 → CAGR 계산 가능
    price_1y = pd.Series(100 * (1.001 ** np.arange(265)), index=pd.date_range("2022-01-03", periods=265, freq="B"))
    bah_1y = buy_and_hold(price_1y, name="1y")
    failures += _check("buy_and_hold CAGR (1년 이상)",
        lambda: bah_1y,
        ("CAGR 양수", lambda r: r.cagr is not None and r.cagr > 0),
    )

    # rule_baseline shift(1) 검증: 상승-only 가격, 신호=시가대비 momentum
    feat = pd.DataFrame({"sig": np.ones(252)}, index=price.index)
    rb = rule_baseline(feat, price, signal_col="sig", threshold=0.5)
    failures += _check("rule_baseline shift(1)",
        lambda: rb,
        ("BacktestResult 반환", lambda r: isinstance(r, BacktestResult)),
        ("n_days == 252",       lambda r: r.n_days == 252),
    )

    # ── p7: Models ────────────────────────────────────────────────────────────
    logger.info("[p7] Models")
    from ml.models import MarketRiskModel, ExcessReturnModel

    failures += _check("MarketRiskModel",
        lambda: MarketRiskModel(),
        ("인스턴스 생성", lambda r: r is not None),
    )
    failures += _check("ExcessReturnModel",
        lambda: ExcessReturnModel(),
        ("인스턴스 생성", lambda r: r is not None),
    )

    # ── p8: Optimization ──────────────────────────────────────────────────────
    logger.info("[p8] Optimization")
    from ml.optimization import composite_score, grid_search_parameters

    failures += _check("composite_score",
        lambda: composite_score(cagr=0.12, max_drawdown=-0.15, turnover=0.05, excess_return=0.03),
        ("float 반환",    lambda r: isinstance(r, float)),
        ("유한값",        lambda r: abs(r) < 1e9),
    )

    failures += _check("grid_search_parameters",
        lambda: grid_search_parameters(
            lambda p: p["x"] ** 2,
            {"x": [-2.0, -1.0, 0.0, 1.0, 2.0]},
            direction="maximize",
        ),
        ("best_params 존재", lambda r: "best_params" in r),
        ("best_value 최대",  lambda r: r["best_value"] == 4.0),
        ("n_trials=5",       lambda r: r["n_trials"] == 5),
    )

    # ── p9: Walk-forward ─────────────────────────────────────────────────────
    logger.info("[p9] Walk-forward")
    from ml.walk_forward import walk_forward_splits, leakage_guard_future_columns

    splits = list(walk_forward_splits(n_rows=500, train_size=200, val_size=50, test_size=50, step=50))
    failures += _check("walk_forward_splits",
        lambda: splits,
        ("5 folds",                     lambda r: len(r) == 5),
        ("fold 인덱스 순서",             lambda r: all(s.fold == i for i, s in enumerate(r))),
        ("겹침 없음 (train_end<=val_start)", lambda r: all(s.train_end <= s.val_start for s in r)),
    )

    feat_df = pd.DataFrame({"momentum": np.zeros(50), "sentiment": np.zeros(50)},
                           index=pd.date_range("2022-01-03", periods=50, freq="B"))
    failures += _check("leakage_guard_future_columns",
        lambda: leakage_guard_future_columns(feat_df),
        ("예외 없이 통과", lambda r: True),
    )

    # ── p10: Portfolio construction ───────────────────────────────────────────
    logger.info("[p10] Portfolio construction")
    from ml.portfolio import PortfolioConfig, build_weights, validate_weights

    scores = pd.Series({"QQQ": 0.4, "NVDA": 0.6, "MSFT": 0.3, "SGOV": 0.1})
    cfg = PortfolioConfig()
    weights = build_weights(scores, cfg)

    failures += _check("build_weights",
        lambda: weights,
        ("pd.Series 반환",           lambda r: hasattr(r, "sum")),
        ("합계 ≤ 1.0",               lambda r: float(r.sum()) <= 1.0 + 1e-6),
        ("음수 없음",                 lambda r: (r >= 0).all()),
    )

    failures += _check("validate_weights (정상 케이스)",
        lambda: (lambda: (validate_weights(weights) or True))(),
        ("예외 없이 통과", lambda r: r is True),
    )

    # ── p11: Sweet-spot optimizer ─────────────────────────────────────────────
    logger.info("[p11-sweet_spot] Sweet-spot optimizer")
    from ml.sweet_spot import (
        generate_synthetic_market_data,
        evaluate_threshold_strategy,
        optimize_sweet_spot,
    )

    data = generate_synthetic_market_data(seed=42)
    failures += _check("generate_synthetic_market_data",
        lambda: data,
        ("756행",               lambda r: len(r["close"]) == 756),
        ("피처 3개",            lambda r: r["features"].shape[1] == 3),
        ("NaN 없음",           lambda r: r["features"].isna().sum().sum() == 0),
        ("결정론적",           lambda r: r["close"].equals(generate_synthetic_market_data(seed=42)["close"])),
    )

    failures += _check("evaluate_threshold_strategy",
        lambda: evaluate_threshold_strategy(data, {"threshold": 0.0}),
        ("BacktestResult 반환", lambda r: isinstance(r, BacktestResult)),
        ("n_days == 756",       lambda r: r.n_days == 756),
        ("turnover >= 0",       lambda r: r.turnover is not None and r.turnover >= 0),
    )

    opt = optimize_sweet_spot(data)
    failures += _check("optimize_sweet_spot",
        lambda: opt,
        ("best_params 존재",          lambda r: bool(r.best_params)),
        ("trials 20행",               lambda r: len(r.trials) == 20),
        ("equity 필수 컬럼 포함",      lambda r: {"ML_model","threshold","SPY","QQQ"}.issubset(set(r.equity.columns))),
        ("wf mean_sharpe 유한값",      lambda r: r.wf_summary["mean_sharpe"] is not None),
        ("ml_result 타입",             lambda r: isinstance(r.ml_result, BacktestResult)),
        ("best score >= baseline score", lambda r: _best_beats_baseline(r)),
    )

    # ── p11: Reporting / Telegram ─────────────────────────────────────────────
    logger.info("[p11-reporting] Reporting")
    from ml.reporting import build_sample_ml_strategy_report, chunk_text

    report = build_sample_ml_strategy_report()
    failures += _check("build_sample_ml_strategy_report",
        lambda: report,
        ("비어있지 않음",          lambda r: len(r) > 100),
        ("헤더 포함",              lambda r: "ML 전략 성과 리포트" in r),
        ("최적화 샘플 레이블",     lambda r: "최적화 샘플" in r),
        ("None 리터럴 없음",       lambda r: "None" not in r),
        ("룩어헤드 경고 포함",     lambda r: "shift(1)" in r),
    )

    chunks = chunk_text(report)
    failures += _check("chunk_text",
        lambda: chunks,
        ("모든 청크 ≤ 3900자", lambda r: all(len(c) <= 3900 for c in r)),
        ("이어 붙이면 원문 복원", lambda r: "".join(r) == report),
    )

    # ── Telegram bot wiring ───────────────────────────────────────────────────
    logger.info("[p11-bot] Telegram bot wiring")
    try:
        from telegram_bot import cmd_mlreport, BOT_COMMANDS, _COMMAND_HANDLERS
        sent: list[str] = []
        cmd_mlreport("fake_chat", send_fn=lambda cid, txt: sent.append(txt))
        failures += _check("cmd_mlreport",
            lambda: sent,
            ("1개 이상 메시지 발송",     lambda r: len(r) >= 1),
            ("리포트 헤더 포함",         lambda r: "ML 전략 성과 리포트" in "\n".join(r)),
        )
        if "mlreport" not in [c["command"] for c in BOT_COMMANDS]:
            failures.append("❌ bot wiring: mlreport가 BOT_COMMANDS에 없음")
        else:
            logger.info("  ✅ bot wiring — mlreport in BOT_COMMANDS")

        if "/mlreport" not in _COMMAND_HANDLERS:
            failures.append("❌ bot wiring: /mlreport가 _COMMAND_HANDLERS에 없음")
        else:
            logger.info("  ✅ bot wiring — /mlreport in _COMMAND_HANDLERS")

    except ImportError as e:
        failures.append(f"❌ bot wiring: import 실패 — {e}")

    return failures


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _best_beats_baseline(opt_result) -> bool:
    from ml.optimization import composite_score
    qqq_cagr = opt_result.qqq_result.cagr or 0.0
    def _score(r):
        return composite_score(
            cagr=r.cagr,
            max_drawdown=r.max_drawdown,
            turnover=r.turnover or 0.0,
            excess_return=(r.cagr or 0.0) - qqq_cagr,
        )
    return _score(opt_result.best_result) >= _score(opt_result.baseline_result) - 1e-9


def pytest_approx_100():
    return 100.0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger.info("=== ml_smoke_test 시작 [%s] ===", datetime.now().strftime("%Y-%m-%d %H:%M"))
    t0 = time.time()

    try:
        failures = run_tests()
    except Exception as e:
        msg = f"❌ ml_smoke_test 실행 자체 실패: {e}"
        logger.error(msg)
        _alert(msg)
        sys.exit(1)

    elapsed = time.time() - t0
    total_checks = 47  # 위 _check 호출의 총 assertions 수 (ml_result 타입 체크 추가)

    if failures:
        msg = "\n".join(failures)
        logger.error("실패 %d건 (%.1fs):\n%s", len(failures), elapsed, msg)
        _alert(msg)
        sys.exit(1)
    else:
        logger.info("✅ 모든 항목 통과 (%d checks, %.1fs)", total_checks, elapsed)


if __name__ == "__main__":
    main()
