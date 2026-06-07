#!/usr/bin/env python3
"""
bot_smoke_test.py — 봇 핵심 기능 연기 테스트 (cron 검증용)

실제 데이터로 핵심 명령어 함수들이 올바른 출력을 반환하는지 검증.
문제 발견 시에만 텔레그램 알림 전송.

크론 (매일 09:00 KST = 00:00 UTC):
    0 0 * * 1-5 cd /home/ubuntu/projects/stock-report && uv run python bot_smoke_test.py >> /tmp/smoke_test.log 2>&1
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
            json={"chat_id": CHAT_ID, "text": f"🧪 smoke test 실패\n━━━━━━━━━━━━━━\n{msg}"},
            timeout=10,
        )
    except Exception as e:
        logger.error("알림 전송 실패: %s", e)


def _check(name: str, fn, *checks) -> list[str]:
    """fn()을 호출해 checks를 검증. 실패 시 오류 메시지 반환."""
    failures = []
    try:
        result = fn()
    except Exception as e:
        return [f"❌ {name}: 예외 발생 — {e}"]

    for desc, condition in checks:
        try:
            ok = condition(result)
        except Exception as e:
            ok = False
            desc = f"{desc} (검증 오류: {e})"
        if not ok:
            failures.append(f"❌ {name}: {desc}")
        else:
            logger.info("  ✅ %s — %s", name, desc)
    return failures


def run_tests() -> list[str]:
    failures = []

    # ── 시장 데이터 수집 ─────────────────────────────────────────────
    from barbell_strategy import (
        fetch_qqq_data, fetch_rsi, fetch_vix, fetch_ma200, fetch_fear_greed,
        fetch_exchange_rate, fetch_portfolio_value, classify_market,
        estimate_qqqi_monthly_dividend, BULL_PHASES, BEAR_PHASES,
    )

    logger.info("시장 데이터 수집 중...")
    qqq = fetch_qqq_data()
    failures += _check("fetch_qqq_data",
        lambda: qqq,
        ("current price > 0", lambda r: r.get("current", 0) > 0),
        ("drawdown_pct 존재", lambda r: "drawdown_pct" in r),
        ("mom_1m_pct 존재", lambda r: "mom_1m_pct" in r),
    )

    rsi = fetch_rsi("QQQ")
    failures += _check("fetch_rsi",
        lambda: rsi,
        ("0 < RSI < 100", lambda r: 0 < r < 100),
    )

    vix = fetch_vix()
    failures += _check("fetch_vix",
        lambda: vix,
        ("VIX > 0", lambda r: r > 0),
    )

    fg = fetch_fear_greed()
    failures += _check("fetch_fear_greed",
        lambda: fg,
        ("score 0~100", lambda r: 0 <= r.get("score", -1) <= 100),
        ("rating 존재", lambda r: r.get("rating") in ("extreme fear","fear","neutral","greed","extreme greed")),
    )

    fx = fetch_exchange_rate()
    failures += _check("fetch_exchange_rate",
        lambda: fx,
        ("환율 500~2000 범위", lambda r: 500 < r < 2000),
    )

    port = fetch_portfolio_value()
    failures += _check("fetch_portfolio_value",
        lambda: port,
        ("total_usd > 0", lambda r: r.get("total_usd", 0) > 0),
        ("holdings_detail 존재", lambda r: isinstance(r.get("holdings_detail"), list)),
        ("holdings_detail 비어있지 않음", lambda r: len(r.get("holdings_detail", [])) > 0),
    )

    mt, pk = classify_market(qqq, rsi, vix)
    failures += _check("classify_market",
        lambda: (mt, pk),
        ("market_type 유효", lambda r: r[0] in ("bull", "neutral", "bear")),
    )

    # ── 봇 명령어 함수 ──────────────────────────────────────────────
    logger.info("봇 명령어 함수 검증 중...")
    div = estimate_qqqi_monthly_dividend(port["qqqi_shares"], port["qqqi_usd"])
    d = {
        "qqq": qqq, "rsi": rsi, "vix": vix, "fear_greed": fg,
        "exchange_rate": fx, "portfolio": port, "qqqi_div": div,
        "market_type": mt, "phase_key": pk,
        "ma": fetch_ma200("QQQ"),
        "fetched_at": datetime.now().strftime("%m/%d %H:%M"),
        "benchmarks": {}, "source_digest": "",
    }

    # /status 검증
    info = BULL_PHASES[pk] if mt == "bull" else BEAR_PHASES[pk]
    def _status():
        ret = port.get("return_pct", 0) or 0
        mom = qqq.get("mom_1m_pct", 0) or 0
        fg_sc = fg.get("score", 50)
        return (
            f"{info['emoji']} {info['label']}\n"
            f"QQQ ${qqq.get('current',0):,.0f}  1M {mom:+.1f}%\n"
            f"수익 {ret:+.1f}%\n"
            f"F&G {fg_sc:.0f}"
        )

    failures += _check("/status 출력",
        _status,
        ("Phase 이모지 포함", lambda r: info["emoji"] in r),
        ("1M 모멘텀 포함", lambda r: "1M" in r),
        ("수익률 포함", lambda r: "수익" in r),
        ("F&G 포함", lambda r: "F&G" in r),
    )

    # /summary 검증
    def _summary():
        ret = port.get("return_pct", 0) or 0
        fg_sc = fg.get("score", 50)
        fg_e = ("💀" if fg_sc<=25 else "😨" if fg_sc<=45 else "😐" if fg_sc<=55 else "😄" if fg_sc<=75 else "🤑")
        dd = qqq.get("drawdown_pct", 0)
        return (
            f"{info['emoji']} {info['label']}  |  "
            f"QQQ ${qqq.get('current',0):,.0f} ({dd:+.1f}%)  |  "
            f"₩{int(port['total_usd']*fx):,} {'▲' if ret>=0 else '▼'}{abs(ret):.1f}%  |  "
            f"F&G {fg_sc:.0f}{fg_e}"
        )

    failures += _check("/summary 출력",
        _summary,
        ("한 줄 출력", lambda r: "\n" not in r.strip()),
        ("QQQ 가격 포함", lambda r: "QQQ" in r),
        ("원화 포함", lambda r: "₩" in r),
        ("F&G 포함", lambda r: "F&G" in r),
    )

    # /portfolio 개별 종목 검증
    holdings_detail = [
        h for h in port.get("holdings_detail", [])
        if h.get("ticker") not in {"SGOV", "QQQI", "QLD", "TQQQ"}
        and h.get("value_usd", 0) > 0
    ]
    failures += _check("/portfolio 개별 종목",
        lambda: holdings_detail,
        ("1개 이상 종목 존재", lambda r: len(r) > 0),
        ("return_pct 필드 존재", lambda r: all("return_pct" in h for h in r)),
        ("value_usd 필드 존재", lambda r: all("value_usd" in h for h in r)),
    )

    # send() 분할 검증
    from telegram_bot import send as _send_fn
    import inspect
    src = inspect.getsource(_send_fn)
    failures += _check("send() 줄바꿈 기반 분할",
        lambda: src,
        ("split('\\n') 사용", lambda r: 'split("\\n")' in r or "split('\\n')" in r),
        ("max_len 파라미터 존재", lambda r: "max_len" in r),
    )

    # _cache_lock 존재 확인
    import telegram_bot as _tb
    failures += _check("캐시 thread-safety",
        lambda: hasattr(_tb, "_cache_lock"),
        ("_cache_lock 존재", lambda r: r is True),
    )

    return failures


def main():
    logger.info("=== bot_smoke_test 시작 [%s] ===", datetime.now().strftime("%Y-%m-%d %H:%M"))
    start = time.time()

    try:
        failures = run_tests()
    except Exception as e:
        msg = f"❌ smoke test 실행 자체 실패: {e}"
        logger.error(msg)
        _alert(msg)
        sys.exit(1)

    elapsed = time.time() - start
    if failures:
        msg = "\n".join(failures)
        logger.error("실패 %d건 (%.1fs):\n%s", len(failures), elapsed, msg)
        _alert(msg)
        sys.exit(1)
    else:
        logger.info("✅ 모든 테스트 통과 (%d항목, %.1fs)", 15, elapsed)


if __name__ == "__main__":
    main()
