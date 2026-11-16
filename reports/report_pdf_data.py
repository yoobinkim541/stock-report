"""reports/report_pdf_data.py — 데일리 PDF 리포트의 입력을 한 모델로 모은다.

숫자는 investment-data JSON 과 가격 이력에서만 온다. 문장은 이미 생성·검증된
마크다운(투자 리포트, 시장 리포트)에서 그대로 읽는다. 여기서는 LLM 을 부르지 않고
판단을 새로 만들지 않는다 — PDF 는 같은 날 마크다운과 같은 내용을 다른 양식으로 보여줄 뿐이다.

입력이 하나 빠지면 그 블록만 비운다(None/빈 리스트). 필수는 investment-data JSON 하나다.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import date as _date
from datetime import timedelta
from pathlib import Path

from reports import account_performance

WEEKDAYS = "월화수목금토일"

# 자산 표: (묶음 제목, 단위 표기, ((표시 이름, 심볼[, 종류]), …)).
# 종류 "yield" 는 금리(%) 계열 — 변동을 비율이 아니라 차이(bp)로 적는다.
MARKET_GROUPS = (
    ("주요국 증시", "pt, %", (
        ("S&P 500", "^GSPC"), ("NASDAQ", "^IXIC"), ("다우", "^DJI"), ("러셀 2000", "^RUT"),
        ("필라델피아 반도체", "^SOX"), ("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11"),
        ("닛케이 225", "^N225"), ("상해종합", "000001.SS"), ("유로스톡스 50", "^STOXX50E"), ("VIX", "^VIX"))),
    ("미국 금리", "%, bp", (
        ("미국채 3개월", "^IRX", "yield"), ("미국채 5년", "^FVX", "yield"),
        ("미국채 10년", "^TNX", "yield"), ("미국채 30년", "^TYX", "yield"))),
    ("통화", "%", (
        ("달러인덱스", "DX-Y.NYB"), ("원/달러", "KRW=X"), ("엔/달러", "JPY=X"), ("달러/유로", "EURUSD=X"))),
    ("원자재·대체자산", "달러, %", (
        ("WTI", "CL=F"), ("금", "GC=F"), ("구리", "HG=F"), ("비트코인", "BTC-USD"))),
    ("대표 ETF", "달러, %", (
        ("S&P 500 (SPY)", "SPY"), ("나스닥 100 (QQQ)", "QQQ"),
        ("미 장기국채 (TLT)", "TLT"), ("하이일드 (HYG)", "HYG"))),
)
# 표지 지표 띠: (표시 이름, 심볼, 단위). SPY·QQQ 는 리포트 본문과 같은 값을 쓰려고 따로 넣는다.
KPI_ROWS = (("KOSPI", "^KS11", "pt"), ("USD/KRW", "KRW=X", "원"), ("미국채 10년", "^TNX", "%"), ("VIX", "^VIX", "pt"))
# 시장 쪽의 작은 그림 넷: (제목, 심볼, 소수 자리)
MINI_CHARTS = (("S&P 500", "^GSPC", 0), ("미국채 10년 금리 (%)", "^TNX", 2),
               ("달러인덱스", "DX-Y.NYB", 1), ("WTI (달러/배럴)", "CL=F", 1))
BENCHMARK = "SPY"
BENCHMARKS = ("SPY", "QQQ")
# 시세 조회가 막힌 날 쓰는 마지막 조회분. 이보다 오래된 캐시는 쓰지 않는다.
PRICE_CACHE = Path.home() / "reports" / "ml-cache" / "report-pdf-prices.json"
PRICE_CACHE_MAX_AGE_DAYS = 7
FUND_WINDOWS = (("1M", "m1"), ("3M", "m3"), ("6M", "m6"), ("12M", "y1"))
# (표시 이름, price_stats 키, 거래일 수). 1M 은 리포트 본문(daily_signals)과 같은 20거래일이다.
RETURN_WINDOWS = (("1D", "d1", 1), ("1W", "d5", 5), ("1M", "m1", 20), ("3M", "m3", 63),
                  ("6M", "m6", 126), ("12M", "y1", 252))
SIGNAL_LABEL = {"Positive": "긍정", "Neutral": "중립", "Warning": "경고", "Critical": "심각"}
SIGNAL_TONE = {"Positive": "pos", "Neutral": "neu", "Warning": "warn", "Critical": "neg"}

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\u2B00-\u2BFF\u2139\uFE0F\u200D\u20E3]"
)
_HEADING = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_TAG = re.compile(r"<[^>]+>")
_STARS = re.compile(r"\s*(★+)\s*$")
_CAL = re.compile(r"(\d{1,2}/\d{1,2})\s+(\d{1,2}:\d{2})\s*·\s*(.+)$")


def clean(text: object) -> str:
    """마크다운 강조·링크·HTML·이모지를 걷어낸 한 줄 텍스트."""
    s = str(text or "")
    s = _TAG.sub("", _LINK.sub(r"\1", s))
    s = s.replace("**", "").replace("`", "")
    s = _EMOJI.sub("", s)
    return re.sub(r"\s+", " ", s).strip(" *")


def md_sections(text: str) -> list[tuple[int, str, list[str]]]:
    """마크다운을 (제목 수준, 정리된 제목, 본문 줄) 목록으로 자른다. 본문은 다음 제목 전까지."""
    out: list[tuple[int, str, list[str]]] = []
    for line in (text or "").splitlines():
        m = _HEADING.match(line)
        if m:
            out.append((len(m.group(1)), clean(m.group(2)), []))
        elif out:
            out[-1][2].append(line)
    return out


def section(sections: list[tuple[int, str, list[str]]], keyword: str, *, deep: bool = False) -> list[str]:
    """제목에 keyword 가 든 첫 섹션의 본문. deep 이면 하위 제목 본문까지 이어 붙인다."""
    for i, (level, title, body) in enumerate(sections):
        if keyword not in title:
            continue
        lines = list(body)
        if deep:
            for sub_level, sub_title, sub_body in sections[i + 1:]:
                if sub_level <= level:
                    break
                lines += [f"{'#' * sub_level} {sub_title}", *sub_body]
        return lines
    return []


def bullets(lines: list[str], *, nested: bool = False) -> list[str]:
    """'- ' 불릿. nested 가 아니면 들여쓰지 않은 것만."""
    out = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped.startswith("- "):
            continue
        if not nested and line[:1] in (" ", "\t"):
            continue
        text = clean(stripped[2:])
        if text:
            out.append(text)
    return out


def leading_bullets(lines: list[str]) -> list[str]:
    """섹션 맨 앞의 불릿 묶음만 — 뒤따르는 소제목 블록의 불릿은 섞지 않는다."""
    head: list[str] = []
    for line in lines:
        if line.lstrip().startswith("- "):
            head.append(line)
        elif line.strip() and head:
            break
    return bullets(head)


def block_after(lines: list[str], marker: str) -> list[str]:
    """marker 가 든 줄 다음부터 빈 줄 뒤 다른 문단(굵은 소제목·구분선)이 나오기 전까지."""
    for i, line in enumerate(lines):
        if marker in line:
            out: list[str] = []
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s.startswith(("**", "---", "#", "![")) and out:
                    break
                if s:
                    out.append(nxt)
            return out
    return []


def md_table(lines: list[str]) -> list[list[str]]:
    """첫 파이프 표를 [헤더, 행…] 으로. 구분선 행은 버린다."""
    rows: list[list[str]] = []
    for line in lines:
        s = line.strip()
        if not s.startswith("|"):
            if rows:
                break
            continue
        cells = [clean(c) for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c) and any(cells):
            continue
        rows.append(cells)
    return rows


def _num(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _read_text(path: Path | None) -> str:
    try:
        return path.read_text(encoding="utf-8") if path else ""
    except OSError:
        return ""


# ── 가격 이력에서 나오는 값 ───────────────────────────────────────────────────

def _closes(frame) -> list[float]:
    if frame is None:
        return []
    try:
        return [float(v) for v in frame["Close"].dropna().tolist()]
    except Exception:
        return []


def pct_change(closes: list[float], sessions: int) -> float | None:
    if len(closes) <= sessions or not closes[-1 - sessions]:
        return None
    return (closes[-1] / closes[-1 - sessions] - 1.0) * 100.0


def bp_change(closes: list[float], sessions: int) -> float | None:
    """금리(%) 계열의 변동폭(bp)."""
    if len(closes) <= sessions:
        return None
    return (closes[-1] - closes[-1 - sessions]) * 100.0


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI 마지막 값."""
    if len(closes) <= period:
        return None
    gains = losses = 0.0
    for prev, cur in zip(closes[:period], closes[1:period + 1]):
        diff = cur - prev
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    for prev, cur in zip(closes[period:-1], closes[period + 1:]):
        diff = cur - prev
        avg_gain = (avg_gain * (period - 1) + max(diff, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0.0)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def price_stats(frame, kind: str = "price") -> dict:
    """가격 이력 하나에서 표·카드가 쓰는 값. 이력이 없으면 빈 dict.

    kind="yield" 면 기간 변동(d1…y1, ytd)이 등락률(%)이 아니라 차이(bp)다.
    """
    closes = _closes(frame)
    if len(closes) < 2:
        return {}
    change = bp_change if kind == "yield" else pct_change
    window = closes[-252:]
    low, high = min(window), max(window)
    ytd = None
    try:
        index = frame["Close"].dropna().index
        year = index[-1].year
        prior = [c for ts, c in zip(index, closes) if ts.year < year]
        if prior and prior[-1]:
            ytd = (closes[-1] - prior[-1]) * 100.0 if kind == "yield" else (closes[-1] / prior[-1] - 1.0) * 100.0
    except Exception:
        ytd = None

    def ma(n: int) -> float | None:
        return sum(closes[-n:]) / n if len(closes) >= n else None

    return {
        "kind": kind, "last": closes[-1],
        "d1": change(closes, 1), "d5": change(closes, 5), "m1": change(closes, 20),
        "m3": change(closes, 63), "m6": change(closes, 126), "y1": change(closes, 252), "ytd": ytd,
        "low52": low, "high52": high,
        "pos52": (closes[-1] - low) / (high - low) * 100.0 if high > low else None,
        "rsi": rsi(closes), "ma20": ma(20), "ma60": ma(60), "ma120": ma(120),
        "spark": closes[-60:],
    }


def clip_history(prices: dict, report_date: str) -> dict:
    """리포트 날짜 전 봉만 남긴다.

    리포트는 KST 아침에 나오므로 그 날짜의 봉은 아직 없거나 장중 미완성이다. 과거 날짜를
    다시 만들 때도 그날 리포트가 본 마지막 종가(전 거래일)와 같은 값이 된다.
    """
    try:
        limit = _date.fromisoformat(report_date)
    except ValueError:
        return dict(prices)
    out = {}
    for symbol, frame in prices.items():
        try:
            out[symbol] = frame[[ts.date() < limit for ts in frame.index]]
        except Exception:
            out[symbol] = frame
    return out


def _download(symbols: list[str], period: str) -> dict:
    """Yahoo Finance 일봉 → 심볼별 표. Close 는 배당·분할을 반영한 수정주가, Raw 는 분할만 반영한 종가."""
    import pandas as pd
    import yfinance as yf

    data = yf.download(symbols, period=period, interval="1d", group_by="ticker",
                       auto_adjust=False, actions=True, progress=False, threads=True)
    out = {}
    for symbol in symbols:
        try:
            raw = data[symbol] if isinstance(data.columns, pd.MultiIndex) else data
            raw = raw.dropna(subset=["Close"])
            if len(raw) < 2:
                continue
            adjusted = raw["Adj Close"] if "Adj Close" in raw else raw["Close"]
            frame = pd.DataFrame({
                "Close": adjusted.fillna(raw["Close"]), "Volume": raw["Volume"].fillna(0.0), "Raw": raw["Close"],
                "Dividends": raw["Dividends"].fillna(0.0) if "Dividends" in raw else 0.0,
                "Splits": raw["Stock Splits"].fillna(0.0) if "Stock Splits" in raw else 0.0,
            })
            if getattr(frame.index, "tz", None) is not None:
                frame = frame.tz_localize(None)
            out[symbol] = frame
        except Exception:
            continue
    return out


def _cache_entry(frame, fetched: str) -> dict:
    return {
        "fetched": fetched, "index": [ts.strftime("%Y-%m-%d") for ts in frame.index],
        **{column: [round(float(v), 6) for v in frame[column].tolist()] for column in ("Close", "Volume", "Raw")},
        **{column: {ts.strftime("%Y-%m-%d"): float(v) for ts, v in frame[column].items() if v}
           for column in ("Dividends", "Splits")},
    }


def _cache_frame(entry: dict):
    import pandas as pd

    index = pd.DatetimeIndex(entry["index"])
    frame = pd.DataFrame({column: entry[column] for column in ("Close", "Volume", "Raw")}, index=index)
    for column in ("Dividends", "Splits"):
        events = pd.Series({pd.Timestamp(day): value for day, value in entry[column].items()}, dtype=float)
        frame[column] = events.reindex(index).fillna(0.0)
    frame.attrs["cached"] = entry["fetched"]
    return frame


def fetch_price_history(symbols: list[str], *, period: str = "2y", cache_path: Path | None = PRICE_CACHE,
                        download=_download, today: _date | None = None, pause: float = 3.0) -> dict:
    """심볼별 일봉(Close·Volume·Raw·Dividends·Splits).

    받지 못한 심볼은 한 번 더 받고, 그래도 없으면 최근에 받아 둔 캐시로 채운다 — 그 표에는
    attrs["cached"](받은 날짜)가 붙는다. 캐시에도 없는 심볼은 빠지고 호출부가 그 블록만 접는다.
    """
    from lib import file_cache

    wanted = sorted({s for s in symbols if s})
    if not wanted:
        return {}
    out: dict = {}
    for attempt in range(2):
        need = [s for s in wanted if s not in out]
        if not need:
            break
        if attempt:
            time.sleep(pause)
        try:
            out.update(download(need, period))
        except Exception:
            continue
    if not cache_path:
        return out
    today = today or _date.today()
    oldest = (today - timedelta(days=PRICE_CACHE_MAX_AGE_DAYS)).isoformat()
    cached = {symbol: entry for symbol, entry in (file_cache.read_json(cache_path) or {}).items()
              if isinstance(entry, dict) and str(entry.get("fetched") or "") >= oldest}
    fresh = dict(out)
    for symbol in wanted:
        if symbol not in out and symbol in cached:
            try:
                out[symbol] = _cache_frame(cached[symbol])
            except Exception:
                continue
    if fresh:
        cached.update({symbol: _cache_entry(frame, today.isoformat()) for symbol, frame in fresh.items()})
        file_cache.write_json_atomic(cache_path, cached)
    return out


def sector_etfs() -> dict[str, str]:
    """시장 리포트와 같은 섹터 ETF 목록(단일 소스). 불러오지 못하면 섹터 블록을 생략한다."""
    try:
        from reports.market_report import SECTOR_ETFS
        return dict(SECTOR_ETFS)
    except Exception:
        return {}


def market_kinds() -> dict[str, str]:
    """자산 표에 나오는 심볼 → 종류("price" | "yield")."""
    return {row[1]: (row[2] if len(row) > 2 else "price") for _title, _unit, rows in MARKET_GROUPS for row in rows}


def history_symbols(data: dict, transactions: list[dict] | None = None) -> list[str]:
    """가격 이력이 필요한 심볼: 보유·비교 ETF·시장 표·섹터·스캔 대상, 그리고 과거에 거래한 종목."""
    portfolio = data.get("portfolio") or []
    holdings = [str(p.get("ticker") or "") for p in portfolio]
    peers = [str(peer) for p in portfolio for peer in ((p.get("etf_comparison") or {}).get("peers") or [])]
    universe = [str(i.get("ticker") or "") for i in (data.get("nasdaq_100_scan") or {}).get("all") or []]
    traded = [str(r.get("ticker") or "") for r in transactions or []]
    return [*holdings, *peers, *BENCHMARKS, *market_kinds(), *sector_etfs(), *universe, *traded]


def aligned(prices: dict, symbol: str, index) -> list[float]:
    if symbol not in prices or index is None:
        return []
    return [float(v) for v in prices[symbol]["Close"].reindex(index).ffill().bfill().tolist()]


def account_summary(account: dict | None, prices: dict, benchmarks: tuple[str, ...] = BENCHMARKS) -> dict:
    """계좌의 기간 수익률과 벤치마크 대비 차이(%p). 계좌 궤적이 없으면 rows 가 빈다."""
    if not account:
        return {"benchmarks": list(benchmarks), "rows": [], "since": None, "total": None, "net_invested": None,
                "income": None, "missing": []}
    index, twr = account["index"], account["twr"]
    lines = [aligned(prices, symbol, index) for symbol in benchmarks]
    rows = []
    for label, _key, sessions in RETURN_WINDOWS[2:5]:
        own = account_performance.period_return(twr, sessions)
        if own is None:
            continue
        others = [account_performance.period_return(line, sessions) for line in lines]
        rows.append({"label": label, "account": own, "vs": [own - o if o is not None else None for o in others]})
    return {
        "benchmarks": list(benchmarks), "rows": rows,
        "since": index[0].strftime("%Y.%m.%d"), "total": twr[-1] - 100.0,
        "net_invested": account["net_invested"][-1], "income": account["income"][-1],
        "missing": account["missing"],
    }


# ── 모델 조립 ────────────────────────────────────────────────────────────────

def _fund_compare(comparison: object, own: dict, stats: dict, max_peers: int = 3) -> dict | None:
    """ETF 의 기간별 총수익과 비교 ETF 대비 차이(%p). 비교 자료가 없으면 None.

    가격 이력이 있으면 카드의 수익률 표와 같은 거래일 구간으로 직접 계산한다(basis="sessions").
    없을 때만 리포트 JSON 의 값을 쓴다 — 그쪽은 달력일 구간이라 숫자가 조금 다르다(basis="calendar").
    """
    if not isinstance(comparison, dict):
        return None
    peers = [str(p) for p in (comparison.get("peers") or [])][:max_peers]
    if not peers:
        return None
    rows = [{"label": label, "ret": own[key],
             "diffs": [own[key] - stats[p][key] if (stats.get(p) or {}).get(key) is not None else None for p in peers]}
            for label, key in FUND_WINDOWS if own.get(key) is not None]
    if rows:
        return {"peers": peers, "rows": rows, "basis": "sessions"}
    for period in comparison.get("periods") or []:
        versus = period.get("vs") or {}
        rows.append({
            "label": str(period.get("actual_label") or period.get("label") or ""),
            "ret": _num(period.get("tr_return_pct", period.get("return_pct"))),
            "diffs": [_num((versus.get(p) or {}).get("tr_diff_pct", (versus.get(p) or {}).get("diff_pct")))
                      for p in peers],
        })
    rows = [r for r in rows if r["ret"] is not None]
    return {"peers": peers, "rows": rows[:5], "basis": "calendar"} if rows else None


def _holding(entry: dict, all_stats: dict, bench: dict) -> dict:
    stats = all_stats.get(str(entry.get("ticker")), {})
    decision = entry.get("decision_v2") or {}
    context = entry.get("decision_context") or {}
    held = entry.get("holding_context") or {}
    earnings = entry.get("earnings_context") or {}
    price = entry.get("price_info") or {}
    signal = str(entry.get("overall_signal") or "Neutral")
    grade = str(entry.get("fundamental_grade") or "N/A")
    is_fund = grade == "N/A" or bool(entry.get("etf_comparison"))
    roe = _num(earnings.get("roe"))
    pillars = []
    for key, label in (("financial", "재무"), ("timing", "타이밍"), ("news", "뉴스"), ("risk", "리스크")):
        part = decision.get(key) or {}
        if part.get("status"):
            pillars.append({"label": label, "status": clean(part.get("status"))})
    returns = [{"label": label, "abs": stats.get(key),
                "rel": stats[key] - bench[key] if stats.get(key) is not None and bench.get(key) is not None else None}
               for label, key, _sessions in RETURN_WINDOWS]
    return {
        "ticker": str(entry.get("ticker") or ""),
        "name": clean(entry.get("company_name") or held.get("name") or entry.get("ticker")),
        "is_fund": is_fund,
        "price": _num(price.get("current_price")) or stats.get("last"),
        "d1": _num(price.get("1d_change_pct")), "d5": _num(price.get("5d_change_pct")),
        "m1": _num(price.get("1mo_change_pct")),
        "m3": stats.get("m3"), "m6": stats.get("m6"), "y1": stats.get("y1"), "ytd": stats.get("ytd"),
        "returns": returns if any(r["abs"] is not None for r in returns) else [],
        "spark": stats.get("spark") or [],
        "rsi": stats.get("rsi"), "pos52": stats.get("pos52"),
        "high52": stats.get("high52"), "low52": stats.get("low52"),
        "weight": _num(held.get("weight_pct")), "value_usd": _num(held.get("value_usd")),
        "cost_usd": _num(held.get("cost_usd")), "ret_pct": _num(held.get("return_pct")),
        "avg_price": _num(held.get("avg_price_usd")), "shares": _num(held.get("shares")),
        "score": None if is_fund else _num(entry.get("fundamental_score")),
        "grade": "ETF" if is_fund else grade,
        "signal_label": SIGNAL_LABEL.get(signal, signal),
        "tone": SIGNAL_TONE.get(signal, "neu"),
        "action": clean(decision.get("action") or context.get("portfolio_action") or entry.get("judgment")),
        "today_action": clean(decision.get("today_action") or context.get("execution_plan")),
        "one_line": clean(decision.get("one_line_reason")),
        "confidence": _num(decision.get("confidence")),
        "pillars": pillars,
        "role": clean(context.get("role")),
        "fund_compare": _fund_compare(entry.get("etf_comparison"), stats, all_stats) if is_fund else None,
        "reasons": _points(entry.get("reasons")),
        "risks": _points(entry.get("risks")),
        # 재점검 조건이 리스크 문장을 그대로 되풀이하는 경우가 많다 — 새 정보만 남긴다.
        "triggers": [t for t in _points(context.get("recheck_triggers"))
                     if t not in _points(entry.get("risks"))][:2],
        # 이미 지난 날짜가 '다음 실적'으로 남아 있는 경우가 있다 — 그때는 비운다.
        "next_earnings": earnings.get("next_earnings_date") if (_num(earnings.get("days_until")) or 0) >= 0 else None,
        "earnings_days": _num(earnings.get("days_until")),
        "per": _num(earnings.get("per")), "fwd_pe": _num(earnings.get("forward_pe")),
        "roe_pct": roe * 100.0 if roe is not None else None,
        "target_upside": _num(earnings.get("target_upside_pct")),
    }


def _points(items: object, limit: int = 3) -> list[str]:
    """카드에 싣는 문장. ETF 에 붙는 '재무 점수 불필요' 같은 자리 채움 문구는 뺀다."""
    out = [clean(x) for x in (items or [])]   # type: ignore[union-attr]
    return [x for x in out if x and "ETF/ETN" not in x][:limit]


def _dedupe_label(text: str) -> str:
    """'섹터: 섹터: 강세…' 처럼 라벨이 두 번 붙은 불릿을 한 번으로."""
    return re.sub(r"^(\S+): \1: ", r"\1: ", text)


def _technical(stats: dict) -> list[dict]:
    """SPY·QQQ 의 추세 위치. 값이 하나도 없으면 빈 목록."""
    def gap(s: dict, key: str) -> float | None:
        return (s["last"] / s[key] - 1.0) * 100.0 if s.get("last") and s.get(key) else None

    spy, qqq = stats.get("SPY") or {}, stats.get("QQQ") or {}
    rows = []
    for key, label in (("ma20", "20일선 대비"), ("ma60", "60일선 대비"), ("ma120", "120일선 대비"),
                       ("high52", "52주 고가 대비")):
        rows.append({"label": label, "kind": "pct", "spy": gap(spy, key), "qqq": gap(qqq, key)})
    return [r for r in rows if r["spy"] is not None or r["qqq"] is not None]


def _scan_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items or []:
        signal = str(item.get("signal") or "")
        rows.append({
            "ticker": str(item.get("ticker") or ""),
            "name": clean(item.get("company_name") or item.get("company")),
            "score": _num(item.get("total_score") if "total_score" in item else item.get("score")),
            "grade": str(item.get("grade") or ""),
            "signal_label": SIGNAL_LABEL.get(signal, signal), "tone": SIGNAL_TONE.get(signal, "neu"),
            "action": clean((item.get("decision_v2") or {}).get("action")),
        })
    return rows


def _movers(items: list[dict], stats: dict, limit: int = 8) -> dict:
    """스캔 유니버스의 1일 상승·하락 상위. 시세가 붙은 종목이 모자라면 양쪽 다 비운다."""
    rows = []
    for item in items or []:
        s = stats.get(str(item.get("ticker") or "")) or {}
        if s.get("d1") is None:
            continue
        rows.append({
            "ticker": str(item.get("ticker")), "name": clean(item.get("company_name")),
            "last": s["last"], "d1": s["d1"], "d5": s.get("d5"), "spark": s.get("spark") or [],
            "score": _num(item.get("total_score")), "grade": str(item.get("grade") or ""),
        })
    if len(rows) < limit * 2:
        return {"up": [], "down": []}
    rows.sort(key=lambda r: -r["d1"])
    return {"up": rows[:limit], "down": rows[::-1][:limit]}


def _stance(glance: list[str]) -> dict | None:
    """'오늘 한눈에' 불릿에서 표지의 스탠스 칸을 읽는다. 해석 문구가 없으면 None."""
    def labelled(prefix: str) -> str:
        return next((g[len(prefix):].lstrip(": ").strip() for g in glance if g.startswith(prefix)), "")

    text = labelled("포트 점수 해석")
    if not text:
        return None
    label, _sep, note = text.partition(" · ")
    tone = ("pos" if any(k in label for k in ("양호", "안정", "우수", "긍정")) else
            "neg" if any(k in label for k in ("위험", "경고", "심각")) else
            "warn" if any(k in label for k in ("주의", "경계")) else "neu")
    return {"label": label, "note": note, "tone": tone, "todo": labelled("오늘 할 일"),
            "phase": next((g for g in glance if g.startswith("Phase")), "")}


def _calendar(lines: list[str]) -> list[dict]:
    rows, group = [], ""
    for line in lines:
        s = line.strip()
        if s.startswith("**"):
            group = "오늘" if "오늘" in s else "예정"
            continue
        m = _CAL.search(clean(s.lstrip("- ")))
        if not m:
            continue
        stars = _STARS.search(s)
        event = _STARS.sub("", m.group(3)).strip()
        rows.append({"group": group, "date": m.group(1), "time": m.group(2), "event": event,
                     "importance": len(stars.group(1)) if stars else 0})
    return rows


def _fear_greed(lines: list[str]) -> dict | None:
    """시장 리포트의 공포·탐욕 값. CNN 수집이 실패한 날은 VIX 구간으로 추정한 3단계 값이다(estimated)."""
    items = bullets(lines)
    for text in items:
        m = re.search(r"추정 공포·탐욕 지수\s*:\s*(\d{1,3})\s*-\s*(.+)$", text)
        if m and 0 <= int(m.group(1)) <= 100:
            return {"value": int(m.group(1)), "label": m.group(2).strip(), "estimated": True}
        m = re.search(r"CNN 공포·탐욕 지수\s*:\s*(\d{1,3}(?:\.\d+)?)", text)
        if m and 0 <= float(m.group(1)) <= 100:
            label = next((t.partition(":")[2].strip() for t in items if t.startswith("상태")), "")
            return {"value": round(float(m.group(1))), "label": label, "estimated": False}
    return None


def _news(lines: list[str], limit: int = 6) -> list[dict]:
    out = []
    for text in bullets(lines):
        if text.startswith("[") or "건" in text[-3:]:
            continue
        m = re.match(r"(.+?)\s*\(([^()]{2,40})\)\s*$", text)
        out.append({"title": m.group(1) if m else text, "source": m.group(2) if m else ""})
        if len(out) >= limit:
            break
    return out


def _subsections(lines: list[str]) -> list[dict]:
    out: list[dict] = []
    for line in lines:
        m = _HEADING.match(line)
        if m:
            out.append({"title": clean(m.group(2)), "points": []})
        elif out and line.lstrip().startswith("- "):
            out[-1]["points"].append(clean(line.lstrip()[2:]))
    return [s for s in out if s["points"]]


def build_model(*, report_date: str, data: dict, investment_md: str = "", market_md: str = "",
                prices: dict | None = None, portfolio_history: list[dict] | None = None,
                transactions: list[dict] | None = None) -> dict:
    """PDF 템플릿이 그대로 쓰는 모델. 순수 함수 — 네트워크·파일 접근 없음."""
    reused = sorted({str(f.attrs["cached"]) for f in (prices or {}).values() if getattr(f, "attrs", {}).get("cached")})
    reused_count = sum(1 for f in (prices or {}).values() if getattr(f, "attrs", {}).get("cached"))
    prices = clip_history(prices or {}, report_date)
    kinds = market_kinds()
    stats = {symbol: price_stats(frame, kinds.get(symbol, "price")) for symbol, frame in prices.items()}
    bench = stats.get(BENCHMARK) or {}
    holdings = [_holding(p, stats, bench) for p in data.get("portfolio") or []]
    holdings.sort(key=lambda h: -(h["weight"] or 0.0))
    # 수량을 모르는 보유 종목이 있으면 거래내역의 수량을 그대로 둔다 — 0주로 맞추면 다 판 것이 된다.
    shares = {h["ticker"]: h["shares"] for h in holdings if h["shares"] is not None}
    account = account_performance.daily_account(
        transactions, prices, holdings=shares if len(shares) == len(holdings) else None) if transactions else None
    asof = None
    if BENCHMARK in prices and len(prices[BENCHMARK]):
        last = prices[BENCHMARK].index[-1]
        asof = f"{last.month}/{last.day}"

    inv = md_sections(investment_md)
    mkt = md_sections(market_md)
    glance_lines = section(inv, "오늘 한눈에")
    conclusion = bullets(block_after(glance_lines, "오늘 결론"))
    # 신호 분포·오늘 할 일·환전·점수 해석·Phase 는 표지의 전용 칸(막대, 우선순위 표, 스탠스)이 보여준다.
    all_glance = leading_bullets(glance_lines)
    glance = [g for g in all_glance
              if not g.startswith(("신호 분포", "오늘 할 일", "환전", "포트 점수 해석", "Phase"))]
    priorities = md_table(block_after(glance_lines, "실행 우선순위"))
    analyst = _subsections(section(inv, "LLM 애널리스트 코멘트", deep=True))

    market = data.get("market") or {}
    groups = []
    for title, unit, rows in MARKET_GROUPS:
        found = [{"label": row[0], "symbol": row[1], **stats[row[1]]} for row in rows if stats.get(row[1])]
        if found:
            groups.append({"title": title, "unit": unit, "rows": found})
    if not groups:   # 가격 이력이 없으면 리포트가 이미 가진 1일 값만 보여준다.
        found = [{**dict.fromkeys(("d5", "m1", "m3", "ytd", "y1", "pos52")), "spark": [], "kind": "price",
                  "label": label, "symbol": key.upper(), "last": _num(market.get(f"{key}_price")),
                  "d1": _num(market.get(f"{key}_change"))}
                 for label, key in (("S&P 500 (SPY)", "spy"), ("나스닥 100 (QQQ)", "qqq"))
                 if _num(market.get(f"{key}_price")) is not None]
        if found:
            groups.append({"title": "대표 ETF", "unit": "달러, %", "rows": found})
    sectors = [{"label": name, "symbol": symbol, **stats[symbol]}
               for symbol, name in sector_etfs().items() if stats.get(symbol)]
    sectors.sort(key=lambda r: -(r.get("d1") or 0.0))

    value = sum(h["value_usd"] or 0.0 for h in holdings)
    cost = sum(h["cost_usd"] or 0.0 for h in holdings)
    weighted_d1 = sum((h["weight"] or 0.0) * (h["d1"] or 0.0) for h in holdings) / 100.0 if value else None
    scored = [h["score"] for h in holdings if h["score"] is not None]
    counts = {tone: sum(1 for h in holdings if h["tone"] == tone) for tone in ("pos", "neu", "warn", "neg")}
    raw_latest = next((r for r in reversed(portfolio_history or [])
                       if r.get("date") and _num(r.get("total_usd")) and str(r.get("date")) <= report_date), {})

    try:
        day = _date.fromisoformat(report_date)
        date_label = f"{day.year}.{day.month:02d}.{day.day:02d} ({WEEKDAYS[day.weekday()]})"
    except ValueError:
        date_label = report_date

    headline = (conclusion[0] if conclusion else
                (analyst[0]["points"][0] if analyst else "오늘의 포트폴리오·시장 점검"))
    spy, qqq = stats.get("SPY") or {}, stats.get("QQQ") or {}
    kpis = [
        {"label": "S&P 500 (SPY)", "value": spy.get("last") or _num(market.get("spy_price")), "unit": "$",
         "kind": "price", "change": spy.get("d1") if spy else _num(market.get("spy_change"))},
        {"label": "NASDAQ 100 (QQQ)", "value": qqq.get("last") or _num(market.get("qqq_price")), "unit": "$",
         "kind": "price", "change": qqq.get("d1") if qqq else _num(market.get("qqq_change"))},
    ]
    for label, symbol, unit in KPI_ROWS:
        if stats.get(symbol):
            kpis.append({"label": label, "value": stats[symbol]["last"], "unit": unit,
                         "kind": stats[symbol]["kind"], "change": stats[symbol]["d1"]})
    actions: dict[str, int] = {}
    for h in holdings:
        if h["action"]:
            actions[h["action"]] = actions.get(h["action"], 0) + 1

    nasdaq = data.get("nasdaq_100_scan") or {}
    kospi = data.get("kospi_top30_scan") or {}
    accumulation = []
    for pick in data.get("institutional_accumulation") or []:
        inst = pick.get("institutional") or {}
        signals = pick.get("signals") or {}
        accumulation.append({
            "ticker": str(pick.get("ticker") or ""), "name": clean(pick.get("company")),
            "score": _num(pick.get("accum_score")), "verdict": clean(pick.get("verdict")),
            "tone": "neg" if "분산" in str(pick.get("verdict")) else "pos" if "매집" in str(pick.get("verdict")) else "neu",
            "obv": _num(signals.get("obv_norm")), "cmf": _num(signals.get("cmf")),
            "updown": _num(signals.get("updown_ratio")),
            "inst_change": _num(inst.get("net_change")) * 100.0 if _num(inst.get("net_change")) is not None else None,
            "buyers": inst.get("buyers"), "sellers": inst.get("sellers"),
        })

    return {
        "date": report_date, "date_label": date_label,
        "generated_at": str(data.get("generated_at") or "")[:16].replace("T", " "),
        "headline": headline,
        "conclusion": conclusion[1:] if conclusion and headline == conclusion[0] else conclusion,
        "glance": glance, "priorities": priorities, "analyst": analyst,
        "stance": _stance(all_glance),
        "fx": bullets(block_after(glance_lines, "환전 타이밍")),
        "risk_flags": bullets(block_after(section(inv, "전체 요약"), "주요 위험 신호"))[:6],
        "kpis": kpis,
        "market": {
            "summary": [_dedupe_label(b) for b in bullets(section(mkt, "오늘 요약"))
                        if b.startswith(("시장", "섹터"))],
            "groups": groups, "sectors": sectors,
            "sector_scale": max([abs(s.get("d1") or 0.0) for s in sectors] + [0.5]),
            "movers": _movers((data.get("nasdaq_100_scan") or {}).get("all"), stats),
            "technical": _technical(stats),
            "fear_greed": _fear_greed(section(mkt, "공포·탐욕")),
            "news": _news(section(mkt, "주요 뉴스", deep=True)),
            "calendar": _calendar(section(mkt, "경제 일정")),
        },
        "portfolio": {
            "holdings": holdings, "value_usd": value or None,
            "pnl_pct": (value / cost - 1.0) * 100.0 if value and cost else None,
            "weighted_d1": weighted_d1,
            "avg_score": sum(scored) / len(scored) if scored else None,
            "signal_counts": counts, "account": account,
            "actions": sorted(actions.items(), key=lambda kv: -kv[1]),
            "performance": account_summary(account, prices),
            "return_scale": max([abs(h["ret_pct"] or 0.0) for h in holdings] + [1.0]),
            "total_krw": _num(raw_latest.get("total_krw")), "phase": raw_latest.get("phase"),
            "drawdown_pct": _num(raw_latest.get("drawdown_pct")),
        },
        "screens": {
            "nasdaq_buy": _scan_rows(nasdaq.get("top_buy")), "nasdaq_warn": _scan_rows(nasdaq.get("top_warning")),
            "kospi_buy": _scan_rows(kospi.get("top_buy")), "kospi_warn": _scan_rows(kospi.get("top_warning")),
            "nasdaq_scores": [s for s in (_num(i.get("total_score")) for i in nasdaq.get("all") or []) if s is not None],
        },
        "accumulation": accumulation,
        "prices": prices,
        # asof: 표·그림이 쓴 마지막 종가의 날짜. reused: 시세 조회가 막혀 캐시로 채운 심볼 수와 그 캐시를 받은 날짜.
        "price_basis": {"asof": asof, "reused": reused_count, "fetched": reused[0] if reused else None},
    }


def load_inputs(report_date: str, reports_dir: Path) -> tuple[dict, str, str]:
    """(investment-data JSON, 투자 리포트 md, 시장 리포트 md). JSON 이 없으면 FileNotFoundError."""
    data_path = reports_dir / f"investment-data-{report_date}.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    return (data, _read_text(reports_dir / f"investment-report-{report_date}.md"),
            _read_text(reports_dir / f"daily-report-{report_date}.md"))
