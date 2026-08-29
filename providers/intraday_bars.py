#!/usr/bin/env python3
"""intraday_bars.py — 단기(1분봉) 데이터층: 틱→분봉 집계 + JSONL 저장/로드 + 심볼 변환.

kis_stream(유일 writer)이 BarAggregator 로 틱을 1분 OHLCV 로 확정해
~/reports/ml-data/intraday_bars/{YYYY-MM-DD(UTC)}.jsonl 에 append 하고,
단기 모의 엔진(crons/intraday_mock_track)·대시보드가 load_bars 로 읽는다.

bar 레코드 (1줄 1bar, append-only — Ledger 관례):
  {ts(시장 로컬 ISO·bar 시작 분), epoch_min, symbol(base), market, o,h,l,c,
   v(당일 누적거래량 차분), n(틱수), session, v_partial(세션 첫 관측),
   v_anom(누적 역행), src}

주의:
  - KIS WS 틱의 volume 은 **당일 누적 거래량** → bar 볼륨은 차분. 누적 역행(글리치)은 0 클램프+v_anom.
  - 틱에 체결시각 필드가 없어 WS 수신시각 기준으로 분 경계를 나눈다(허용 오차 ~1초).
  - 미완성 분은 프로세스 재시작 시 소실 허용(다음 분부터 재개).
  - 모듈 임포트는 경량 유지(pandas 는 reader 내부 import) — kis_stream 이 상시 임포트.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ohlc_utils import normalize_ohlc_frame
from ml.strategy_studio.profiles import profile_health

logger = logging.getLogger(__name__)

BAR_DIR = Path(os.path.expanduser("~/reports/ml-data/intraday_bars"))
_TZ = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}
_SESSION_ALIASES = {
    "after_hours": "aftermarket",
    "afterhours": "aftermarket",
    "pre_market": "premarket",
}
_SESSION_FILTERS = {
    "regular": frozenset({"regular"}),
    "premarket": frozenset({"premarket"}),
    "aftermarket": frozenset({"aftermarket"}),
    "overnight": frozenset({"overnight"}),
    "extended": frozenset({"premarket", "regular", "aftermarket"}),
    "all": frozenset({"premarket", "regular", "aftermarket", "overnight"}),
}
_KR_AUCTION_WINDOWS = {
    "opening_auction": (8 * 60 + 30, 9 * 60),
    "closing_auction": (15 * 60 + 20, 15 * 60 + 30),
}


# ── 심볼 변환 단일 진실원 (bar store·state·ledger·실시간 캐시 = base 표기) ────

def base_symbol(ticker: str) -> str:
    """"005930.KS"→"005930" · "AAPL"→"AAPL" — 저장/캐시 키 표기."""
    t = (ticker or "").strip().upper()
    for suf in (".KS", ".KQ"):
        if t.endswith(suf):
            return t[: -len(suf)]
    return t


def market_of(ticker: str) -> str:
    """6자리 숫자 코드 → "KR", 그 외 "US" (kis_stream._classify 와 동일 규칙)."""
    base = (ticker or "").strip().upper().split(".")[0]
    return "KR" if base.isdigit() and len(base) == 6 else "US"


def to_yf(symbol: str, market: str | None = None) -> str:
    """yfinance 티커 표기 — KR 은 .KS 기본(.KQ 는 원 표기에 명시된 경우 보존), US 그대로."""
    t = (symbol or "").strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return t
    mk = market or market_of(t)
    return f"{t}.KS" if mk == "KR" else t


def _profile_for_market(market: str) -> str:
    return "kr_intraday" if str(market or "").strip().upper() == "KR" else "extended_us"


def session_for_timestamp(timestamp, *, market: str = "KR", profile: str | None = None) -> str:
    """시장 프로필과 현지 시각으로 bar 세션을 결정한다.

    KRX는 기존 KIS bar 동작과 호환되도록 regular로 유지하고, extended_us는
    미국 동부시간 기준으로 premarket/regular/aftermarket/overnight를 구분한다.
    """
    profile_key = str(profile or _profile_for_market(market)).strip().lower()
    market_key = "KR" if profile_key == "kr_intraday" else (
        "US" if profile_key in {"extended_us", "global_swing"} else str(market or "").strip().upper()
    )
    tz = _TZ.get(market_key, timezone.utc)
    if isinstance(timestamp, datetime):
        local = timestamp.astimezone(tz) if timestamp.tzinfo else timestamp.replace(tzinfo=tz)
    else:
        local = datetime.fromtimestamp(float(timestamp), tz=tz)
    if profile_key == "kr_intraday" or market_key == "KR":
        return "regular"
    minute = local.hour * 60 + local.minute
    if 4 * 60 <= minute < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= minute < 16 * 60:
        return "regular"
    if 16 * 60 <= minute < 20 * 60:
        return "aftermarket"
    return "overnight"


def _normalise_session(session: str) -> str:
    key = str(session or "").strip().lower()
    return _SESSION_ALIASES.get(key, key)


def _session_filter_values(session: str) -> frozenset[str]:
    key = _normalise_session(session)
    return _SESSION_FILTERS.get(key, frozenset({key}))


def _row_session(row: dict) -> str:
    stored = _normalise_session(row.get("session"))
    if stored:
        return stored
    try:
        timestamp = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
        market = str(row.get("market") or market_of(row.get("symbol", ""))).upper()
        return session_for_timestamp(timestamp, market=market, profile=_profile_for_market(market))
    except (KeyError, TypeError, ValueError):
        return "regular"


# ── 집계 (순수·클록 주입 — kis_stream 전용 writer) ────────────────────────────

class BarAggregator:
    """틱 스트림 → 1분 OHLCV 확정. 볼륨은 당일 누적 차분.

    on_tick 이 분 경계를 넘기면 이전 bar 를 확정 큐로 이동, roll(now) 이 시간 경과로도
    확정(틱 없는 분 대비)해 확정분을 회수한다. 상태는 메모리 한정(재시작 소실 허용).
    """

    def __init__(self):
        self._cur: dict[str, dict] = {}     # sym → 진행 중 bar
        self._prev_cum: dict[str, float] = {}   # sym → 직전 관측 누적거래량
        self._done: list[dict] = []
        self._allowed: set[str] | None = None   # 구독 심볼 화이트리스트 (None=전부 허용)

    def set_allowed(self, symbols) -> None:
        """구독 중인 심볼만 집계 — 멀티레코드 프레임 폭 어긋남으로 rec[0]에 가격/거래량이
        오는 파싱 글리치가 bar store(학습 데이터)를 오염시키는 것을 원천 차단.
        (6자리 숫자 거래량은 KR 심볼 패턴으로 위장하므로 패턴 검증으론 부족 — 라이브 실증.)"""
        self._allowed = set(symbols) if symbols is not None else None

    def on_tick(self, symbol: str, price: float, cum_volume, ts_epoch: float,
                market: str = "KR") -> None:
        if not symbol or not price or price <= 0:
            return
        if self._allowed is not None and symbol not in self._allowed:
            return
        minute = int(ts_epoch // 60)
        cur = self._cur.get(symbol)
        if cur is not None and cur["minute"] != minute:
            self._finalize(symbol)
            cur = None
        if cur is None:
            prev = self._prev_cum.get(symbol)
            cur = {"minute": minute, "market": market, "o": price, "h": price,
                   "l": price, "c": price, "n": 0,
                   # 세션 첫 관측 bar 는 이전 누적을 모름 → 첫 틱 누적을 기준(과소집계)·v_partial
                   "cum_open": prev if prev is not None else (float(cum_volume) if cum_volume else 0.0),
                   "cum_last": float(cum_volume) if cum_volume else 0.0,
                   "v_partial": prev is None}
            self._cur[symbol] = cur
        cur["h"] = max(cur["h"], price)
        cur["l"] = min(cur["l"], price)
        cur["c"] = price
        cur["n"] += 1
        if cum_volume is not None:
            try:
                cur["cum_last"] = max(cur["cum_last"], float(cum_volume))
                self._prev_cum[symbol] = cur["cum_last"]
            except (TypeError, ValueError):
                pass

    def _finalize(self, symbol: str) -> None:
        cur = self._cur.pop(symbol, None)
        if not cur:
            return
        delta = cur["cum_last"] - cur["cum_open"]
        v_anom = delta < 0
        tz = _TZ.get(cur["market"], timezone.utc)
        self._done.append({
            "ts": datetime.fromtimestamp(cur["minute"] * 60, tz=tz).isoformat(),
            "epoch_min": cur["minute"], "symbol": symbol, "market": cur["market"],
            "o": cur["o"], "h": cur["h"], "l": cur["l"], "c": cur["c"],
            "v": max(0.0, delta), "n": cur["n"],
            "session": session_for_timestamp(
                cur["minute"] * 60,
                market=cur["market"],
                profile=_profile_for_market(cur["market"]),
            ),
            "v_partial": bool(cur["v_partial"]), "v_anom": bool(v_anom), "src": "kis_ws",
        })

    def roll(self, now_epoch: float) -> list[dict]:
        """분 경계를 지난 진행분을 확정하고 확정 큐 전체를 회수."""
        now_min = int(now_epoch // 60)
        for sym in [s for s, c in self._cur.items() if c["minute"] < now_min]:
            self._finalize(sym)
        out, self._done = self._done, []
        return out


def bar_path(date_utc: str, base_dir: Path | str | None = None) -> Path:
    return Path(base_dir or BAR_DIR) / f"{date_utc}.jsonl"


def append_bars(bars: list[dict], base_dir: Path | str | None = None) -> int:
    """확정 bar append (단일 writer=kis_stream 전제 — 락 불필요). 반환: 기록 건수."""
    if not bars:
        return 0
    d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = bar_path(d, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for b in bars:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")
    return len(bars)


# ── reader (엔진·대시보드·학습) ───────────────────────────────────────────────

def _read_rows(date_utc: str, symbol: str | None = None,
               base_dir: Path | str | None = None) -> list[dict]:
    path = bar_path(date_utc, base_dir)
    if not path.exists():
        return []
    sym = base_symbol(symbol) if symbol else None
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if sym is None or r.get("symbol") == sym:
                    rows.append(r)
    except OSError as e:
        logger.debug("bar 파일 읽기 실패(%s): %s", path, e)
    return rows


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def available_dates(base_dir: Path | str | None = None) -> list[str]:
    """bar 파일이 존재하는 날짜(YYYY-MM-DD) 오름차순."""
    d = Path(base_dir or BAR_DIR)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("????-??-??.jsonl"))


def list_symbols(date_utc: str, market: str | None = None,
                 base_dir: Path | str | None = None) -> list[str]:
    """그날 bar 가 있는 심볼 목록 (대시보드 선택용)."""
    seen: list[str] = []
    for r in _read_rows(date_utc, None, base_dir):
        if market and r.get("market") != market:
            continue
        s = r.get("symbol")
        if s and s not in seen:
            seen.append(s)
    return seen


def load_bars(symbol: str, date_utc: str | None = None, *, interval: str = "1m",
              base_dir: Path | str | None = None, session: str | None = None):
    """자체 bar store → OHLCV DataFrame (tz-aware 인덱스·compute_intraday_features 호환).

    빈 결과는 빈 DataFrame (graceful). interval="5m" 은 1m 리샘플.
    """
    import pandas as pd
    rows = _read_rows(date_utc or today_utc(), symbol, base_dir)
    if session:
        requested_sessions = _session_filter_values(session)
        rows = [
            row for row in rows
            if _row_session(row) in requested_sessions
        ]
    if not rows:
        return pd.DataFrame()
    dedup: dict[int, dict] = {}
    for r in rows:
        try:
            dedup[int(r["epoch_min"])] = r
        except (KeyError, TypeError, ValueError):
            continue
    rows = [dedup[k] for k in sorted(dedup)]
    idx = pd.DatetimeIndex(pd.to_datetime([r["ts"] for r in rows]))
    df = pd.DataFrame({
        "Open": [r.get("o") for r in rows], "High": [r.get("h") for r in rows],
        "Low": [r.get("l") for r in rows], "Close": [r.get("c") for r in rows],
        "Volume": [r.get("v", 0.0) for r in rows],
    }, index=idx)
    if interval == "5m":
        df = (df.resample("5min")
                .agg({"Open": "first", "High": "max", "Low": "min",
                      "Close": "last", "Volume": "sum"})
                .dropna(subset=["Open"]))
    frame = normalize_ohlc_frame(df)
    source = str(rows[-1].get("src") or "kis_ws")
    requested_session = _normalise_session(session) if session else ""
    row_sessions = {_row_session(row) for row in rows}
    session_name = requested_session or (next(iter(row_sessions)) if len(row_sessions) == 1 else "all")
    quality = "incomplete" if any(row.get("v_partial") or row.get("v_anom") for row in rows) else "complete"
    frame.attrs.update({
        "profile": "kr_intraday" if market_of(symbol) == "KR" else "extended_us",
        "session": session_name,
        "source_health": _source_health(rows),
    })
    frame.attrs["data_snapshot"] = build_data_snapshot(
        frame,
        symbol=base_symbol(symbol),
        source=source,
        timeframe=interval,
        session=session_name,
        quality=quality,
        raw_ref=str(bar_path(date_utc or today_utc(), base_dir)),
    )
    try:
        last_bar_at = frame.index[-1].isoformat()
        limit = 60 if interval == "1m" else 300
        frame.attrs["profile_health"] = profile_health(
            str(frame.attrs["profile"]),
            last_bar_at=last_bar_at,
            now=datetime.now(timezone.utc).isoformat(),
            max_age_seconds=limit,
        ).to_dict()
    except (TypeError, ValueError):
        frame.attrs["profile_health"] = {
            "status": "pause",
            "reason": "invalid_intraday_bar_timestamp",
            "age_seconds": None,
        }
    return frame


def _source_health(rows: list[dict]) -> dict[str, object]:
    """Summarize the existing append-only rows without writing another store."""

    sources = sorted({str(row.get("src") or "unknown") for row in rows})
    quality = "incomplete" if any(row.get("v_partial") or row.get("v_anom") for row in rows) else "complete"
    return {
        "status": "degraded" if quality != "complete" else "available",
        "sources": sources,
        "bar_count": len(rows),
        "quality": quality,
        "last_bar_at": str(rows[-1].get("ts") or "") if rows else None,
    }


def build_data_snapshot(
    frame,
    *,
    symbol: str,
    source: str = "kis_ws",
    timeframe: str = "1m",
    session: str = "regular",
    quality: str = "complete",
    raw_ref: str | None = None,
):
    """Create the shared provenance DTO from an already-loaded bar frame."""

    from ml.data_pipeline import normalize_data_snapshot

    return normalize_data_snapshot(
        frame,
        symbol=symbol,
        source=source,
        timeframe=timeframe,
        session=session,
        adjustment="raw",
        raw_ref=raw_ref,
        quality=quality,
    )


def _slice_latest_session(
    df,
    *,
    date_utc: str | None = None,
    market: str | None = None,
    session: str | None = None,
):
    """여러 세션이 섞인 yfinance 분봉은 한 세션만 남겨 차트를 읽기 쉽게 만든다."""
    import pandas as pd

    if df is None or getattr(df, "empty", True):
        return df
    if session:
        df = _filter_session_frame(df, market=market, session=session)
        if df is None or getattr(df, "empty", True):
            return df
    try:
        idx = pd.DatetimeIndex(df.index)
    except Exception:
        return df
    if len(idx) < 2:
        return df
    tz = _TZ.get((market or "").upper())
    if idx.tz is not None:
        session_idx = idx.tz_convert(tz or "UTC").normalize()
    else:
        session_idx = idx.normalize()
    session_dates = pd.Index(session_idx).unique().sort_values()
    if len(session_dates) == 0:
        return df
    target = session_dates[-1]
    if date_utc:
        try:
            want = str(date_utc)[:10]
            as_str = [str(ts)[:10] for ts in session_dates]
            if want in as_str:
                target = session_dates[as_str.index(want)]
        except Exception:
            pass
    mask = session_idx == target
    sliced = df.loc[mask]
    if sliced is not None and not getattr(sliced, "empty", True):
        return sliced
    return df


def _filter_session_frame(df, *, market: str | None, session: str):
    """Keep regular/extended observations separate without filling gaps."""

    import pandas as pd

    idx = pd.DatetimeIndex(df.index)
    market_key = (market or "").upper()
    key = _normalise_session(session)
    if market_key == "KR":
        local = idx.tz_convert(_TZ[market_key]) if idx.tz is not None else idx
        minutes = local.hour * 60 + local.minute
        if key == "regular":
            start, end = 9 * 60, 15 * 60 + 40
        elif key in _KR_AUCTION_WINDOWS:
            start, end = _KR_AUCTION_WINDOWS[key]
        else:
            return df.iloc[0:0]
        return df.loc[(minutes >= start) & (minutes < end)]
    requested_sessions = _session_filter_values(key)
    local = idx.tz_convert(_TZ["US"]) if idx.tz is not None else idx.tz_localize(_TZ["US"])
    observed = [
        session_for_timestamp(value, market="US", profile="extended_us")
        for value in local
    ]
    return df.loc[[value in requested_sessions for value in observed]]


def load_bars_with_fallback(symbol: str, market: str | None = None,
                            date_utc: str | None = None, *, interval: str = "1m",
                            session: str | None = None):
    """bar store 우선, 없으면 yfinance 폴백 (대시보드·백필 전용 — 엔진 핫패스 금지).

    반환 (DataFrame, src) — src ∈ {"store", "yfinance", "none"}.
    """
    df = load_bars(symbol, date_utc, interval=interval, session=session)
    if df is not None and not getattr(df, "empty", True):
        return df, "store"
    mk = market or market_of(symbol)
    try:
        from ml.intraday_signal import fetch_intraday
        cands = [to_yf(symbol, mk)]
        if mk == "KR" and cands[0].endswith(".KS"):
            cands.append(cands[0][:-3] + ".KQ")   # 스캐너 코드는 시장 미상 — 코스닥 재시도
        fetch_days = {"1m": 7, "5m": 60, "15m": 60, "1h": 730}.get(interval, 7)
        for yf_t in cands:
            df = fetch_intraday(yf_t, interval=interval, days=fetch_days)
            if df is not None and not getattr(df, "empty", True):
                df = _slice_latest_session(df, date_utc=date_utc, market=mk, session=session)
                return normalize_ohlc_frame(df), "yfinance"
    except Exception as e:
        logger.debug("yfinance 폴백 실패(%s): %s", symbol, e)
    import pandas as pd
    return pd.DataFrame(), "none"


# ── 분대별 거래량 프로파일 (volspike 시간대 정규화 원천) ──────────────────────

def build_minute_profile(symbol: str, dates: list[str] | None = None, *,
                         base_dir: Path | str | None = None,
                         max_sessions: int = 20) -> dict:
    """최근 ≤max_sessions 세션의 같은 분대(HH:MM 시장 로컬) 거래량 mean/std.

    반환 {"HH:MM": {"mean": v, "std": v, "n": k}} — v_partial 제외. 표본 없으면 {}.
    """
    ds = dates if dates is not None else available_dates(base_dir)[-(max_sessions + 1):]
    sym = base_symbol(symbol)
    buckets: dict[str, list[float]] = {}
    for d in ds[-max_sessions:]:
        for r in _read_rows(d, sym, base_dir):
            if r.get("v_partial") or r.get("v_anom"):
                continue
            hhmm = str(r.get("ts", ""))[11:16]
            if len(hhmm) == 5:
                buckets.setdefault(hhmm, []).append(float(r.get("v", 0.0)))
    out = {}
    for hhmm, vs in buckets.items():
        n = len(vs)
        mean = sum(vs) / n
        var = sum((v - mean) ** 2 for v in vs) / n
        out[hhmm] = {"mean": mean, "std": math.sqrt(var), "n": n}
    return out
