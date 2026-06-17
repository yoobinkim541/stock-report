#!/usr/bin/env python3
"""
institutional_flow_smoke_test.py — 기관 매집 추적(reports/institutional_flow.py) 무네트워크 연기 테스트

합성 OHLCV(매집·분산·현금성 패턴)로 compute_accumulation / rank_accumulation /
표기 헬퍼를 전부 네트워크 없이 검증한다. yfinance·실데이터 호출 금지:
rank_accumulation 은 enrich=False + price_fetcher 주입으로 13F·가격 다운로드를 모두 건너뛴다.
실패 시에만 텔레그램 알림 전송. exit code 0(통과) / 1(실패).

크론 (평일 09:00 KST = 00:00 UTC, ml_smoke_test 와 함께):
    0 0 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python tests/institutional_flow_smoke_test.py >> /tmp/institutional_flow_smoke_test.log 2>&1
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
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
            json={"chat_id": CHAT_ID, "text": f"🤖 기관매집 smoke test 실패\n━━━━━━━━━━━━━━\n{msg}"},
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


# ---------------------------------------------------------------------------
# 합성 OHLCV 생성 (네트워크 없이 결정론적)
# ---------------------------------------------------------------------------

def _idx(n: int):
    import pandas as pd
    return pd.date_range("2024-01-01", periods=n, freq="B")


def make_accumulation(ticker_n: int = 80, seed: int = 1):
    """매집 패턴: 상승추세 + 상승일 거래량 多 + 종가 일중 고가 근처 → 점수 높음."""
    import numpy as np, pandas as pd
    rng = np.random.default_rng(seed)
    idx = _idx(ticker_n)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.004, 0.012, ticker_n)), index=idx)
    chg = close.diff().fillna(0.0)
    # 상승일에 거래량 8~12M, 하락일에 1~3M
    vol = pd.Series(np.where(chg.values >= 0, rng.uniform(8e6, 12e6, ticker_n),
                             rng.uniform(1e6, 3e6, ticker_n)), index=idx)
    span = close * 0.015
    high = close + span * 0.2     # 종가가 고가 근처
    low = close - span * 0.8
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol})


def make_distribution(ticker_n: int = 80, seed: int = 2):
    """분산 패턴: 하락추세 + 하락일 거래량 多 + 종가 일중 저가 근처 → 점수 낮음."""
    import numpy as np, pandas as pd
    rng = np.random.default_rng(seed)
    idx = _idx(ticker_n)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(-0.004, 0.012, ticker_n)), index=idx)
    chg = close.diff().fillna(0.0)
    vol = pd.Series(np.where(chg.values < 0, rng.uniform(8e6, 12e6, ticker_n),
                             rng.uniform(1e6, 3e6, ticker_n)), index=idx)
    span = close * 0.015
    high = close + span * 0.8
    low = close - span * 0.2      # 종가가 저가 근처
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol})


def make_neutral(ticker_n: int = 80, seed: int = 5):
    """중립 패턴: 완만한 상승 + 균등 거래량 → 점수 중간대(min_score 필터 검증용)."""
    import numpy as np, pandas as pd
    rng = np.random.default_rng(seed)
    idx = _idx(ticker_n)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.0008, 0.012, ticker_n)), index=idx)
    vol = pd.Series(rng.uniform(4e6, 6e6, ticker_n), index=idx)
    span = close * 0.015
    return pd.DataFrame({"Open": close, "High": close + span * 0.5,
                         "Low": close - span * 0.5, "Close": close, "Volume": vol})


def make_cash(ticker_n: int = 80, seed: int = 3):
    """현금성 패턴: 거의 변동 없는 가격(연변동성<3%) → compute_accumulation None 반환."""
    import numpy as np, pandas as pd
    rng = np.random.default_rng(seed)
    idx = _idx(ticker_n)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0.00005, 0.0005, ticker_n)), index=idx)
    vol = pd.Series(rng.uniform(1e6, 2e6, ticker_n), index=idx)
    return pd.DataFrame({"Open": close, "High": close * 1.0005,
                         "Low": close * 0.9995, "Close": close, "Volume": vol})


def run_tests() -> list[str]:
    import pandas as pd
    from reports.institutional_flow import (
        compute_accumulation, rank_accumulation, fetch_13f,
        accumulation_line, accumulation_mobile_block, clean_entry,
        VERDICT_ACCUM, MIN_VOL_ANNUAL,
    )

    failures = []

    # ── compute_accumulation: 매집 패턴 ───────────────────────────────────────
    logger.info("[1] compute_accumulation — 매집 패턴")
    failures += _check("accumulation 패턴",
        lambda: compute_accumulation("ACC", make_accumulation()),
        ("dict 반환",            lambda r: isinstance(r, dict)),
        ("accum_score >= 60",    lambda r: r["accum_score"] >= 60),
        ("verdict 매집계열",      lambda r: r["verdict"] in ("강한 매집", "매집")),
        ("accum_score 0~100",    lambda r: 0.0 <= r["accum_score"] <= 100.0),
        ("signals 키 6종",        lambda r: {"obv_norm", "cmf", "updown_ratio",
                                            "ad_norm", "vol_surge", "price_chg_20d"}
                                            .issubset(r["signals"])),
    )

    # ── compute_accumulation: 분산 패턴 ───────────────────────────────────────
    logger.info("[2] compute_accumulation — 분산 패턴")
    failures += _check("distribution 패턴",
        lambda: compute_accumulation("DIS", make_distribution()),
        ("dict 반환",          lambda r: isinstance(r, dict)),
        ("accum_score < 45",   lambda r: r["accum_score"] < 45),
        ("verdict == 분산",     lambda r: r["verdict"] == "분산"),
        ("accum_score 0~100",  lambda r: 0.0 <= r["accum_score"] <= 100.0),
    )

    # ── compute_accumulation: 현금성 패턴 (변동성 필터) ──────────────────────────
    logger.info("[3] compute_accumulation — 현금성 패턴 (MIN_VOL_ANNUAL=%.2f)", MIN_VOL_ANNUAL)
    failures += _check("cash 패턴 → None",
        lambda: compute_accumulation("CASH", make_cash()),
        ("None 반환 (변동성 필터)", lambda r: r is None),
    )

    # ── 데이터 부족 / 컬럼 누락 방어 ──────────────────────────────────────────
    logger.info("[4] 입력 방어 — 빈 DF / 30행 미만 / 컬럼 누락")
    failures += _check("빈 DataFrame → None",
        lambda: compute_accumulation("EMPTY", pd.DataFrame()),
        ("None 반환", lambda r: r is None),
    )
    failures += _check("30행 미만 → None",
        lambda: compute_accumulation("SHORT", make_accumulation(ticker_n=25)),
        ("None 반환", lambda r: r is None),
    )
    failures += _check("Close 컬럼 누락 → None (예외 아님)",
        lambda: compute_accumulation("NOCLOSE", make_accumulation().drop(columns=["Close"])),
        ("None 반환", lambda r: r is None),
    )
    failures += _check("Volume 컬럼 누락 → None (예외 아님)",
        lambda: compute_accumulation("NOVOL", make_accumulation().drop(columns=["Volume"])),
        ("None 반환", lambda r: r is None),
    )

    # ── accum_score 범위 불변식 (세 패턴 전부) ────────────────────────────────
    logger.info("[5] accum_score 0~100 불변식")
    failures += _check("accum_score 항상 0~100",
        lambda: [compute_accumulation(t, df) for t, df in
                 (("A", make_accumulation()), ("N", make_neutral()), ("D", make_distribution()))],
        ("모두 0~100 범위", lambda r: all(0.0 <= m["accum_score"] <= 100.0 for m in r if m)),
    )

    # ── rank_accumulation: 합성 fetcher + enrich=False (무네트워크) ─────────────
    logger.info("[6] rank_accumulation — 합성 price_fetcher + enrich=False")
    _data = {"T1": make_accumulation(seed=1), "T2": make_neutral(seed=5), "T3": make_distribution(seed=2)}

    def _fetcher(tickers, days=160):
        return {t: _data[t] for t in tickers if t in _data}

    failures += _check("rank_accumulation (min_score=0)",
        lambda: rank_accumulation(["T1", "T2", "T3"], price_fetcher=_fetcher,
                                  enrich=False, min_score=0, limit=10),
        ("list 반환",          lambda r: isinstance(r, list)),
        ("길이 <= limit",      lambda r: len(r) <= 10),
        ("accum_score 내림차순", lambda r: all(r[i]["accum_score"] >= r[i + 1]["accum_score"]
                                            for i in range(len(r) - 1))),
        ("institutional None (enrich=False)",
                               lambda r: all(e.get("institutional") is None for e in r)),
        ("최상위는 매집 패턴 T1", lambda r: r[0]["ticker"] == "T1"),
    )

    # ── rank_accumulation: min_score 필터 동작 ────────────────────────────────
    logger.info("[7] rank_accumulation — min_score 필터")
    failures += _check("min_score 필터 (높게 주면 일부 탈락)",
        lambda: (
            rank_accumulation(["T1", "T2", "T3"], price_fetcher=_fetcher,
                              enrich=False, min_score=0, limit=10),
            rank_accumulation(["T1", "T2", "T3"], price_fetcher=_fetcher,
                              enrich=False, min_score=90, limit=10),
        ),
        ("min_score=90 결과가 더 짧음", lambda r: len(r[1]) < len(r[0])),
        ("min_score=90 전부 >=90",     lambda r: all(e["accum_score"] >= 90 for e in r[1])),
        ("min_score=90 에 T1 잔존",     lambda r: any(e["ticker"] == "T1" for e in r[1])),
    )

    # ── fetch_13f: 한국 티커 → None (네트워크 없이 즉시) ────────────────────────
    logger.info("[8] fetch_13f — .KS 즉시 None")
    failures += _check("fetch_13f('005930.KS')",
        lambda: fetch_13f("005930.KS"),
        ("None 반환 (한국·무네트워크)", lambda r: r is None),
    )
    failures += _check("fetch_13f(빈 문자열)",
        lambda: fetch_13f(""),
        ("None 반환", lambda r: r is None),
    )

    # ── 표기 헬퍼 (entry 한 건 재사용) ────────────────────────────────────────
    logger.info("[9] 표기 헬퍼 — accumulation_line / mobile_block / clean_entry")
    entry = compute_accumulation("T1", make_accumulation(seed=1))

    failures += _check("accumulation_line",
        lambda: accumulation_line(entry, name_fn=lambda t: t),
        ("str 반환",   lambda r: isinstance(r, str)),
        ("'|' 포함",    lambda r: "|" in r),
        ("티커 포함",   lambda r: "T1" in r),
    )

    failures += _check("accumulation_mobile_block",
        lambda: accumulation_mobile_block([entry], title="🏛️ 기관 매집", name_fn=lambda t: t),
        ("list[str] 반환", lambda r: isinstance(r, list) and all(isinstance(x, str) for x in r)),
        ("첫 줄 제목 포함", lambda r: "기관 매집" in r[0]),
        ("종목 줄 존재",    lambda r: any("T1" in x for x in r)),
    )

    failures += _check("clean_entry",
        lambda: clean_entry(entry, name_fn=lambda t: t),
        ("dict 반환",        lambda r: isinstance(r, dict)),
        ("필수 키 포함",      lambda r: {"ticker", "accum_score", "verdict", "signals"}.issubset(r)),
        ("json 직렬화 성공",  lambda r: bool(json.dumps(r, ensure_ascii=False))),
    )

    return failures


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger.info("=== institutional_flow_smoke_test 시작 [%s] ===",
                datetime.now().strftime("%Y-%m-%d %H:%M"))
    t0 = time.time()

    try:
        failures = run_tests()
    except Exception as e:
        msg = f"❌ institutional_flow_smoke_test 실행 자체 실패: {e}"
        logger.error(msg)
        _alert(msg)
        sys.exit(1)

    elapsed = time.time() - t0
    total_checks = 30

    if failures:
        msg = "\n".join(failures)
        logger.error("실패 %d건 (%.1fs):\n%s", len(failures), elapsed, msg)
        _alert(msg)
        sys.exit(1)
    else:
        logger.info("✅ 모든 테스트 통과 (%d항목, %.1fs)", total_checks, elapsed)
        sys.exit(0)


if __name__ == "__main__":
    main()
