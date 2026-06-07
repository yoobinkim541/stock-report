"""bot/entry_commands.py — /entry 진입 타점 분석 커맨드

커맨드:
  /entry                 — 전체 포트폴리오 + 레버리지 진입 분석
  /entry TICKER          — 단일 종목 상세 분석
  /entry alert reset     — 알림 쿨다운 초기화 (재알림 허용)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def cmd_entry(chat_id: str, args: list, send_fn=None) -> None:
    """진입 타점 분석 커맨드 핸들러."""
    from telegram_bot import send as _default_send
    _send = send_fn if send_fn is not None else _default_send

    args = [a.strip().upper() for a in (args or [])]

    # /entry alert reset
    if len(args) >= 2 and args[0] == "ALERT" and args[1] == "RESET":
        from ml.entry_analyzer import reset_alert_state
        reset_alert_state()
        _send(chat_id, "✅ 진입 알림 쿨다운 초기화 완료 — 다음 조건 충족 시 즉시 알림")
        return

    # /entry TICKER (단일 종목 상세)
    if len(args) == 1 and args[0] not in ("ALERT",):
        ticker = args[0]
        _entry_single(chat_id, ticker, _send)
        return

    # /entry (전체 분석)
    _entry_all(chat_id, _send)


def _entry_all(chat_id: str, _send) -> None:
    """전체 포트폴리오 + 레버리지 진입 분석."""
    _send(chat_id, "⏳ 진입 타점 분석 중... (약 20초)")
    try:
        from ml.entry_analyzer import analyze_all_entries, format_entry_report
        scores = analyze_all_entries()
        report = format_entry_report(scores)
        for chunk in _split(report):
            _send(chat_id, chunk)
    except Exception as e:
        _send(chat_id, f"❌ 진입 분석 오류: {e}")
        logger.exception("cmd_entry all")


def _entry_single(chat_id: str, ticker: str, _send) -> None:
    """단일 종목 상세 진입 분석."""
    _send(chat_id, f"⏳ {ticker} 진입 분석 중...")
    try:
        from ml.data_pipeline import fetch_prices
        from ml.entry_analyzer import (
            analyze_entry, format_alert_message,
            LEVERAGE_ETFS, LEVERAGE_UNDERLYING,
        )

        all_tickers = list({ticker, "QQQ", "SPY", "^VIX"})
        prices = fetch_prices(all_tickers, days=756)

        vix_s = prices.get("^VIX", {})
        vix_s = vix_s.get("Close") if hasattr(vix_s, "get") else None
        if vix_s is None:
            import pandas as pd
            vix_s = pd.Series(20.0, index=pd.date_range("2020-01-01", periods=1))

        df = prices.get(ticker)
        if df is None:
            _send(chat_id, f"❌ {ticker} 가격 데이터 없음")
            return

        category = "leverage" if ticker in LEVERAGE_ETFS else "stock"
        und_key  = LEVERAGE_UNDERLYING.get(ticker, "QQQ")
        und_px   = prices.get(und_key, {}).get("Close") if ticker in LEVERAGE_ETFS else None

        score = analyze_entry(ticker, df, vix_s, n_similar=40,
                              category=category, underlying_price=und_px)
        if score is None:
            _send(chat_id, f"❌ {ticker} 분석 실패 (데이터 부족)")
            return

        msg = format_alert_message(score)
        for chunk in _split(msg):
            _send(chat_id, chunk)

    except Exception as e:
        _send(chat_id, f"❌ {ticker} 분석 오류: {e}")
        logger.exception("cmd_entry single %s", ticker)


def _split(text: str, limit: int = 4000) -> list[str]:
    chunks, cur = [], []
    for line in text.splitlines():
        cur.append(line)
        if sum(len(l) + 1 for l in cur) > limit:
            chunks.append("\n".join(cur[:-1]))
            cur = [line]
    if cur:
        chunks.append("\n".join(cur))
    return chunks or [""]
