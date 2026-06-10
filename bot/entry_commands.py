"""bot/entry_commands.py — /entry 진입 타점 분석 커맨드

커맨드:
  /entry                 — 포트폴리오 + 레버리지 진입 분석
  /entry us50            — 미국 시총 상위 50 분석
  /entry kr              — 한국 시총 상위 10 분석
  /entry watch           — 전체 감시 (포트 + us50 + kr10)
  /entry TICKER          — 단일 종목 상세 분석 (한국: /entry 005930.KS)
  /entry alert reset     — 알림 쿨다운 초기화 (재알림 허용)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_UNIVERSE_CMDS = {"US50", "KR", "WATCH", "LEVERAGE"}


def cmd_entry(chat_id: str, args: list, send_fn=None) -> None:
    """진입 타점 분석 커맨드 핸들러."""
    from telegram_bot import send as _default_send
    _send = send_fn if send_fn is not None else _default_send

    raw  = [a.strip() for a in (args or [])]
    args = [a.upper() for a in raw]

    # /entry alert reset
    if len(args) >= 2 and args[0] == "ALERT" and args[1] == "RESET":
        from ml.entry_analyzer import reset_alert_state
        reset_alert_state()
        _send(chat_id, "✅ 진입 알림 쿨다운 초기화 완료 — 다음 조건 충족 시 즉시 알림")
        return

    # 서브 유니버스 커맨드
    if args and args[0] in _UNIVERSE_CMDS:
        sub = args[0]
        if sub == "US50":
            _entry_universe(chat_id, "us_top50", "🇺🇸 미국 시총 상위 50", _send)
        elif sub == "KR":
            _entry_universe(chat_id, "kr_top10", "🇰🇷 한국 시총 상위 10", _send)
        elif sub == "WATCH":
            _entry_universe(chat_id, "watch", "🌐 전체 감시 (포트+US50+KR10)", _send)
        elif sub == "LEVERAGE":
            _entry_universe(chat_id, "leverage", "📊 레버리지 ETF", _send)
        return

    # /entry TICKER (단일 종목 — 미국/한국 모두 지원)
    if len(raw) == 1:
        ticker = raw[0]
        # 한국 주식: 숫자로 시작하면 .KS 붙이기
        if ticker.replace(".", "").isdigit():
            ticker = ticker if "." in ticker else ticker + ".KS"
        else:
            ticker = ticker.upper()
        _entry_single(chat_id, ticker, _send)
        return

    # /entry (기본: 포트폴리오 + 레버리지)
    _entry_universe(chat_id, "portfolio", "📊 진입 타점 분석 (포트폴리오)", _send)


def _entry_universe(chat_id: str, universe: str, title: str, _send) -> None:
    """유니버스별 진입 분석."""
    wait_msg = {
        "portfolio": "⏳ 포트폴리오 + 레버리지 분석 중...",
        "us_top50":  "⏳ 미국 시총 상위 50 분석 중... (약 30초)",
        "kr_top10":  "⏳ 한국 시총 상위 10 분석 중... (약 20초)",
        "watch":     "⏳ 전체 감시 대상 분석 중... (약 60초)",
        "leverage":  "⏳ 레버리지 ETF 분석 중...",
    }.get(universe, "⏳ 분석 중...")
    _send(chat_id, wait_msg)
    try:
        from ml.entry_analyzer import analyze_all_entries, format_entry_report
        scores = analyze_all_entries(universe=universe)
        report = format_entry_report(scores, title=f"📊 {title}")
        for chunk in _split(report):
            _send(chat_id, chunk)
    except Exception as e:
        _send(chat_id, f"❌ 진입 분석 오류: {e}")
        logger.exception("cmd_entry %s", universe)


def _entry_all(chat_id: str, _send) -> None:
    _entry_universe(chat_id, "portfolio", "진입 타점 분석 (포트폴리오)", _send)


def _entry_single(chat_id: str, ticker: str, _send) -> None:
    """단일 종목 상세 진입 분석 (미국/한국 모두 지원)."""
    _send(chat_id, f"⏳ {ticker} 진입 분석 중...")
    try:
        import pandas as pd
        from ml.data_pipeline import fetch_prices
        from ml.entry_analyzer import (
            analyze_entry, format_alert_message,
            LEVERAGE_ETFS, LEVERAGE_UNDERLYING, is_kr_stock,
        )

        all_tickers = list({ticker, "QQQ", "SPY", "^VIX"})
        prices = fetch_prices(all_tickers, days=756)

        vix_df = prices.get("^VIX", pd.DataFrame())
        vix_s  = vix_df.get("Close") if hasattr(vix_df, "get") else None
        if vix_s is None or len(vix_s) == 0:
            vix_s = pd.Series(20.0, index=pd.date_range("2020-01-01", periods=1))

        df = prices.get(ticker)
        if df is None:
            _send(chat_id, f"❌ {ticker} 가격 데이터 없음 (yfinance 미지원 티커일 수 있음)")
            return

        category = "leverage" if ticker in LEVERAGE_ETFS else "stock"
        und_key  = LEVERAGE_UNDERLYING.get(ticker, "QQQ")
        und_px_df = prices.get(und_key, pd.DataFrame()) if ticker in LEVERAGE_ETFS else pd.DataFrame()
        und_px    = und_px_df.get("Close") if hasattr(und_px_df, "get") else None

        score = analyze_entry(ticker, df, vix_s, n_similar=40,
                              category=category, underlying_price=und_px)
        if score is None:
            _send(chat_id, f"❌ {ticker} 분석 실패 — 데이터 부족 (최소 120일 필요)")
            return

        msg = format_alert_message(score)
        try:
            from ml.technical_rating import build_reference_brief
            ref = build_reference_brief(ticker, df)
            if ref:
                msg += "\n" + ref
        except Exception:
            logger.debug("참고지표 생략: %s", ticker)
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
